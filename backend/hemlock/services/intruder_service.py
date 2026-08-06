"""Intruder 服务：并发爆破/fuzz。"""

import asyncio
import itertools
import uuid
from typing import Any

from sqlalchemy.orm import Session

from hemlock.models.flow import Flow
from hemlock.services.request_service import send_raw_request
from hemlock.services.event_bus import event_bus
from hemlock.services.flowtag_engine import enrich_flow_fields


def _expand_payloads(positions: list, attack_type: str = "sniper") -> list[dict[str, str]]:
    """展开多标记位的 payload 组合。

    - sniper: 单个 payload 集，依次替换每个标记位（每次只替换一个）
    - battering_ram: 单个 payload 集，所有标记位同时替换为同一值
    - pitchfork: 多个 payload 集，按索引一一配对
    - cluster_bomb: 多个 payload 集，测试所有组合（笛卡尔积）
    """
    if not positions:
        return []

    # 过滤掉没有 payload 的位置
    valid = [p for p in positions if p.payloads]
    if not valid:
        return []

    if len(valid) == 1:
        pos = valid[0]
        return [{pos.placeholder: p} for p in pos.payloads]

    if attack_type == "sniper":
        # 单 payload 集：取第一个位置的 payload，依次替换每个标记位
        pos0 = valid[0]
        combos = []
        for pos in valid:
            for payload in pos0.payloads:
                combos.append({pos.placeholder: payload})
        return combos

    if attack_type == "battering_ram":
        # 所有位置用同一组 payload，同步替换
        pos0 = valid[0]
        return [
            {p.placeholder: payload for p in valid}
            for payload in pos0.payloads
        ]

    if attack_type == "pitchfork":
        # 按索引对齐，长度取最短
        max_len = min(len(p.payloads) for p in valid)
        combos = []
        for i in range(max_len):
            combo = {}
            for p in valid:
                combo[p.placeholder] = p.payloads[i]
            combos.append(combo)
        return combos

    # 默认 cluster_bomb：笛卡尔积
    combos = []
    for combo_vals in itertools.product(*[p.payloads for p in valid]):
        combo = {}
        for p, val in zip(valid, combo_vals):
            combo[p.placeholder] = val
        combos.append(combo)
    return combos


def _format_payload_str(combo: dict[str, str]) -> str:
    """格式化 payload 显示字符串。

    单标记位时只显示值（如 "1"），多标记位时显示 k=v 格式（如 "a=1 | b=2"）。
    """
    if len(combo) == 1:
        return list(combo.values())[0]
    return " | ".join(f"{k}={v}" for k, v in combo.items())


async def run_intruder(
    db: Session,
    method: str,
    url: str,
    headers: str,
    body: str,
    positions: list,
    concurrency: int,
    project_id: str | None,
    attack_type: str = "sniper",
    task_id: str | None = None,
) -> dict[str, Any]:
    """执行爆破任务，逐个推送结果到 WebSocket。

    通过 event_bus 发布 intruder_progress 事件，前端可实时接收每个结果。
    最终返回完整结果列表。
    """
    if task_id is None:
        task_id = f"int_{uuid.uuid4().hex[:16]}"
    combinations = _expand_payloads(positions, attack_type)
    total = len(combinations)
    results: list[dict] = []
    completed = 0

    semaphore = asyncio.Semaphore(concurrency)

    async def attack(combo: dict[str, str]):
        nonlocal completed
        async with semaphore:
            payload_str = _format_payload_str(combo)
            req_headers = headers
            req_body = body
            req_url = url
            for placeholder, value in combo.items():
                req_url = req_url.replace(f"§{placeholder}§", value)
                req_headers = req_headers.replace(f"§{placeholder}§", value)
                req_body = req_body.replace(f"§{placeholder}§", value)

            try:
                resp = await send_raw_request(method, req_url, req_headers, req_body)
                resp_headers = resp.get("headers", "")
                resp_body = resp.get("body", "")
                actual_req_headers = resp.get("req_headers", req_headers)
                meta = enrich_flow_fields(
                    url=req_url,
                    req_headers=actual_req_headers,
                    req_body=req_body,
                    resp_headers=resp_headers,
                    resp_body=resp_body,
                    resp_status=resp.get("status"),
                )
                flow = Flow(
                    project_id=project_id,
                    method=method.upper(),
                    url=req_url,
                    req_headers=actual_req_headers,
                    req_body=req_body,
                    resp_status=resp.get("status"),
                    resp_headers=resp_headers,
                    resp_body=resp_body,
                    source="intruder",
                    duration_ms=resp.get("duration_ms"),
                    **meta,
                )
                db.add(flow)
                db.commit()
                db.refresh(flow)

                result_item = {
                    "payload": payload_str,
                    "status": resp.get("status"),
                    "length": len(resp.get("body", "")),
                    "req_length": len((actual_req_headers or "") + (req_body or "")),
                    "flow_id": flow.id,
                    "duration_ms": resp.get("duration_ms"),
                    "mime_type": meta.get("mime_type", ""),
                    "host": meta.get("host", ""),
                    "tags": meta.get("tags", ""),
                    "tag_colors": meta.get("tag_colors", ""),
                }
                results.append(result_item)
                completed += 1

                # 逐个推送结果到前端
                event_bus.publish("intruder", {
                    "event": "intruder_progress",
                    "task_id": task_id,
                    "result": result_item,
                    "completed": completed,
                    "total": total,
                })
            except Exception as e:
                result_item = {
                    "payload": payload_str,
                    "status": None,
                    "length": 0,
                    "flow_id": None,
                    "error": str(e),
                }
                results.append(result_item)
                completed += 1

                event_bus.publish("intruder", {
                    "event": "intruder_progress",
                    "task_id": task_id,
                    "result": result_item,
                    "completed": completed,
                    "total": total,
                })

    await asyncio.gather(*[attack(c) for c in combinations])

    # 推送完成事件
    event_bus.publish("intruder", {
        "event": "intruder_complete",
        "task_id": task_id,
        "total": len(results),
    })

    return {
        "task_id": task_id,
        "total": len(results),
        "results": results,
    }
