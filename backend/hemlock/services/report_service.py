"""报告生成服务：生成渗透测试报告，包含漏洞详情和证据链。

支持格式：
- HTML：可直接在浏览器中查看的完整报告
- Markdown：便于版本管理和编辑
- JSON：结构化数据，便于其他工具消费
"""

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from hemlock.models.finding import Finding
from hemlock.models.evidence import Evidence
from hemlock.models.task import Task
from hemlock.models.flow import Flow
from hemlock.core.config import settings


# 严重程度排序
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_SEVERITY_LABEL = {
    "critical": "严重",
    "high": "高危",
    "medium": "中危",
    "low": "低危",
    "info": "信息",
}
_SEVERITY_COLOR = {
    "critical": "#dc2626",
    "high": "#ea580c",
    "medium": "#ca8a04",
    "low": "#2563eb",
    "info": "#6b7280",
}


def generate_report(
    db: Session,
    project_id: str | None = None,
    task_id: str | None = None,
    fmt: str = "html",
) -> str:
    """生成渗透测试报告。

    Args:
        db: 数据库会话
        project_id: 项目 ID（可选）
        task_id: 任务 ID（可选）
        fmt: 报告格式 html/markdown/json

    Returns:
        报告字符串
    """
    # 查询漏洞
    query = db.query(Finding)
    if project_id:
        query = query.filter(Finding.project_id == project_id)
    if task_id:
        query = query.filter(Finding.task_id == task_id)
    findings = query.order_by(Finding.created_at.desc()).all()

    # 查询任务信息
    tasks = []
    if task_id:
        task = db.query(Task).filter(Task.id == task_id).first()
        if task:
            tasks = [task]
    elif project_id:
        tasks = db.query(Task).filter(Task.project_id == project_id).all()

    # 收集证据
    finding_details = []
    for f in findings:
        evidence = None
        if f.evidence_id:
            evidence = db.query(Evidence).filter(Evidence.id == f.evidence_id).first()
        finding_details.append({
            "finding": f,
            "evidence": evidence,
        })

    # 统计
    stats = _calculate_stats(findings)

    if fmt == "json":
        return _generate_json(finding_details, stats, tasks)
    elif fmt == "markdown":
        return _generate_markdown(finding_details, stats, tasks)
    else:
        return _generate_html(finding_details, stats, tasks)


def _calculate_stats(findings: list[Finding]) -> dict:
    """计算漏洞统计。"""
    stats = {
        "total": len(findings),
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "info": 0,
        "confirmed": 0,
        "pending": 0,
        "false_positive": 0,
        "closed": 0,
    }
    for f in findings:
        severity = f.severity if f.severity in stats else "info"
        stats[severity] += 1
        if f.status in stats:
            stats[f.status] += 1
    return stats


def _generate_json(
    finding_details: list[dict],
    stats: dict,
    tasks: list[Task],
) -> str:
    """生成 JSON 格式报告。"""
    report = {
        "title": "Hemlock 渗透测试报告",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "tasks": [
            {
                "id": t.id,
                "goal": t.goal,
                "status": t.status,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "ended_at": t.ended_at.isoformat() if t.ended_at else None,
            }
            for t in tasks
        ],
        "findings": [
            {
                "id": fd["finding"].id,
                "vuln_type": fd["finding"].vuln_type,
                "severity": fd["finding"].severity,
                "status": fd["finding"].status,
                "title": fd["finding"].title,
                "detail": fd["finding"].detail,
                "created_at": fd["finding"].created_at.isoformat() if fd["finding"].created_at else None,
                "evidence": {
                    "request": fd["evidence"].request if fd["evidence"] else "",
                    "response": fd["evidence"].response if fd["evidence"] else "",
                    "indicator": fd["evidence"].indicator if fd["evidence"] else "",
                    "ai_reasoning": fd["evidence"].ai_reasoning if fd["evidence"] else "",
                } if fd["evidence"] else None,
            }
            for fd in finding_details
        ],
    }
    return json.dumps(report, ensure_ascii=False, indent=2)


def _generate_markdown(
    finding_details: list[dict],
    stats: dict,
    tasks: list[Task],
) -> str:
    """生成 Markdown 格式报告。"""
    lines = [
        "# Hemlock 渗透测试报告",
        "",
        f"**生成时间**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        "## 漏洞统计",
        "",
        f"| 严重程度 | 数量 |",
        f"|---------|------|",
        f"| 严重 | {stats['critical']} |",
        f"| 高危 | {stats['high']} |",
        f"| 中危 | {stats['medium']} |",
        f"| 低危 | {stats['low']} |",
        f"| 信息 | {stats['info']} |",
        f"| **总计** | **{stats['total']}** |",
        "",
        f"- 已确认: {stats['confirmed']}",
        f"- 待确认: {stats['pending']}",
        f"- 误报: {stats['false_positive']}",
        f"- 已关闭: {stats['closed']}",
        "",
    ]

    if tasks:
        lines.append("## 测试任务")
        lines.append("")
        for t in tasks:
            lines.append(f"### {t.goal}")
            lines.append(f"- 任务 ID: `{t.id}`")
            lines.append(f"- 状态: {t.status}")
            if t.started_at:
                lines.append(f"- 开始时间: {t.started_at.strftime('%Y-%m-%d %H:%M:%S')}")
            if t.ended_at:
                lines.append(f"- 结束时间: {t.ended_at.strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append("")

    # 按严重程度排序
    sorted_details = sorted(
        finding_details,
        key=lambda x: _SEVERITY_ORDER.get(x["finding"].severity, 99),
    )

    lines.append("## 漏洞详情")
    lines.append("")

    for idx, fd in enumerate(sorted_details, 1):
        f = fd["finding"]
        ev = fd["evidence"]
        severity_label = _SEVERITY_LABEL.get(f.severity, f.severity)

        lines.append(f"### {idx}. [{severity_label}] {f.title}")
        lines.append("")
        lines.append(f"- **漏洞 ID**: `{f.id}`")
        lines.append(f"- **类型**: {f.vuln_type}")
        lines.append(f"- **严重程度**: {severity_label}")
        lines.append(f"- **状态**: {f.status}")
        lines.append(f"- **发现时间**: {f.created_at.strftime('%Y-%m-%d %H:%M:%S') if f.created_at else 'N/A'}")
        lines.append("")

        if f.detail:
            lines.append(f"**漏洞详情**:")
            lines.append(f"```")
            lines.append(f.detail)
            lines.append(f"```")
            lines.append("")

        if ev:
            lines.append(f"**判定指标**:")
            lines.append(f"> {ev.indicator}")
            lines.append("")

            lines.append(f"**原始请求**:")
            lines.append(f"```http")
            lines.append(ev.request)
            lines.append(f"```")
            lines.append("")

            lines.append(f"**原始响应**:")
            lines.append(f"```http")
            lines.append(ev.response[:3000])
            lines.append(f"```")
            lines.append("")

            if ev.ai_reasoning:
                lines.append(f"**AI 分析**:")
                lines.append(f"> {ev.ai_reasoning}")
                lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _generate_html(
    finding_details: list[dict],
    stats: dict,
    tasks: list[Task],
) -> str:
    """生成 HTML 格式报告。"""
    sorted_details = sorted(
        finding_details,
        key=lambda x: _SEVERITY_ORDER.get(x["finding"].severity, 99),
    )

    # 漏洞卡片
    finding_cards = []
    for idx, fd in enumerate(sorted_details, 1):
        f = fd["finding"]
        ev = fd["evidence"]
        color = _SEVERITY_COLOR.get(f.severity, "#6b7280")
        label = _SEVERITY_LABEL.get(f.severity, f.severity)

        evidence_html = ""
        if ev:
            evidence_html = f"""
            <div class="evidence">
                <h4>判定指标</h4>
                <div class="indicator">{_escape_html(ev.indicator)}</div>
                <h4>原始请求</h4>
                <pre class="code-block"><code>{_escape_html(ev.request)}</code></pre>
                <h4>原始响应</h4>
                <pre class="code-block"><code>{_escape_html(ev.response[:3000])}</code></pre>
                {"<h4>AI 分析</h4><div class='ai-reasoning'>" + _escape_html(ev.ai_reasoning) + "</div>" if ev.ai_reasoning else ""}
            </div>"""

        finding_cards.append(f"""
        <div class="finding-card" style="border-left-color: {color};">
            <div class="finding-header">
                <span class="severity-badge" style="background-color: {color};">{label}</span>
                <h3>{idx}. {_escape_html(f.title)}</h3>
            </div>
            <div class="finding-meta">
                <span>类型: <strong>{_escape_html(f.vuln_type)}</strong></span>
                <span>状态: <strong>{_escape_html(f.status)}</strong></span>
                <span>发现时间: {f.created_at.strftime('%Y-%m-%d %H:%M:%S') if f.created_at else 'N/A'}</span>
            </div>
            {f"<div class='finding-detail'>{_escape_html(f.detail)}</div>" if f.detail else ""}
            {evidence_html}
        </div>""")

    # 任务列表
    tasks_html = ""
    if tasks:
        task_items = []
        for t in tasks:
            task_items.append(f"""
            <tr>
                <td>{_escape_html(t.goal)}</td>
                <td><code>{t.id}</code></td>
                <td>{_escape_html(t.status)}</td>
                <td>{t.started_at.strftime('%Y-%m-%d %H:%M') if t.started_at else 'N/A'}</td>
                <td>{t.ended_at.strftime('%Y-%m-%d %H:%M') if t.ended_at else 'N/A'}</td>
            </tr>""")
        tasks_html = f"""
        <section class="tasks">
            <h2>测试任务</h2>
            <table>
                <thead><tr><th>目标</th><th>任务 ID</th><th>状态</th><th>开始</th><th>结束</th></tr></thead>
                <tbody>{''.join(task_items)}</tbody>
            </table>
        </section>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hemlock 渗透测试报告</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f3f4f6; color: #1f2937; line-height: 1.6; padding: 20px;
        }}
        .container {{ max-width: 960px; margin: 0 auto; }}
        header {{
            background: linear-gradient(135deg, #1e3a5f 0%, #2d5a87 100%);
            color: white; padding: 30px; border-radius: 12px; margin-bottom: 24px;
        }}
        header h1 {{ font-size: 28px; margin-bottom: 8px; }}
        header .meta {{ opacity: 0.8; font-size: 14px; }}
        .stats-grid {{
            display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 24px;
        }}
        .stat-card {{
            background: white; padding: 20px; border-radius: 8px; text-align: center;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .stat-card .num {{ font-size: 32px; font-weight: 700; }}
        .stat-card .label {{ font-size: 12px; color: #6b7280; margin-top: 4px; }}
        .stat-card.critical .num {{ color: #dc2626; }}
        .stat-card.high .num {{ color: #ea580c; }}
        .stat-card.medium .num {{ color: #ca8a04; }}
        .stat-card.low .num {{ color: #2563eb; }}
        .stat-card.info .num {{ color: #6b7280; }}
        .stat-card.total .num {{ color: #1f2937; }}
        section {{ margin-bottom: 24px; }}
        section h2 {{ font-size: 20px; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 2px solid #e5e7eb; }}
        table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        th, td {{ padding: 12px 16px; text-align: left; border-bottom: 1px solid #e5e7eb; font-size: 14px; }}
        th {{ background: #f9fafb; font-weight: 600; }}
        .finding-card {{
            background: white; border-radius: 8px; padding: 20px; margin-bottom: 16px;
            border-left: 4px solid #6b7280; box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .finding-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }}
        .finding-header h3 {{ font-size: 18px; }}
        .severity-badge {{
            color: white; padding: 4px 12px; border-radius: 4px; font-size: 12px; font-weight: 600;
        }}
        .finding-meta {{ display: flex; gap: 20px; font-size: 13px; color: #6b7280; margin-bottom: 12px; }}
        .finding-detail {{ background: #f9fafb; padding: 12px; border-radius: 6px; margin-bottom: 12px; font-size: 14px; }}
        .evidence {{ margin-top: 16px; }}
        .evidence h4 {{ font-size: 14px; color: #374151; margin-bottom: 8px; }}
        .indicator {{ background: #fef3c7; padding: 10px; border-radius: 6px; margin-bottom: 12px; font-size: 13px; }}
        .ai-reasoning {{ background: #dbeafe; padding: 10px; border-radius: 6px; margin-bottom: 12px; font-size: 13px; }}
        .code-block {{
            background: #1e293b; color: #e2e8f0; padding: 14px; border-radius: 6px;
            overflow-x: auto; font-family: 'Fira Code', Consolas, monospace; font-size: 12px;
            max-height: 400px; overflow-y: auto; white-space: pre-wrap; word-break: break-all;
        }}
        footer {{ text-align: center; color: #9ca3af; font-size: 12px; margin-top: 40px; }}
    </style>
</head>
<body>
<div class="container">
    <header>
        <h1>Hemlock 渗透测试报告</h1>
        <div class="meta">生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} | Hemlock AI Security Testing Platform</div>
    </header>

    <div class="stats-grid">
        <div class="stat-card critical"><div class="num">{stats['critical']}</div><div class="label">严重</div></div>
        <div class="stat-card high"><div class="num">{stats['high']}</div><div class="label">高危</div></div>
        <div class="stat-card medium"><div class="num">{stats['medium']}</div><div class="label">中危</div></div>
        <div class="stat-card low"><div class="num">{stats['low']}</div><div class="label">低危</div></div>
        <div class="stat-card total"><div class="num">{stats['total']}</div><div class="label">总计</div></div>
    </div>

    {tasks_html}

    <section class="findings">
        <h2>漏洞详情 ({stats['total']} 个)</h2>
        {''.join(finding_cards) if finding_cards else '<p style="color:#6b7280;text-align:center;padding:40px;">暂无漏洞记录</p>'}
    </section>

    <footer>Generated by Hemlock - AI Agent + Burp Suite-like Security Testing Platform</footer>
</div>
</body>
</html>"""


def _escape_html(text: str) -> str:
    """转义 HTML 特殊字符。"""
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
