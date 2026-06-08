from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from ft_diag_agent.llm import LlmProvider
from ft_diag_agent.models import (
    CaseOnlyHypothesis,
    CaseOnlyHypothesisStatus,
    DiagnosticAction,
    DiagnosticState,
    ExploratoryDiagnosticPlan,
)
from ft_diag_agent.settings import Settings
from ft_diag_agent.work_orders import work_order_to_intake_text


@dataclass(frozen=True)
class CaseOnlyPlannerResult:
    hypotheses: list[CaseOnlyHypothesis]
    plan: ExploratoryDiagnosticPlan
    actions: list[DiagnosticAction]


class _LlmCaseOnlyAction(BaseModel):
    test_id: str
    title: str
    check_instruction: str
    expected_signal: str | None = None
    supports_hypothesis_ids: list[str] = Field(default_factory=list)
    priority: int = Field(default=50, ge=1, le=99)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class _LlmCaseOnlyOutput(BaseModel):
    objective: str
    summary: str
    hypotheses: list[CaseOnlyHypothesis] = Field(default_factory=list)
    actions: list[_LlmCaseOnlyAction] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class CaseOnlyPlanner:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.llm = LlmProvider(self.settings)

    def plan(self, state: DiagnosticState, limit: int = 5) -> CaseOnlyPlannerResult:
        evidence_ids = [e.evidence_id for e in state.evidence_chain[:8]]
        if state.case_only_hypotheses:
            hypotheses = _update_hypotheses_from_findings(state.case_only_hypotheses, state)
            active_hypotheses = [
                hypothesis
                for hypothesis in hypotheses
                if hypothesis.status in {CaseOnlyHypothesisStatus.OPEN, CaseOnlyHypothesisStatus.NEEDS_EVIDENCE}
            ]
            actions = self._rule_actions(state, active_hypotheses, evidence_ids) if active_hypotheses else []
            iteration = (state.case_only_plan.iteration + 1) if state.case_only_plan else 2
            completed_action_ids = _completed_case_only_action_ids(state)
            stopped_reason = None if actions else _case_only_stopped_reason(hypotheses)
            return CaseOnlyPlannerResult(
                hypotheses=hypotheses,
                plan=ExploratoryDiagnosticPlan(
                    objective=state.case_only_plan.objective
                    if state.case_only_plan
                    else "在无故障树覆盖条件下持续收敛探索假设。",
                    summary=_loop_summary(state, hypotheses, actions),
                    planner_source="RULE_LOOP",
                    hypothesis_ids=[h.hypothesis_id for h in hypotheses],
                    next_action_ids=[a.action_id for a in actions[:limit]],
                    evidence_ids=evidence_ids,
                    risk_notes=["非故障树覆盖探索循环，不可生产放行"],
                    iteration=iteration,
                    completed_action_ids=completed_action_ids,
                    stopped_reason=stopped_reason,
                ),
                actions=actions[:limit],
            )
        llm_output = self._llm_plan(state)
        if llm_output and llm_output.hypotheses and llm_output.actions:
            hypotheses = _normalize_hypotheses(llm_output.hypotheses, evidence_ids)
            actions = self._actions_from_llm(llm_output.actions, hypotheses, evidence_ids)
            return CaseOnlyPlannerResult(
                hypotheses=hypotheses,
                plan=ExploratoryDiagnosticPlan(
                    objective=llm_output.objective,
                    summary=llm_output.summary,
                    planner_source="LLM",
                    hypothesis_ids=[h.hypothesis_id for h in hypotheses],
                    next_action_ids=[a.action_id for a in actions[:limit]],
                    evidence_ids=evidence_ids,
                    risk_notes=[
                        "非故障树覆盖探索计划，不可生产放行",
                        *llm_output.risk_notes,
                    ],
                    iteration=1,
                ),
                actions=actions[:limit],
            )

        hypotheses = self._rule_hypotheses(state, evidence_ids)
        actions = self._rule_actions(state, hypotheses, evidence_ids)
        return CaseOnlyPlannerResult(
            hypotheses=hypotheses,
            plan=ExploratoryDiagnosticPlan(
                objective="在无故障树覆盖条件下缩小疑似系统范围并设计下一轮验证。",
                summary=self._rule_summary(state, hypotheses),
                planner_source="RULE",
                hypothesis_ids=[h.hypothesis_id for h in hypotheses],
                next_action_ids=[a.action_id for a in actions[:limit]],
                evidence_ids=evidence_ids,
                risk_notes=["非故障树覆盖探索计划，不可生产放行"],
                iteration=1,
            ),
            actions=actions[:limit],
        )

    def _llm_plan(self, state: DiagnosticState) -> _LlmCaseOnlyOutput | None:
        evidence_index = [
            {
                "evidence_id": evidence.evidence_id,
                "source_type": evidence.source_type,
                "source_id": evidence.source_id,
                "claim": evidence.claim,
                "source_refs": evidence.source_refs,
            }
            for evidence in state.evidence_chain[:8]
        ]
        payload = {
            "work_order": state.work_order.model_dump(mode="json") if state.work_order else None,
            "normalized_phenomenon": state.intake.model_dump(mode="json") if state.intake else None,
            "evidence_index": evidence_index,
            "human_findings": [finding.model_dump(mode="json") for finding in state.case_only_findings],
            "existing_hypotheses": [h.model_dump(mode="json") for h in state.case_only_hypotheses],
            "constraints": [
                "没有故障树覆盖，不能给最终根因，不能生产 PASS",
                "输出 3-5 个可区分的诊断假设",
                "每个下一步动作必须是现场工程师可填写的 HITL 检查",
                "动作应说明预期信号，以及支持/反驳哪些假设",
            ],
        }
        return self.llm.json_completion(
            system_prompt=(
                "你是制造质量开发态自主诊断 Planner。你的任务是在无故障树覆盖时，"
                "基于工单、历史/RAG 证据和人工发现，形成可追溯的诊断假设与下一步人工检查。"
                "不要输出生产放行结论。"
            ),
            user_prompt=json.dumps(payload, ensure_ascii=False),
            response_model=_LlmCaseOnlyOutput,
            complexity="pro",
        )

    def _rule_hypotheses(self, state: DiagnosticState, evidence_ids: list[str]) -> list[CaseOnlyHypothesis]:
        routing_text = _work_order_text(state)
        domain_template = _domain_template(routing_text)
        if domain_template:
            return [
                CaseOnlyHypothesis(
                    hypothesis_id=domain_template["hypothesis_id"],
                    system_area=domain_template["system_area"],
                    component=domain_template["component"],
                    failure_mode=domain_template["failure_mode"],
                    rationale=domain_template["rationale"],
                    confidence=domain_template["confidence"],
                    supporting_evidence_ids=evidence_ids[:3],
                    next_check_ids=[item[0] for item in domain_template["actions"]],
                )
            ]
        if _looks_powertrain(routing_text):
            return [
                CaseOnlyHypothesis(
                    hypothesis_id="H-POWER-BMS-DERATE",
                    system_area="动力系统/BMS",
                    component="动力电池单体/采样链路",
                    failure_mode="单体压差、温度或采样异常触发保护性扭矩降额",
                    rationale="工单出现动力受限、VCU 扭矩降额、BMS 单体压差瞬时升高等信号。",
                    confidence=0.62,
                    supporting_evidence_ids=evidence_ids[:3],
                    next_check_ids=["CASE_ONLY_BMS_FREEZE_FRAME", "CASE_ONLY_CELL_DELTA_TREND"],
                ),
                CaseOnlyHypothesis(
                    hypothesis_id="H-POWER-HVIL-LIMIT",
                    system_area="高压系统",
                    component="高压互锁/高压连接器",
                    failure_mode="高压互锁或连接状态异常导致车辆进入限功率保护",
                    rationale="初步检查包含高压互锁，动力受限场景需要确认 HVIL 是否瞬断或边界不稳定。",
                    confidence=0.48,
                    supporting_evidence_ids=evidence_ids[:2],
                    next_check_ids=["CASE_ONLY_HVIL_STATUS_CONFIRM"],
                ),
                CaseOnlyHypothesis(
                    hypothesis_id="H-POWER-VCU-TORQUE-PATH",
                    system_area="整车控制",
                    component="VCU/加速踏板/电机控制请求链路",
                    failure_mode="VCU 降额策略、踏板信号或扭矩请求链路不一致",
                    rationale="现场已提到 VCU 扭矩降额，需要区分策略保护、输入信号异常和执行侧限制。",
                    confidence=0.55,
                    supporting_evidence_ids=evidence_ids[:3],
                    next_check_ids=["CASE_ONLY_TORQUE_REQUEST_COMPARE"],
                ),
            ]
        if any(keyword in routing_text for keyword in ("空调", "制冷", "压缩机", "出风")):
            return [
                CaseOnlyHypothesis(
                    hypothesis_id="H-HVAC-REFRIGERANT",
                    system_area="热管理/空调",
                    component="制冷剂回路",
                    failure_mode="制冷剂压力或膨胀阀状态异常导致制冷不足",
                    rationale="工单描述为空调制冷不足，优先确认压力、温度和压缩机工作边界。",
                    confidence=0.5,
                    supporting_evidence_ids=evidence_ids[:2],
                    next_check_ids=["CASE_ONLY_HVAC_PRESSURE_TEMP"],
                ),
                CaseOnlyHypothesis(
                    hypothesis_id="H-HVAC-COMPRESSOR-CONTROL",
                    system_area="热管理/电控",
                    component="电动压缩机/控制请求",
                    failure_mode="压缩机控制请求、转速或保护状态异常",
                    rationale="压缩机工作但效果差，需要确认请求与实际运行状态。",
                    confidence=0.45,
                    supporting_evidence_ids=evidence_ids[:2],
                    next_check_ids=["CASE_ONLY_HVAC_COMPRESSOR_REQUEST"],
                ),
            ]
        return [
            CaseOnlyHypothesis(
                hypothesis_id="H-GENERIC-SCOPE",
                system_area=(
                    state.work_order.business_domain
                    if state.work_order and state.work_order.business_domain
                    else "未知系统"
                ),
                component=None,
                failure_mode="故障范围尚未收敛",
                rationale="缺少覆盖故障树和同类闭环样本，需先补充复现条件、DTC、现场检查和处置反馈。",
                confidence=0.3,
                supporting_evidence_ids=evidence_ids[:2],
                next_check_ids=["CASE_ONLY_SCOPE_CONFIRM", "CASE_ONLY_HISTORY_COMPARE"],
            )
        ]

    def _rule_actions(
        self,
        state: DiagnosticState,
        hypotheses: list[CaseOnlyHypothesis],
        evidence_ids: list[str],
    ) -> list[DiagnosticAction]:
        routing_text = _work_order_text(state)
        if domain_template := _domain_template(routing_text):
            specs = [
                (
                    test_id,
                    title,
                    instruction,
                    domain_template["hypothesis_id"],
                    priority,
                    confidence,
                )
                for test_id, title, instruction, priority, confidence in domain_template["actions"]
            ]
        elif _looks_powertrain(routing_text):
            specs = [
                (
                    "CASE_ONLY_BMS_FREEZE_FRAME",
                    "核对 BMS/VCU 冻结帧",
                    "读取 P1A0B 及相关 BMS/VCU DTC 冻结帧，记录触发时 SOC、单体最大/最小电压、"
                    "温度、允许放电功率、降额原因码。",
                    "H-POWER-BMS-DERATE",
                    20,
                    0.72,
                ),
                (
                    "CASE_ONLY_CELL_DELTA_TREND",
                    "复核单体压差趋势",
                    "在静置、上电、轻踩加速三个工况下记录单体压差变化，判断是否为瞬时采样异常或真实一致性问题。",
                    "H-POWER-BMS-DERATE",
                    25,
                    0.68,
                ),
                (
                    "CASE_ONLY_HVIL_STATUS_CONFIRM",
                    "确认高压互锁稳定性",
                    "查看 HVIL 状态位和历史瞬断记录，轻微晃动高压连接器/维修开关时观察状态是否抖动。",
                    "H-POWER-HVIL-LIMIT",
                    32,
                    0.55,
                ),
                (
                    "CASE_ONLY_TORQUE_REQUEST_COMPARE",
                    "比对扭矩请求链路",
                    "同步记录加速踏板开度、VCU 扭矩请求、电机控制器实际扭矩、允许扭矩上限，"
                    "确认限制来自请求侧还是执行侧。",
                    "H-POWER-VCU-TORQUE-PATH",
                    35,
                    0.62,
                ),
            ]
        elif any(keyword in routing_text for keyword in ("空调", "制冷", "压缩机", "出风")):
            specs = [
                (
                    "CASE_ONLY_HVAC_PRESSURE_TEMP",
                    "记录制冷压力与温度",
                    "记录高低压、蒸发器温度、出风温度和环境温度，判断制冷剂回路是否异常。",
                    "H-HVAC-REFRIGERANT",
                    25,
                    0.58,
                ),
                (
                    "CASE_ONLY_HVAC_COMPRESSOR_REQUEST",
                    "核对压缩机请求与实际状态",
                    "记录压缩机请求转速、实际转速、电流、电压和保护状态，确认是否为控制或保护限制。",
                    "H-HVAC-COMPRESSOR-CONTROL",
                    35,
                    0.52,
                ),
                (
                    "CASE_ONLY_HVAC_BOUNDARY_CONFIRM",
                    "确认热管理边界条件",
                    "补充环境温度、目标温度、风量档位、热泵/冷却液回路状态和故障复现频次。",
                    "H-HVAC-REFRIGERANT",
                    45,
                    0.4,
                ),
            ]
        else:
            specs = [
                (
                    "CASE_ONLY_SCOPE_CONFIRM",
                    "确认故障范围与复现条件",
                    "补充故障发生条件、频次、边界条件、DTC、已排除项和当前处置反馈。",
                    hypotheses[0].hypothesis_id,
                    40,
                    0.35,
                ),
                (
                    "CASE_ONLY_HISTORY_COMPARE",
                    "对照历史工单与维修闭环",
                    "检索并记录最相似历史工单、最终处置、复测结果，用于形成下一轮探索假设。",
                    hypotheses[0].hypothesis_id,
                    48,
                    0.32,
                ),
            ]
        hypothesis_ids = {hypothesis.hypothesis_id for hypothesis in hypotheses}
        executed = {test.test_id for test in state.executed_tests}
        actions: list[DiagnosticAction] = []
        for test_id, title, instruction, hypothesis_id, priority, confidence in specs:
            if test_id in executed:
                continue
            target = hypothesis_id if hypothesis_id in hypothesis_ids else hypotheses[0].hypothesis_id
            actions.append(
                DiagnosticAction(
                    action_type="CASE_ONLY_HITL",
                    target_cause_id=target,
                    test_id=test_id,
                    tool_name="human_input",
                    priority=priority,
                    blocking=True,
                    expected_result_schema={
                        "result": "str",
                        "value": "str|number|bool|null",
                        "passed": "bool|null",
                        "notes": "str|null",
                        "supports_hypothesis_id": "str|null",
                    },
                    reason=f"{title}：{instruction}",
                    source_refs=_source_refs(state),
                    planner_source="RULE",
                    evidence_ids=evidence_ids[:5],
                    confidence=confidence,
                    risk_notes=["探索性诊断动作，不可作为生产放行依据"],
                )
            )
        return actions

    def _actions_from_llm(
        self,
        llm_actions: list[_LlmCaseOnlyAction],
        hypotheses: list[CaseOnlyHypothesis],
        evidence_ids: list[str],
    ) -> list[DiagnosticAction]:
        valid_hypotheses = {hypothesis.hypothesis_id for hypothesis in hypotheses}
        actions: list[DiagnosticAction] = []
        for item in llm_actions:
            target = next((hid for hid in item.supports_hypothesis_ids if hid in valid_hypotheses), None)
            actions.append(
                DiagnosticAction(
                    action_type="CASE_ONLY_HITL",
                    target_cause_id=target,
                    test_id=_normalize_case_only_id(item.test_id),
                    tool_name="human_input",
                    priority=item.priority,
                    blocking=True,
                    expected_result_schema={
                        "result": "str",
                        "value": "str|number|bool|null",
                        "passed": "bool|null",
                        "notes": "str|null",
                        "supports_hypothesis_id": "str|null",
                    },
                    reason=f"{item.title}：{item.check_instruction}"
                    + (f" 预期信号：{item.expected_signal}" if item.expected_signal else ""),
                    planner_source="LLM",
                    evidence_ids=item.evidence_ids or evidence_ids[:5],
                    confidence=item.confidence,
                    risk_notes=["LLM 生成探索动作，需人工确认", *item.risk_notes],
                )
            )
        return actions

    def _rule_summary(self, state: DiagnosticState, hypotheses: list[CaseOnlyHypothesis]) -> str:
        names = "；".join(f"{h.system_area}/{h.failure_mode}" for h in hypotheses[:3])
        phenomenon = state.intake.phenomenon if state.intake else state.work_order.failure_phenomenon
        return f"围绕“{phenomenon}”形成 {len(hypotheses)} 个探索假设：{names}。"


def _normalize_hypotheses(hypotheses: list[CaseOnlyHypothesis], evidence_ids: list[str]) -> list[CaseOnlyHypothesis]:
    normalized: list[CaseOnlyHypothesis] = []
    seen: set[str] = set()
    for index, hypothesis in enumerate(hypotheses[:5], start=1):
        hypothesis_id = hypothesis.hypothesis_id or f"H-LLM-{index:02d}"
        if hypothesis_id in seen:
            hypothesis_id = f"{hypothesis_id}-{index}"
        seen.add(hypothesis_id)
        normalized.append(
            hypothesis.model_copy(
                update={
                    "hypothesis_id": hypothesis_id,
                    "supporting_evidence_ids": hypothesis.supporting_evidence_ids or evidence_ids[:3],
                }
            )
        )
    return normalized


def _update_hypotheses_from_findings(
    hypotheses: list[CaseOnlyHypothesis],
    state: DiagnosticState,
) -> list[CaseOnlyHypothesis]:
    updated: list[CaseOnlyHypothesis] = []
    has_findings = bool(state.case_only_findings)
    for hypothesis in hypotheses:
        supporting = list(hypothesis.supporting_evidence_ids)
        contradicting = list(hypothesis.contradicting_evidence_ids)
        support_count = 0
        refute_count = 0
        for finding in state.case_only_findings:
            if hypothesis.hypothesis_id in finding.supports_hypothesis_ids:
                support_count += 1
                if finding.evidence_id:
                    supporting.append(finding.evidence_id)
            if hypothesis.hypothesis_id in finding.refutes_hypothesis_ids:
                refute_count += 1
                if finding.evidence_id:
                    contradicting.append(finding.evidence_id)
        status = hypothesis.status
        confidence = hypothesis.confidence
        if refute_count > support_count:
            status = CaseOnlyHypothesisStatus.REFUTED
            confidence = max(0.05, confidence - 0.25)
        elif support_count > refute_count:
            status = CaseOnlyHypothesisStatus.SUPPORTED
            confidence = min(0.95, confidence + 0.2)
        elif has_findings and status == CaseOnlyHypothesisStatus.OPEN:
            status = CaseOnlyHypothesisStatus.NEEDS_EVIDENCE
            confidence = max(0.1, confidence - 0.05)
        updated.append(
            hypothesis.model_copy(
                update={
                    "status": status,
                    "confidence": confidence,
                    "supporting_evidence_ids": _unique_texts(supporting),
                    "contradicting_evidence_ids": _unique_texts(contradicting),
                }
            )
        )
    return updated


def _completed_case_only_action_ids(state: DiagnosticState) -> list[str]:
    return _unique_texts(
        [
            finding.action_id
            for finding in state.case_only_findings
            if finding.action_id
        ]
    )


def _case_only_stopped_reason(hypotheses: list[CaseOnlyHypothesis]) -> str:
    if not hypotheses:
        return "NO_HYPOTHESES"
    if all(hypothesis.status == CaseOnlyHypothesisStatus.REFUTED for hypothesis in hypotheses):
        return "ALL_HYPOTHESES_REFUTED"
    if all(
        hypothesis.status in {CaseOnlyHypothesisStatus.SUPPORTED, CaseOnlyHypothesisStatus.REFUTED}
        for hypothesis in hypotheses
    ):
        return "ALL_HYPOTHESES_RESOLVED"
    return "NO_REMAINING_CHECKS"


def _loop_summary(
    state: DiagnosticState,
    hypotheses: list[CaseOnlyHypothesis],
    actions: list[DiagnosticAction],
) -> str:
    counts = {status.value: 0 for status in CaseOnlyHypothesisStatus}
    for hypothesis in hypotheses:
        counts[str(hypothesis.status)] = counts.get(str(hypothesis.status), 0) + 1
    next_text = f"下一轮规划 {len(actions)} 个检查动作" if actions else "暂无可继续规划的检查动作"
    return (
        f"已记录 {len(state.case_only_findings)} 条探索发现；"
        f"假设状态：OPEN={counts.get('OPEN', 0)}，SUPPORTED={counts.get('SUPPORTED', 0)}，"
        f"REFUTED={counts.get('REFUTED', 0)}，NEEDS_EVIDENCE={counts.get('NEEDS_EVIDENCE', 0)}；"
        f"{next_text}。"
    )


def _unique_texts(values: list[str | None]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value))


def _state_text(state: DiagnosticState) -> str:
    parts = []
    if state.work_order:
        parts.append(work_order_to_intake_text(state.work_order))
        parts.append(state.work_order.business_domain or "")
    if state.intake:
        parts.append(state.intake.phenomenon)
    parts.extend(e.claim for e in state.evidence_chain[:5])
    parts.extend(f.result for f in state.case_only_findings)
    return "\n".join(part for part in parts if part)


def _work_order_text(state: DiagnosticState) -> str:
    parts = []
    if state.work_order:
        parts.append(work_order_to_intake_text(state.work_order))
        parts.append(state.work_order.business_domain or "")
        parts.extend(state.work_order.executed_checks)
    if state.intake:
        parts.append(state.intake.phenomenon)
    return "\n".join(part for part in parts if part)


def _looks_powertrain(text: str) -> bool:
    text = text.lower()
    return any(keyword in text for keyword in ("动力", "扭矩", "vcu", "bms", "dcdc", "高压", "soc", "p1a0b"))


def _domain_template(text: str) -> dict | None:
    templates = [
        (
            ("电驱", "逆变器", "驱动系统"),
            {
                "hypothesis_id": "H-EDU-INVERTER-TEMP",
                "system_area": "电驱系统",
                "component": "逆变器温度传感器/线束",
                "failure_mode": "逆变器温度传感器读数跳变或线束接触异常",
                "rationale": "工单描述动力受限和驱动系统故障，已有逆变器温度传感器读数跳变证据。",
                "confidence": 0.58,
                "actions": [
                    (
                        "CASE_ONLY_EDU_DTC_READ",
                        "读取电驱故障码",
                        "读取电驱/逆变器 DTC、冻结帧和降额原因码。",
                        24,
                        0.58,
                    ),
                    (
                        "CASE_ONLY_INVERTER_TEMP_SENSOR",
                        "检查温度传感器线束",
                        "检查逆变器温度传感器读数、线束插接和与冷却状态的一致性。",
                        32,
                        0.6,
                    ),
                ],
            },
        ),
        (
            ("充电", "快充", "慢充", "cc2", "握手"),
            {
                "hypothesis_id": "H-CHARGE-HANDSHAKE",
                "system_area": "充电系统",
                "component": "充电口信号/温度采样链路",
                "failure_mode": "充电握手、CC2 或充电口温度信号异常",
                "rationale": "工单出现充电无法启动/中断、握手失败、CC2 或充电口温度信号异常。",
                "confidence": 0.58,
                "actions": [
                    (
                        "CASE_ONLY_CHARGE_HANDSHAKE",
                        "生成充电握手检查计划",
                        "读取充电握手状态、CC/CP/CC2 信号和 BMS 允许充电状态。",
                        24,
                        0.62,
                    ),
                    (
                        "CASE_ONLY_CHARGE_PORT_SIGNAL",
                        "检查充电口端子状态",
                        "检查充电口低压信号线束、端子水汽、接触阻抗和温度传感器读数。",
                        30,
                        0.6,
                    ),
                ],
            },
        ),
        (
            ("制动", "摩擦片", "制动盘"),
            {
                "hypothesis_id": "H-BRAKE-NOISE",
                "system_area": "制动系统",
                "component": "制动盘片",
                "failure_mode": "制动盘片状态或磨合不足导致低速异响",
                "rationale": "工单描述低速制动异响，并出现制动盘锈蚀、片盘磨合不足等证据。",
                "confidence": 0.52,
                "actions": [
                    (
                        "CASE_ONLY_BRAKE_DISC_PAD_CHECK",
                        "检查制动盘片状态",
                        "检查制动盘锈蚀、摩擦片磨损/异物和卡钳回位状态。",
                        28,
                        0.56,
                    ),
                    (
                        "CASE_ONLY_BRAKE_ROAD_TEST",
                        "执行路试复现",
                        "按低速制动工况路试复现，记录异响位置、温度和制动力是否异常。",
                        36,
                        0.48,
                    ),
                ],
            },
        ),
        (
            ("adas", "noa", "摄像头", "标定"),
            {
                "hypothesis_id": "H-ADAS-CAMERA-VIEW",
                "system_area": "ADAS",
                "component": "前视摄像头/标定状态",
                "failure_mode": "前视摄像头视野污染或标定状态异常",
                "rationale": "工单描述 NOA 受限，且已有摄像头视野或标定相关证据。",
                "confidence": 0.55,
                "actions": [
                    (
                        "CASE_ONLY_ADAS_CAMERA_VIEW",
                        "检查摄像头视野",
                        "检查前风挡摄像头区域污染、遮挡、结露和摄像头图像质量。",
                        26,
                        0.6,
                    ),
                    (
                        "CASE_ONLY_ADAS_CALIBRATION",
                        "读取标定状态",
                        "读取摄像头标定状态、感知受限原因码和相关 DTC。",
                        34,
                        0.56,
                    ),
                ],
            },
        ),
        (
            ("悬架", "底盘", "球头", "减速带"),
            {
                "hypothesis_id": "H-CHASSIS-LINKAGE",
                "system_area": "底盘悬架",
                "component": "稳定杆连杆/球头",
                "failure_mode": "稳定杆连杆球头松旷或悬架连接件间隙异常",
                "rationale": "工单描述减速带异响，且已有球头间隙或举升晃动复现证据。",
                "confidence": 0.55,
                "actions": [
                    (
                        "CASE_ONLY_CHASSIS_LIFT_CHECK",
                        "举升检查球头间隙",
                        "举升车辆检查稳定杆连杆、球头和衬套间隙。",
                        25,
                        0.6,
                    ),
                    (
                        "CASE_ONLY_CHASSIS_ROAD_TEST",
                        "路试定位声源",
                        "按减速带/碎石路工况路试，定位声源方位和触发条件。",
                        36,
                        0.48,
                    ),
                ],
            },
        ),
        (
            ("座椅", "通风", "风扇", "风道"),
            {
                "hypothesis_id": "H-SEAT-VENT-FAN",
                "system_area": "座椅系统",
                "component": "座椅通风风扇/风道",
                "failure_mode": "座椅通风风扇堵转、供电异常或风道异物",
                "rationale": "工单描述座椅通风无风量，且已有风扇不转/供电正常/堵转证据。",
                "confidence": 0.55,
                "actions": [
                    ("CASE_ONLY_SEAT_FAN_POWER", "测风扇供电", "测量座椅通风风扇供电、PWM 控制和堵转电流。", 24, 0.58),
                    ("CASE_ONLY_SEAT_DUCT_CHECK", "检查风道异物", "检查座椅风道、滤网和风扇叶轮是否堵塞。", 34, 0.52),
                ],
            },
        ),
        (
            ("灯光", "大灯", "近光", "led"),
            {
                "hypothesis_id": "H-LIGHT-LED-DRIVER",
                "system_area": "灯光系统",
                "component": "LED 驱动板/灯具供电",
                "failure_mode": "大灯 LED 驱动板失效或灯具供电输出异常",
                "rationale": "工单描述单侧近光不亮，且已有供电正常、驱动板无输出证据。",
                "confidence": 0.55,
                "actions": [
                    ("CASE_ONLY_LIGHT_POWER_CHECK", "测灯具供电", "测量灯具供电、接地和控制信号。", 24, 0.54),
                    (
                        "CASE_ONLY_LIGHT_DRIVER_OUTPUT",
                        "检查驱动板输出",
                        "检查 LED 驱动板输出、电流保护状态和灯板连接。",
                        32,
                        0.58,
                    ),
                ],
            },
        ),
        (
            ("tbox", "车联网", "远程控车", "车辆离线"),
            {
                "hypothesis_id": "H-TBOX-CONNECTIVITY",
                "system_area": "车联网",
                "component": "TBOX/SIM/网络链路",
                "failure_mode": "TBOX 通信模块或蜂窝网络链路异常",
                "rationale": "工单描述远程控车失败、App 离线，但车机显示正常。",
                "confidence": 0.52,
                "actions": [
                    (
                        "CASE_ONLY_TBOX_ONLINE_STATUS",
                        "检查TBOX在线状态",
                        "读取 TBOX 在线状态、SIM 信号、网络注册状态和重启记录。",
                        25,
                        0.58,
                    ),
                    (
                        "CASE_ONLY_TBOX_COCKPIT_BOUNDARY",
                        "区分车机显示与车联网",
                        "确认车机显示/座舱功能与 TBOX 远控链路是否独立异常。",
                        36,
                        0.5,
                    ),
                ],
            },
        ),
        (
            ("雨量", "雨刮", "lin"),
            {
                "hypothesis_id": "H-BODY-RAIN-SENSOR",
                "system_area": "车身电器",
                "component": "雨量传感器/LIN 通信",
                "failure_mode": "雨量传感器通信异常或供电异常",
                "rationale": "工单描述雨刮自动模式异常，手动档正常，并有 LIN 无响应证据。",
                "confidence": 0.55,
                "actions": [
                    (
                        "CASE_ONLY_RAIN_SENSOR_COMM",
                        "读取雨量传感器通信",
                        "读取雨量传感器 LIN 通信、DTC 和自动雨刮请求状态。",
                        24,
                        0.58,
                    ),
                    ("CASE_ONLY_RAIN_SENSOR_POWER", "检查传感器供电", "检查雨量传感器供电、接地和插接件。", 34, 0.52),
                ],
            },
        ),
        (
            ("尾门", "撑杆", "后备箱"),
            {
                "hypothesis_id": "H-TAILGATE-STRUT",
                "system_area": "尾门系统",
                "component": "电动撑杆/尾门阻力",
                "failure_mode": "电动撑杆卡滞、阻力异常或异物干涉",
                "rationale": "工单描述尾门开启/关闭异常，并有撑杆电流或阻力异常证据。",
                "confidence": 0.55,
                "actions": [
                    (
                        "CASE_ONLY_TAILGATE_STRUT_CURRENT",
                        "采集撑杆电流",
                        "采集左右撑杆电流、霍尔信号和防夹状态。",
                        24,
                        0.58,
                    ),
                    (
                        "CASE_ONLY_TAILGATE_RESISTANCE",
                        "检查尾门阻力",
                        "检查尾门铰链、撑杆阻力和是否有异物干涉。",
                        32,
                        0.56,
                    ),
                ],
            },
        ),
    ]
    lower = text.lower()
    for keywords, template in templates:
        if any(keyword in lower for keyword in keywords):
            return template
    if "屏幕暗" in text or "亮度" in text:
        return {
            "hypothesis_id": "H-COCKPIT-BRIGHTNESS",
            "system_area": "座舱设置",
            "component": "屏幕亮度设置/显示状态",
            "failure_mode": "亮度设置过低或用户设置导致屏幕暗",
            "rationale": "工单描述屏幕暗但不是黑屏，已有亮度设置过低证据。",
            "confidence": 0.5,
            "actions": [
                (
                    "CASE_ONLY_DISPLAY_IMAGE_CONFIRM",
                    "确认是否有图像",
                    "确认屏幕是否存在图像、背光和触控响应。",
                    24,
                    0.52,
                ),
                ("CASE_ONLY_DISPLAY_BRIGHTNESS", "检查亮度设置", "检查亮度设置、自动亮度和用户配置。", 30, 0.58),
            ],
        }
    return None


def _source_refs(state: DiagnosticState) -> list[str]:
    refs = [ref for evidence in state.evidence_chain for ref in evidence.source_refs]
    return list(dict.fromkeys(refs))


def _normalize_case_only_id(value: str) -> str:
    text = re.sub(r"[^A-Z0-9_]+", "_", value.upper()).strip("_")
    if not text:
        text = "LLM_ACTION"
    return text if text.startswith("CASE_ONLY_") else f"CASE_ONLY_{text}"
