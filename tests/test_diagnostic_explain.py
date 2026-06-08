from ft_diag_agent.diagnostic_explain import (
    build_diagnostic_timeline,
    build_evidence_summary,
    build_planner_gate_explanations,
)
from ft_diag_agent.models import (
    CoverageDecision,
    CoverageStatus,
    DiagnosisMode,
    DiagnosticAction,
    DiagnosticState,
    EvidenceItem,
    GateResult,
    GateStatus,
    WorkOrder,
)


def test_explanation_links_pending_action_to_gate_requirement() -> None:
    action = DiagnosticAction(
        action_type="FAULT_TREE_TEST",
        target_node_id="S101",
        target_cause_id="S101",
        test_id="T101",
        tool_name="human_input",
        priority=1,
        reason="需要确认门锁是否进入二道锁分支。",
    )
    state = DiagnosticState(
        work_order=WorkOrder(order_id="WO-1", failure_phenomenon="车门无法关闭"),
        coverage_decision=CoverageDecision(
            status=CoverageStatus.COVERED,
            diagnosis_mode=DiagnosisMode.PRODUCTION,
            tree_id="FT_002",
            reason="命中车门故障树。",
        ),
        active_tree_id="FT_002",
        candidate_paths=[],
        planned_actions=[action],
        gate_result=GateResult(
            status=GateStatus.GRAY,
            blocking_reasons=["存在未完成的关键检测项"],
            required_actions=["T101"],
            can_generate_final_report=True,
        ),
    )

    timeline = build_diagnostic_timeline(state)
    explanations = build_planner_gate_explanations(state)

    assert any(item.step == "Planner 检查动作" and item.status == "CURRENT" for item in timeline)
    assert any(item.step == "Gate 判定" and item.status == "CURRENT" for item in timeline)
    assert explanations[0].status == "PENDING"
    assert "Gate 要求补齐" in explanations[0].gate_effect


def test_explanation_summarizes_supporting_evidence() -> None:
    action = DiagnosticAction(
        action_type="FAULT_TREE_TEST",
        target_node_id="S105",
        target_cause_id="S105",
        test_id="T105",
        tool_name="human_input",
        priority=1,
        reason="确认执行器是否动作无力。",
    )
    evidence = EvidenceItem(
        evidence_id="E-HITL-1",
        source_type="HITL",
        source_id="T105",
        claim="人工检测 T105: 执行器动作无力",
        supports_node_id="S105",
        supports_cause_id="S105",
        strength=0.82,
    )
    state = DiagnosticState(
        planned_actions=[action],
        evidence_chain=[evidence],
        gate_result=GateResult(status=GateStatus.PASS, can_generate_final_report=True),
    )

    explanations = build_planner_gate_explanations(state)
    evidence_rows = build_evidence_summary(state)

    assert explanations[0].status == "SUPPORTED"
    assert explanations[0].evidence_ids == ["E-HITL-1"]
    assert "Gate 已 PASS" in explanations[0].gate_effect
    assert evidence_rows[0].supports == "节点 S105；根因 S105"
    assert "强支持" in evidence_rows[0].interpretation


def test_unsupported_production_coverage_is_blocked_in_timeline() -> None:
    state = DiagnosticState(
        work_order=WorkOrder(order_id="WO-AC", failure_phenomenon="空调不制冷"),
        coverage_decision=CoverageDecision(
            status=CoverageStatus.UNSUPPORTED,
            diagnosis_mode=DiagnosisMode.PRODUCTION,
            reason="现有发布树未覆盖热管理问题。",
        ),
        gate_result=GateResult(
            status=GateStatus.FAIL,
            blocking_reasons=["该工单故障类型不在现有故障树覆盖范围内"],
            required_actions=["补充对应故障树后再诊断"],
        ),
    )

    timeline = build_diagnostic_timeline(state)

    assert next(item for item in timeline if item.step == "分类与覆盖").status == "BLOCKED"
    assert next(item for item in timeline if item.step == "Gate 判定").status == "BLOCKED"
