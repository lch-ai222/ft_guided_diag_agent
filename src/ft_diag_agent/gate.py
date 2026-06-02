from __future__ import annotations

from ft_diag_agent.models import DiagnosticState, GateResult, GateStatus


class Gate:
    def evaluate(self, state: DiagnosticState) -> GateResult:
        blocking: list[str] = []
        required: list[str] = []
        risk_notes: list[str] = list(state.data_quality_notes)
        if state.rework_risk and state.rework_risk.risk_notes:
            risk_notes.extend(state.rework_risk.risk_notes)
            required.extend(state.rework_risk.recommended_checks)

        if state.coverage_decision and state.coverage_decision.status == "UNSUPPORTED":
            guardrail_text = _guardrail_text(state)
            if "事故" in guardrail_text and any(marker in guardrail_text for marker in ["多系统", "多处", "无法归因"]):
                return GateResult(
                    status=GateStatus.GRAY,
                    blocking_reasons=["事故/多系统问题缺少单点归因证据"],
                    required_actions=["补充事故维修记录和线束分段检查后再诊断"],
                    risk_notes=[*risk_notes, "多系统事故场景不可直接发布根因"],
                    can_generate_final_report=True,
                )
            if state.diagnosis_mode == "CASE_ONLY_EXPLORATORY":
                return GateResult(
                    status=GateStatus.GRAY,
                    blocking_reasons=["开发态历史工单探索，不属于故障树覆盖范围"],
                    required_actions=["补充故障树或人工确认覆盖范围后才能生产放行"],
                    risk_notes=[*risk_notes, "非故障树覆盖诊断结果不可生产 PASS"],
                    can_generate_final_report=True,
                )
            return GateResult(
                status=GateStatus.FAIL,
                blocking_reasons=["该工单故障类型不在现有故障树覆盖范围内"],
                required_actions=["补充对应故障树后再诊断"],
                risk_notes=risk_notes,
                can_generate_final_report=False,
            )

        if not state.matched_trees:
            return GateResult(
                status=GateStatus.FAIL,
                blocking_reasons=["未匹配到可用故障树"],
                required_actions=["补充或更换故障树 TTL 输入"],
                risk_notes=risk_notes,
                can_generate_final_report=False,
            )

        if not state.candidate_paths:
            return GateResult(
                status=GateStatus.FAIL,
                blocking_reasons=["故障树未枚举出可诊断路径"],
                required_actions=["检查故障树 transition/root 节点完整性"],
                risk_notes=risk_notes,
                can_generate_final_report=False,
            )

        executed_ids = {test.test_id for test in state.executed_tests}
        supporting_cause_ids = {
            evidence.supports_cause_id
            for evidence in state.evidence_chain
            if evidence.supports_cause_id and evidence.strength >= 0.5
        }

        active_node = state.active_node_id
        if active_node:
            node = None
            if state.active_tree_id:
                node = state.candidate_causes[0] if state.candidate_causes else None
            if any(cause.cause_id == active_node for cause in state.candidate_causes):
                has_support = active_node in supporting_cause_ids
                if has_support:
                    return GateResult(
                        status=GateStatus.PASS,
                        blocking_reasons=[],
                        required_actions=[],
                        risk_notes=risk_notes,
                        can_generate_final_report=True,
                    )
            _ = node

        verified_paths = []
        for path in state.candidate_paths:
            if path.test_ids and all(test_id in executed_ids for test_id in path.test_ids):
                if path.root_cause_id in supporting_cause_ids:
                    verified_paths.append(path)

        if verified_paths:
            return GateResult(
                status=GateStatus.PASS,
                blocking_reasons=[],
                required_actions=[],
                risk_notes=risk_notes,
                can_generate_final_report=True,
            )

        if state.planned_actions:
            for action in state.planned_actions:
                if action.blocking and action.test_id not in executed_ids:
                    required.append(action.test_id or action.action_id)
            blocking.append("存在未完成的关键检测项")

        if not supporting_cause_ids:
            blocking.append("尚无强度足够的根因支持证据")
            required.append("补充人工检测、RAG 证据或生产工具证据")

        return GateResult(
            status=GateStatus.GRAY,
            blocking_reasons=sorted(set(blocking)),
            required_actions=sorted(set(required)),
            risk_notes=risk_notes,
            can_generate_final_report=True,
        )


def _guardrail_text(state: DiagnosticState) -> str:
    if not state.work_order:
        return ""
    return "\n".join(
        part
        for part in [
            state.work_order.failure_phenomenon,
            state.work_order.description or "",
            state.work_order.business_domain or "",
            *state.work_order.executed_checks,
        ]
        if part
    )
