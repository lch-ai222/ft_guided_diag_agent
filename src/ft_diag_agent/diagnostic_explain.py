from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ft_diag_agent.models import DiagnosticAction, DiagnosticState, EvidenceItem

TimelineStatus = Literal["DONE", "CURRENT", "BLOCKED", "PENDING"]
ExplanationStatus = Literal["PENDING", "EXECUTED", "SUPPORTED", "REFUTED", "INFORMATIONAL"]


class DiagnosticTimelineItem(BaseModel):
    step: str
    status: TimelineStatus
    summary: str
    detail: str | None = None
    action_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    gate_status: str | None = None


class PlannerGateExplanation(BaseModel):
    action_id: str
    status: ExplanationStatus
    planner_step: str
    planned_reason: str
    evidence_summary: str
    gate_effect: str
    test_id: str | None = None
    target_node_id: str | None = None
    target_cause_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class EvidenceSummaryRow(BaseModel):
    evidence_id: str
    source_type: str
    source_id: str
    claim: str
    supports: str
    strength: float
    interpretation: str
    source_refs: list[str] = Field(default_factory=list)


def build_diagnostic_timeline(state: DiagnosticState) -> list[DiagnosticTimelineItem]:
    executed_ids = {item.test_id for item in state.executed_tests}
    pending_actions = _pending_actions(state)
    evidence_ids = [item.evidence_id for item in state.evidence_chain]
    rows: list[DiagnosticTimelineItem] = []

    rows.append(
        DiagnosticTimelineItem(
            step="工单输入",
            status="DONE" if state.work_order or state.intake_request else "PENDING",
            summary=_work_order_summary(state),
            detail=state.work_order.description if state.work_order else None,
        )
    )
    rows.append(
        DiagnosticTimelineItem(
            step="分类与覆盖",
            status=_coverage_status(state),
            summary=_coverage_summary(state),
            detail=_coverage_detail(state),
        )
    )
    rows.append(
        DiagnosticTimelineItem(
            step="路径或探索计划",
            status=_path_status(state),
            summary=_path_summary(state),
            detail=_path_detail(state),
        )
    )
    rows.append(
        DiagnosticTimelineItem(
            step="Planner 检查动作",
            status="CURRENT" if pending_actions else "DONE" if state.planned_actions or executed_ids else "PENDING",
            summary=(
                f"待人工完成 {len(pending_actions)} 项；已记录检测 {len(executed_ids)} 项。"
                if pending_actions or executed_ids
                else "尚未生成检查动作。"
            ),
            detail="；".join(action.reason for action in pending_actions[:3]) or None,
            action_ids=[action.action_id for action in pending_actions],
        )
    )
    rows.append(
        DiagnosticTimelineItem(
            step="人工结果与证据",
            status="DONE" if state.evidence_chain else "CURRENT" if pending_actions else "PENDING",
            summary=(
                f"已形成 {len(state.evidence_chain)} 条证据，"
                f"来源 {len({item.source_type for item in state.evidence_chain})} 类。"
            ),
            detail=_evidence_detail(state),
            evidence_ids=evidence_ids[:8],
        )
    )
    rows.append(
        DiagnosticTimelineItem(
            step="Gate 判定",
            status=_gate_timeline_status(state),
            summary=_gate_summary(state),
            detail=_gate_detail(state),
            gate_status=str(state.gate_result.status) if state.gate_result else None,
        )
    )
    rows.append(
        DiagnosticTimelineItem(
            step="报告与 Replay",
            status=(
                "DONE"
                if state.final_report and state.replay_trace
                else "CURRENT"
                if state.gate_result
                else "PENDING"
            ),
            summary=(
                f"报告 {'已生成' if state.final_report else '未生成'}；Replay 记录 {len(state.replay_trace)} 条。"
            ),
            detail="最终报告可用于人工审核；Replay 可用于离线评测和训练数据导出。"
            if state.final_report or state.replay_trace
            else None,
        )
    )
    return rows


def build_planner_gate_explanations(state: DiagnosticState) -> list[PlannerGateExplanation]:
    explanations: list[PlannerGateExplanation] = []
    for action in state.planned_actions:
        related_evidence = _evidence_for_action(state, action)
        explanations.append(
            PlannerGateExplanation(
                action_id=action.action_id,
                test_id=action.test_id,
                target_node_id=action.target_node_id,
                target_cause_id=action.target_cause_id,
                status=_action_explanation_status(state, action, related_evidence),
                planner_step=_planner_step(action),
                planned_reason=action.reason,
                evidence_summary=_action_evidence_summary(action, related_evidence),
                gate_effect=_action_gate_effect(state, action, related_evidence),
                evidence_ids=[item.evidence_id for item in related_evidence],
                risk_notes=action.risk_notes,
            )
        )

    if not explanations and state.gate_result:
        explanations.append(
            PlannerGateExplanation(
                action_id="GATE_ONLY",
                status="INFORMATIONAL",
                planner_step="未产生可执行 Planner 动作",
                planned_reason="当前 Gate 直接基于覆盖范围、已执行检查或证据链完成判定。",
                evidence_summary=_evidence_detail(state) or "暂无证据链。",
                gate_effect=_gate_summary(state),
            )
        )
    return explanations


def build_evidence_summary(state: DiagnosticState) -> list[EvidenceSummaryRow]:
    return [
        EvidenceSummaryRow(
            evidence_id=item.evidence_id,
            source_type=item.source_type,
            source_id=item.source_id,
            claim=item.claim,
            supports=_supports_label(item),
            strength=item.strength,
            interpretation=_evidence_interpretation(item),
            source_refs=item.source_refs,
        )
        for item in state.evidence_chain
    ]


def _pending_actions(state: DiagnosticState) -> list[DiagnosticAction]:
    executed_ids = {item.test_id for item in state.executed_tests}
    pending: list[DiagnosticAction] = []
    for action in state.planned_actions:
        if not action.blocking:
            continue
        if action.test_id and action.test_id in executed_ids:
            continue
        pending.append(action)
    return pending


def _work_order_summary(state: DiagnosticState) -> str:
    if state.work_order:
        title = state.work_order.title or state.work_order.failure_phenomenon
        return f"{state.work_order.order_id} · {title}"
    if state.intake_request:
        return state.intake_request.raw_input[:80]
    return "尚未输入工单。"


def _coverage_status(state: DiagnosticState) -> TimelineStatus:
    if not state.coverage_decision:
        return "PENDING"
    unsupported = _enum_value(state.coverage_decision.status) == "UNSUPPORTED"
    production_blocked = _enum_value(state.diagnosis_mode) != "CASE_ONLY_EXPLORATORY"
    if unsupported and production_blocked:
        return "BLOCKED"
    return "DONE"


def _coverage_summary(state: DiagnosticState) -> str:
    if not state.coverage_decision:
        return "尚未完成分类和覆盖判断。"
    tree = state.active_tree_id or "无活动故障树"
    coverage = _enum_value(state.coverage_decision.status)
    mode = _enum_value(state.diagnosis_mode)
    return f"覆盖状态 {coverage}；诊断模式 {mode}；活动树 {tree}。"


def _coverage_detail(state: DiagnosticState) -> str | None:
    if not state.coverage_decision:
        return None
    reasons = getattr(state.coverage_decision, "reasons", []) or []
    if reasons:
        return "；".join(str(item) for item in reasons)
    if state.coverage_decision.reason:
        return state.coverage_decision.reason
    if _enum_value(state.coverage_decision.status) == "UNSUPPORTED":
        return "未命中已发布故障树，生产态不能放行；开发态只能进入 case-only 探索。"
    return None


def _path_status(state: DiagnosticState) -> TimelineStatus:
    if state.case_only_plan or state.candidate_paths:
        return "DONE"
    if state.coverage_decision and _enum_value(state.coverage_decision.status) == "UNSUPPORTED":
        return "CURRENT" if _enum_value(state.diagnosis_mode) == "CASE_ONLY_EXPLORATORY" else "BLOCKED"
    return "PENDING"


def _path_summary(state: DiagnosticState) -> str:
    if state.case_only_plan:
        return (
            f"case-only 第 {state.case_only_plan.iteration} 轮；"
            f"假设 {len(state.case_only_hypotheses)} 个；下一步动作 {len(state.case_only_plan.next_action_ids)} 个。"
        )
    if state.candidate_paths:
        return f"枚举候选路径 {len(state.candidate_paths)} 条；候选根因 {len(state.candidate_causes)} 个。"
    return "尚未形成可诊断路径或探索计划。"


def _path_detail(state: DiagnosticState) -> str | None:
    if state.case_only_plan:
        return state.case_only_plan.summary
    if state.candidate_causes:
        return "候选根因：" + "；".join(f"{item.cause_id} {item.name}" for item in state.candidate_causes[:5])
    return None


def _evidence_detail(state: DiagnosticState) -> str | None:
    if not state.evidence_chain:
        return None
    by_source: dict[str, int] = {}
    for item in state.evidence_chain:
        by_source[item.source_type] = by_source.get(item.source_type, 0) + 1
    return "；".join(f"{source}: {count}" for source, count in sorted(by_source.items()))


def _gate_timeline_status(state: DiagnosticState) -> TimelineStatus:
    if not state.gate_result:
        return "PENDING"
    if _enum_value(state.gate_result.status) == "PASS":
        return "DONE"
    if _enum_value(state.gate_result.status) == "FAIL":
        return "BLOCKED"
    return "CURRENT"


def _gate_summary(state: DiagnosticState) -> str:
    if not state.gate_result:
        return "尚未运行 Gate。"
    status = state.gate_result.status
    if _enum_value(status) == "PASS":
        return "Gate PASS：已有足够证据支持当前路径或根因。"
    reasons = state.gate_result.blocking_reasons or state.gate_result.risk_notes
    return f"Gate {status}：" + ("；".join(reasons[:3]) if reasons else "需要人工复核。")


def _gate_detail(state: DiagnosticState) -> str | None:
    if not state.gate_result:
        return None
    parts: list[str] = []
    if state.gate_result.blocking_reasons:
        parts.append("阻塞：" + "；".join(state.gate_result.blocking_reasons))
    if state.gate_result.required_actions:
        parts.append("待补充：" + "；".join(state.gate_result.required_actions))
    if state.gate_result.risk_notes:
        parts.append("风险：" + "；".join(state.gate_result.risk_notes[:4]))
    return " | ".join(parts) or None


def _evidence_for_action(state: DiagnosticState, action: DiagnosticAction) -> list[EvidenceItem]:
    evidence_by_id = {item.evidence_id: item for item in state.evidence_chain}
    related: list[EvidenceItem] = [evidence_by_id[item] for item in action.evidence_ids if item in evidence_by_id]
    for item in state.evidence_chain:
        if item in related:
            continue
        if action.test_id and item.source_id == action.test_id:
            related.append(item)
            continue
        if action.target_node_id and item.supports_node_id == action.target_node_id:
            related.append(item)
            continue
        if action.target_cause_id and item.supports_cause_id == action.target_cause_id:
            related.append(item)
    return related


def _action_explanation_status(
    state: DiagnosticState,
    action: DiagnosticAction,
    evidence: list[EvidenceItem],
) -> ExplanationStatus:
    executed = any(item.test_id == action.test_id for item in state.executed_tests) if action.test_id else False
    if not executed and not evidence:
        return "PENDING"
    if any(item.strength >= 0.5 for item in evidence):
        return "SUPPORTED"
    if evidence:
        return "REFUTED"
    return "EXECUTED"


def _planner_step(action: DiagnosticAction) -> str:
    target = action.target_cause_id or action.target_node_id or "未指定目标"
    if action.test_id:
        return f"{action.action_type} · {action.test_id} -> {target}"
    return f"{action.action_type} -> {target}"


def _action_evidence_summary(action: DiagnosticAction, evidence: list[EvidenceItem]) -> str:
    if not evidence:
        return "尚未收到对应人工检查结果或外部证据。"
    strongest = max(evidence, key=lambda item: item.strength)
    return (
        f"{len(evidence)} 条相关证据；"
        f"最强证据 {strongest.evidence_id} 强度 {strongest.strength:.2f}：{strongest.claim}"
    )


def _action_gate_effect(
    state: DiagnosticState,
    action: DiagnosticAction,
    evidence: list[EvidenceItem],
) -> str:
    if not state.gate_result:
        return "Gate 尚未运行。"
    if _enum_value(state.gate_result.status) == "PASS":
        return "当前 Gate 已 PASS；该动作关联证据已足够支撑当前诊断结论。" if evidence else "当前 Gate 已 PASS。"
    required = set(state.gate_result.required_actions)
    if action.test_id and action.test_id in required:
        return "该动作仍是 Gate 要求补齐的关键检查。"
    if action.action_id in required:
        return "该动作仍是 Gate 要求补齐的关键检查。"
    if evidence:
        return "该动作已有证据，但 Gate 仍受其他阻塞项或风险项影响。"
    if action.blocking:
        return "该动作未完成时会阻止 Gate PASS。"
    return "该动作是非阻塞建议，用于降低误判或补充审计信息。"


def _supports_label(item: EvidenceItem) -> str:
    labels: list[str] = []
    if item.supports_node_id:
        labels.append(f"节点 {item.supports_node_id}")
    if item.supports_cause_id:
        labels.append(f"根因 {item.supports_cause_id}")
    return "；".join(labels) or "上下文/风险信息"


def _evidence_interpretation(item: EvidenceItem) -> str:
    if item.strength >= 0.75:
        level = "强支持"
    elif item.strength >= 0.5:
        level = "可采信支持"
    elif item.strength >= 0.3:
        level = "弱支持/需复核"
    else:
        level = "低强度，仅作背景"
    return f"{level}；来源 {item.source_type}。"


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))
