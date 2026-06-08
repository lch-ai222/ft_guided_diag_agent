from __future__ import annotations

import os

from ft_diag_agent.fault_tree import RdfFaultTreeRepository
from ft_diag_agent.models import CandidateCause, DiagnosisReport, DiagnosticState, GateStatus
from ft_diag_agent.settings import Settings


class ReportGenerator:
    def __init__(self, repository: RdfFaultTreeRepository, settings: Settings | None = None):
        self.repository = repository
        self.settings = settings or Settings()

    def generate(self, state: DiagnosticState) -> DiagnosisReport:
        gate_status = state.gate_result.status if state.gate_result else "GRAY"
        confirmed_cause = self._confirmed_cause(state)
        recommended = self._recommended_actions(state, confirmed_cause)
        if state.rework_risk:
            recommended = list(dict.fromkeys([*state.rework_risk.avoided_repeat_actions, *recommended]))
        report = DiagnosisReport(
            case_id=state.case_id,
            root_cause=confirmed_cause.name if confirmed_cause and gate_status == GateStatus.PASS else None,
            candidate_causes=[cause.name for cause in state.candidate_causes],
            evidence=[e.claim for e in state.evidence_chain],
            executed_tests=[
                f"{item.test_id}: {item.result}" + (f" ({item.notes})" if item.notes else "")
                for item in state.executed_tests
            ],
            recommended_actions=recommended,
            gate_status=gate_status,
            risk_notes=state.gate_result.risk_notes if state.gate_result else [],
        )
        report.markdown = self._markdown(state, report)
        if self.settings.openai_enable_llm and os.getenv("OPENAI_API_KEY"):
            report.markdown = self._polish_with_llm(report.markdown)
        return report

    def _markdown(self, state: DiagnosticState, report: DiagnosisReport) -> str:
        phenomenon = state.intake.phenomenon if state.intake else "未归一化"
        lines = [
            f"# 诊断报告：{report.case_id}",
            "",
            f"- 标准现象：{phenomenon}",
            f"- Gate 状态：{report.gate_status}",
            f"- 确认根因：{report.root_cause or '未确认'}",
            "",
            "## 候选根因",
        ]
        if report.candidate_causes:
            lines.extend([f"- {cause}" for cause in report.candidate_causes])
        else:
            lines.append("- 无")
        lines.extend(["", "## 检测记录"])
        lines.extend([f"- {item}" for item in report.executed_tests] or ["- 无"])
        lines.extend(["", "## 证据链"])
        lines.extend([f"- {item}" for item in report.evidence] or ["- 无"])
        lines.extend(["", "## 推荐处置"])
        lines.extend([f"- {item}" for item in report.recommended_actions] or ["- 暂无"])
        if state.rework_risk and state.rework_risk.is_rework_suspected:
            lines.extend(["", "## 返修/误判风险"])
            lines.append(f"- 置信度：{state.rework_risk.confidence:.2f}")
            lines.extend([f"- 前次动作：{item}" for item in state.rework_risk.prior_actions] or ["- 前次动作：未明确"])
            lines.extend([f"- 无效动作：{item}" for item in state.rework_risk.ineffective_actions])
            lines.extend([f"- 避免重复：{item}" for item in state.rework_risk.avoided_repeat_actions])
            lines.extend([f"- 建议反证：{item}" for item in state.rework_risk.recommended_checks])
            lines.extend(
                [
                    f"- 相似案例：{item.similarity_signal} / {item.summary}"
                    for item in state.rework_risk.similar_cases
                ]
            )
            lines.extend([f"- 证据片段：{item}" for item in state.rework_risk.evidence_snippets])
        if state.fault_tree_generation_request:
            request = state.fault_tree_generation_request
            lines.extend(["", "## 动态故障树候选请求"])
            lines.append(f"- 请求ID：{request.request_id}")
            lines.append(f"- 审核状态：{request.review_status}")
            lines.append(f"- 候选入口现象：{request.candidate_start_symptom}")
            if request.candidate_failure_domain:
                lines.append(f"- 候选故障域：{request.candidate_failure_domain}")
            lines.extend([f"- 候选根因假设：{item}" for item in request.candidate_root_hypotheses])
            lines.extend([f"- 建议检查项：{item}" for item in request.candidate_tests])
            lines.append("- 约束：候选树未审核，不可生产放行；最终 FaultTree 必须由本体 transition 确定性重建。")
            if state.fault_tree_request_cluster:
                cluster = state.fault_tree_request_cluster
                lines.append(f"- 聚类ID：{cluster.cluster_id}")
                lines.append(f"- 聚类支持案例数：{cluster.support_count}/{cluster.min_support_for_review}")
                lines.append(f"- 推荐下一步：{cluster.recommended_next_step}")
        if state.tree_change_proposal:
            proposal = state.tree_change_proposal
            lines.extend(["", "## 已有树变更候选"])
            lines.append(f"- Proposal：{proposal.proposal_id}")
            lines.append(f"- 目标树：{proposal.target_tree_id or 'UNKNOWN'}")
            lines.append(f"- 变更类型：{', '.join(item.value for item in proposal.change_types) or '待确认'}")
            if proposal.change_summary:
                lines.append(f"- 变更摘要：{proposal.change_summary}")
            lines.append("- 约束：TREE_CHANGE 只作为版本化 patch 审核输入，不直接修改生产 TTL，不影响 Gate。")
        lines.extend(["", "## 风险与阻塞"])
        if state.gate_result:
            lines.extend([f"- 阻塞：{item}" for item in state.gate_result.blocking_reasons] or ["- 无"])
            lines.extend([f"- 待补充：{item}" for item in state.gate_result.required_actions])
        lines.extend([f"- 数据质量：{item}" for item in report.risk_notes] or ["- 无"])
        return "\n".join(lines)

    def _polish_with_llm(self, markdown: str) -> str:  # pragma: no cover - network/API dependent
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model=self.settings.openai_model, temperature=0)
        response = llm.invoke(
            [
                (
                    "system",
                    "Polish this Chinese diagnostic report for clarity. Do not change gate status, "
                    "root cause, evidence, or recommendations.",
                ),
                ("human", markdown),
            ]
        )
        return str(response.content)

    def _confirmed_cause(self, state: DiagnosticState) -> CandidateCause | None:
        if state.active_node_id:
            for cause in state.candidate_causes:
                if cause.cause_id == state.active_node_id:
                    return cause
        return state.candidate_causes[0] if state.candidate_causes else None

    def _recommended_actions(
        self,
        state: DiagnosticState,
        confirmed_cause: CandidateCause | None = None,
    ) -> list[str]:
        causes = (
            [confirmed_cause]
            if confirmed_cause and state.gate_result and state.gate_result.status == GateStatus.PASS
            else state.candidate_causes
        )
        actions: list[str] = []
        for cause in causes:
            for measure_id in cause.measure_ids:
                measure = self.repository.get_measure(measure_id)
                if measure:
                    actions.append(measure.name or measure.measure_id)
        return list(dict.fromkeys(actions))
