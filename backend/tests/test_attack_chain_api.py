"""攻击链画布 API 测试（第二期前端画布的后端支撑端点）。

覆盖：列表（项目隔离 + 节点计数）/ 详情（节点排序 + JSON 解析）/
存坐标 / 加删边（幂等 + 校验）/ 状态确认。
"""

import json

import pytest
from fastapi.testclient import TestClient

from hemlock.models.attack_chain import AttackChain, AttackNode


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """创建测试客户端，使用临时数据库（与 test_flows_api 同款 fixture）。"""
    monkeypatch.setenv("HEMLOCK_DB_PATH", str(tmp_path / "test.db"))

    from hemlock.core import config, database
    import importlib
    importlib.reload(config)
    monkeypatch.setattr("hemlock.core.config.settings.db_path", str(tmp_path / "test.db"))

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    database.engine = engine
    database.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    database.Base.metadata.create_all(bind=engine)

    from hemlock.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def chain_with_nodes(client):
    """预置一条链：3 个节点（source_step 2/0/1 打乱顺序验证排序）+ 2 条边。"""
    from hemlock.core.database import SessionLocal
    from hemlock.models.project import Project

    db = SessionLocal()
    project = Project(name="链测试项目")
    db.add(project)
    db.commit()
    db.refresh(project)
    project_id = project.id

    chain = AttackChain(project_id=project_id, task_id="task_x", name="测试链", status="draft")
    db.add(chain)
    db.commit()
    db.refresh(chain)

    nodes = {}
    for idx, (ntype, step) in enumerate([("asset", 2), ("vuln", 0), ("credential", 1)]):
        node = AttackNode(
            chain_id=chain.id,
            node_type=ntype,
            title=f"节点{idx}",
            detail=json.dumps({"url": f"https://x/{idx}"}, ensure_ascii=False),
            source_step=step,
        )
        db.add(node)
        nodes[step] = node
    db.commit()
    for n in nodes.values():
        db.refresh(n)

    chain.edges = json.dumps(
        [
            {"from": nodes[0].id, "to": nodes[1].id, "label": "发现"},
            {"from": nodes[1].id, "to": nodes[2].id, "label": "利用"},
        ],
        ensure_ascii=False,
    )
    db.commit()
    ids = {
        "project_id": project_id,
        "chain_id": chain.id,
        "node_ids": {step: n.id for step, n in nodes.items()},
    }
    db.close()
    return ids


def test_list_empty(client):
    resp = client.get("/api/attack-chain")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_project_isolation_and_count(client, chain_with_nodes):
    ids = chain_with_nodes
    # 按项目过滤命中
    resp = client.get("/api/attack-chain", params={"project_id": ids["project_id"]})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == ids["chain_id"]
    assert data[0]["node_count"] == 3
    assert data[0]["status"] == "draft"
    # 其他项目查不到
    resp = client.get("/api/attack-chain", params={"project_id": "other_project"})
    assert resp.json() == []


def test_detail_nodes_ordered_by_source_step(client, chain_with_nodes):
    ids = chain_with_nodes
    resp = client.get(f"/api/attack-chain/{ids['chain_id']}")
    assert resp.status_code == 200
    data = resp.json()
    # source_step 乱序插入，详情按 0/1/2 排序返回
    assert [n["source_step"] for n in data["nodes"]] == [0, 1, 2]
    assert data["nodes"][0]["detail"] == {"url": "https://x/1"}
    assert len(data["edges"]) == 2
    assert data["edges"][0]["label"] == "发现"


def test_detail_404(client):
    resp = client.get("/api/attack-chain/chn_missing")
    assert resp.status_code == 404


def test_save_node_position(client, chain_with_nodes):
    ids = chain_with_nodes
    node_id = ids["node_ids"][0]
    resp = client.put(
        f"/api/attack-chain/{ids['chain_id']}/nodes/{node_id}/position",
        json={"x": 123.5, "y": -45.0},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # 落库验证
    detail = client.get(f"/api/attack-chain/{ids['chain_id']}").json()
    node = next(n for n in detail["nodes"] if n["id"] == node_id)
    assert node["x"] == pytest.approx(123.5)
    assert node["y"] == pytest.approx(-45.0)


def test_save_position_wrong_chain_404(client, chain_with_nodes):
    ids = chain_with_nodes
    node_id = ids["node_ids"][0]
    resp = client.put(
        f"/api/attack-chain/chn_other/nodes/{node_id}/position",
        json={"x": 1, "y": 1},
    )
    assert resp.status_code == 404


def test_add_edge_and_duplicate(client, chain_with_nodes):
    ids = chain_with_nodes
    a, c = ids["node_ids"][0], ids["node_ids"][2]
    # 首次加边（0→2 原本没有）
    resp = client.post(
        f"/api/attack-chain/{ids['chain_id']}/edges",
        json={"source": a, "target": c, "label": "手工补"},
    )
    assert resp.status_code == 200
    assert resp.json()["duplicate"] is False

    # 同向重复：幂等返回 duplicate=True，不新增
    resp = client.post(
        f"/api/attack-chain/{ids['chain_id']}/edges",
        json={"source": a, "target": c, "label": "手工补"},
    )
    assert resp.json()["duplicate"] is True
    detail = client.get(f"/api/attack-chain/{ids['chain_id']}").json()
    assert len(detail["edges"]) == 3


def test_add_edge_rejects_self_loop_and_foreign_node(client, chain_with_nodes):
    ids = chain_with_nodes
    a = ids["node_ids"][0]
    # 自环
    resp = client.post(
        f"/api/attack-chain/{ids['chain_id']}/edges", json={"source": a, "target": a}
    )
    assert resp.status_code == 400
    # 不属于这条链的节点
    resp = client.post(
        f"/api/attack-chain/{ids['chain_id']}/edges",
        json={"source": a, "target": "and_ghost"},
    )
    assert resp.status_code == 404


def test_delete_edge(client, chain_with_nodes):
    ids = chain_with_nodes
    a, b = ids["node_ids"][0], ids["node_ids"][1]
    # 旧版 TestClient 的 delete() 不带 json 参数，走通用 request
    resp = client.request(
        "DELETE",
        f"/api/attack-chain/{ids['chain_id']}/edges",
        json={"source": a, "target": b},
    )
    assert resp.json()["deleted"] is True
    detail = client.get(f"/api/attack-chain/{ids['chain_id']}").json()
    assert len(detail["edges"]) == 1
    # 再删一次：不存在 → deleted=False
    resp = client.request(
        "DELETE",
        f"/api/attack-chain/{ids['chain_id']}/edges",
        json={"source": a, "target": b},
    )
    assert resp.json()["deleted"] is False


def test_update_status(client, chain_with_nodes):
    ids = chain_with_nodes
    resp = client.put(
        f"/api/attack-chain/{ids['chain_id']}/status", json={"status": "confirmed"}
    )
    assert resp.json()["status"] == "confirmed"
    detail = client.get(f"/api/attack-chain/{ids['chain_id']}").json()
    assert detail["status"] == "confirmed"
    # 非法状态
    resp = client.put(
        f"/api/attack-chain/{ids['chain_id']}/status", json={"status": "ok"}
    )
    assert resp.status_code == 400
