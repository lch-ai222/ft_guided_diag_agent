from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from ft_diag_agent.llm import LlmProvider
from ft_diag_agent.models import WorkOrder
from ft_diag_agent.settings import Settings

FLEX_HEADING_RE = re.compile(
    r"^##\s*(?P<prefix>.+?)[｜|](?P<order_id>[A-Z0-9][A-Z0-9_-]{2,})[｜|](?P<title>.+)$"
)
ORDER_ID_RE = re.compile(
    r"(?:工单编号|order[_\s-]*id|case[_\s-]*id)\s*[:：]\s*`?(?P<order_id>[A-Z0-9][A-Z0-9_-]{2,})`?",
    re.I,
)
VIN_RE = re.compile(r"\b(?P<vin>[A-HJ-NPR-Z0-9]{11,17})\b")

ORDER_HEADING_RE = re.compile(r"^##\s+(?P<order_id>[A-Z]{2}-[A-Z]{2}-\d{6}-\d{3})[｜|](?P<title>.+)$")


class _LlmWorkOrderExtraction(BaseModel):
    order_id: str | None = None
    title: str | None = None
    failure_phenomenon: str | None = None
    vin: str | None = None
    created_time: str | None = None
    vehicle_project: str | None = None
    business_domain: str | None = None
    source: str | None = None
    station_or_scene: str | None = None
    severity: str | None = None
    description: str | None = None
    executed_checks: list[str] = Field(default_factory=list)
    repair_action: str | None = None
    expected_route: str | None = None
    expected_fault_tree: str | None = None
    expected_root_cause: str | None = None
    expected_leaf_symptom_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    quality_notes: list[str] = Field(default_factory=list)


def parse_work_order_files(raw_docs_dir: str | Path) -> list[WorkOrder]:
    root = Path(raw_docs_dir)
    orders: list[WorkOrder] = []
    for path in sorted(root.glob("mock_work_orders_*.md")):
        orders.extend(parse_work_order_markdown(path))
    return orders


def parse_work_order_markdown(path: str | Path) -> list[WorkOrder]:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="ignore")
    return parse_work_order_markdown_text(text, source_path=path)


def parse_work_order_markdown_text(text: str, source_path: str | Path | None = None) -> list[WorkOrder]:
    path = Path(source_path) if source_path else Path("<pasted-work-order>")
    lines = text.splitlines()
    chunks: list[tuple[str, str, list[str]]] = []
    current_id = ""
    current_title = ""
    current_lines: list[str] = []
    for line in lines:
        match = ORDER_HEADING_RE.match(line)
        if match:
            if current_id:
                chunks.append((current_id, current_title, current_lines))
            current_id = match.group("order_id")
            current_title = match.group("title").strip()
            current_lines = [line]
        elif current_id:
            current_lines.append(line)
    if current_id:
        chunks.append((current_id, current_title, current_lines))
    return [_parse_chunk(order_id, title, chunk, path) for order_id, title, chunk in chunks]


def parse_pasted_work_order_text(
    text: str,
    settings: Settings | None = None,
    source_path: str | Path | None = None,
) -> WorkOrder | None:
    if not text.strip():
        return None
    strict_orders = parse_work_order_markdown_text(text, source_path=source_path)
    if strict_orders:
        return strict_orders[0]

    fallback = _fallback_parse_free_text(text, source_path=source_path)
    llm_order = _llm_parse_free_text(text, settings=settings, fallback=fallback)
    return llm_order or fallback


def _parse_chunk(order_id: str, title: str, lines: list[str], path: Path) -> WorkOrder:
    raw_text = "\n".join(lines)
    fields = _parse_table_fields(lines)
    return WorkOrder(
        order_id=order_id,
        title=title,
        failure_phenomenon=fields.get("failure_phenomenon", title),
        vin=fields.get("vin") or fields.get("VIN"),
        created_time=fields.get("created_time"),
        vehicle_project=fields.get("vehicle_project"),
        business_domain=fields.get("business_domain"),
        source=fields.get("source"),
        station_or_scene=fields.get("station_or_scene"),
        severity=fields.get("severity"),
        expected_route=fields.get("expected_route"),
        expected_fault_tree=fields.get("expected_fault_tree"),
        expected_root_cause=fields.get("expected_root_cause"),
        expected_leaf_symptom_id=fields.get("expected_leaf_symptom_id"),
        description=_section(raw_text, "现象描述"),
        executed_checks=_checks(_section(raw_text, "已执行检查/证据")),
        repair_action=_section(raw_text, "处理措施与闭环"),
        extraction_method="STRICT_MARKDOWN",
        raw_text=raw_text,
        source_path=str(path),
    )


def _parse_table_fields(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        if not line.startswith("|") or "---" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 2 and cells[0] != "字段":
            fields[cells[0]] = cells[1]
    return fields


def _fallback_parse_free_text(text: str, source_path: str | Path | None = None) -> WorkOrder:
    heading_order_id, heading_title = _extract_heading(text)
    order_id = heading_order_id or _extract_labeled_value(text, ORDER_ID_RE) or _generated_order_id(text)
    title = heading_title or _first_heading(text) or _extract_labeled_line(text, "故障标签")
    failure = (
        _extract_labeled_line(text, "故障标签")
        or heading_title
        or _section(text, "用户/现场描述")
        or _section(text, "现象描述")
        or title
        or text.strip()[:120]
    )
    vehicle_project, station_or_scene = _split_project_factory(_extract_labeled_line(text, "车型/工厂"))
    description = _section(text, "用户/现场描述") or _section(text, "现场描述") or _section(text, "现象描述")
    checks = [
        *_bullet_items(_section(text, "初步检查记录")),
        *_bullet_items(_section(text, "已执行检查/证据")),
    ]
    finding = _section(text, "现场发现")
    if finding:
        checks.append(finding)
    expected_fault_tree = _none_if_literal_none(_extract_labeled_line(text, "期望故障树"))
    return WorkOrder(
        order_id=order_id,
        title=title,
        failure_phenomenon=_clean_inline(failure),
        vin=_extract_labeled_line(text, "VIN") or _extract_vin(text),
        created_time=_extract_labeled_line(text, "创建时间"),
        vehicle_project=vehicle_project,
        business_domain=_extract_labeled_line(text, "业务域"),
        source=_extract_labeled_line(text, "来源"),
        station_or_scene=station_or_scene,
        severity=_extract_labeled_line(text, "严重度"),
        description=description,
        executed_checks=[_clean_inline(item) for item in checks if item.strip()],
        repair_action=_section(text, "处理措施与闭环"),
        expected_route=_extract_labeled_line(text, "期望路由"),
        expected_fault_tree=expected_fault_tree,
        extraction_method="RULE_FALLBACK",
        raw_text=text,
        source_path=str(source_path) if source_path else "<pasted-work-order>",
    )


def _llm_parse_free_text(
    text: str,
    settings: Settings | None,
    fallback: WorkOrder,
) -> WorkOrder | None:
    provider = LlmProvider(settings or Settings())
    result = provider.json_completion(
        system_prompt=(
            "你是制造质量诊断系统的工单抽取器。输入可能是自由文本、半结构化 Markdown、"
            "OCR 文本或字段顺序混乱的工单。请只抽取原文明确支持的信息，不要编造。"
        ),
        user_prompt=(
            "从下面工单文本抽取 JSON。若字段不存在则返回 null 或空数组。"
            "failure_phenomenon 应是最适合诊断路由的简短故障现象，不要把否定项当成现象。\n\n"
            f"{text}"
        ),
        response_model=_LlmWorkOrderExtraction,
        complexity="fast",
    )
    if not result:
        return None
    order_id = result.order_id or fallback.order_id
    failure = result.failure_phenomenon or fallback.failure_phenomenon
    return WorkOrder(
        order_id=order_id,
        title=result.title or fallback.title,
        failure_phenomenon=failure,
        vin=result.vin or fallback.vin,
        created_time=result.created_time or fallback.created_time,
        vehicle_project=result.vehicle_project or fallback.vehicle_project,
        business_domain=result.business_domain or fallback.business_domain,
        source=result.source or fallback.source,
        station_or_scene=result.station_or_scene or fallback.station_or_scene,
        severity=result.severity or fallback.severity,
        description=result.description or fallback.description,
        executed_checks=result.executed_checks or fallback.executed_checks,
        repair_action=result.repair_action or fallback.repair_action,
        expected_route=result.expected_route or fallback.expected_route,
        expected_fault_tree=_none_if_literal_none(result.expected_fault_tree) or fallback.expected_fault_tree,
        expected_root_cause=result.expected_root_cause or fallback.expected_root_cause,
        expected_leaf_symptom_id=result.expected_leaf_symptom_id or fallback.expected_leaf_symptom_id,
        extraction_method="LLM_JSON",
        extraction_quality_notes=result.quality_notes,
        raw_text=text,
        source_path=fallback.source_path,
    )


def _extract_heading(text: str) -> tuple[str | None, str | None]:
    for line in text.splitlines():
        match = FLEX_HEADING_RE.match(line.strip())
        if match:
            return match.group("order_id").strip(), _clean_inline(match.group("title"))
    return None, None


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("##"):
            return _clean_inline(line.strip("# "))
    return None


def _extract_labeled_value(text: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(text)
    return _clean_inline(match.group("order_id")) if match else None


def _extract_labeled_line(text: str, label: str) -> str | None:
    pattern = rf"[-*]?\s*{re.escape(label)}\s*[:：]\s*`?(?P<value>[^`\n]+)`?"
    match = re.search(pattern, text, flags=re.I)
    return _clean_inline(match.group("value")) if match else None


def _extract_vin(text: str) -> str | None:
    match = VIN_RE.search(text)
    return match.group("vin") if match else None


def _generated_order_id(text: str) -> str:
    import hashlib

    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8].upper()
    return f"PASTE-{digest}"


def _split_project_factory(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    parts = [item.strip() for item in re.split(r"[/／|｜]", value, maxsplit=1)]
    if len(parts) == 2:
        return parts[0] or None, parts[1] or None
    return value, None


def _bullet_items(text: str | None) -> list[str]:
    if not text:
        return []
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*")):
            items.append(stripped.lstrip("-* ").strip())
    return items or [text.strip()]


def _clean_inline(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().strip("`").strip()
    return cleaned or None


def _none_if_literal_none(value: str | None) -> str | None:
    cleaned = _clean_inline(value)
    if cleaned and cleaned.upper() in {"NONE", "NULL", "N/A", "无"}:
        return None
    return cleaned


def _section(raw_text: str, title: str) -> str | None:
    pattern = rf"###\s+{re.escape(title)}\n(?P<body>.*?)(?=\n###\s+|\n##\s+|\Z)"
    match = re.search(pattern, raw_text, flags=re.S)
    if not match:
        return None
    body = match.group("body").strip()
    return body or None


def _checks(text: str | None) -> list[str]:
    if not text:
        return []
    return [item.strip() for item in re.split(r"[；;]\s*", text) if item.strip()]


def work_order_to_intake_text(order: WorkOrder) -> str:
    parts = [
        order.failure_phenomenon,
        order.title or "",
        order.business_domain or "",
        order.description or "",
    ]
    if order.executed_checks:
        parts.append("；".join(order.executed_checks))
    return "\n".join(part for part in parts if part)
