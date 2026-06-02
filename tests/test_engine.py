from pathlib import Path

from ft_diag_agent.fault_tree import RdfFaultTreeRepository
from ft_diag_agent.models import (
    CoverageStatus,
    DiagnosisMode,
    FaultTreeReviewStatus,
    GateStatus,
    IntakeRequest,
    WorkOrder,
)
from ft_diag_agent.rag import DocumentRag
from ft_diag_agent.settings import Settings
from ft_diag_agent.workflow import DiagnosticEngine, build_langgraph_app

ROOT = Path(__file__).resolve().parents[1]
TTL = ROOT / "corrected_fault_tree_instances.ttl"
RAW_DOCS = ROOT / "data/raw_docs"


def build_engine(tmp_path: Path) -> DiagnosticEngine:
    settings = Settings(
        fault_tree_ttl_path=TTL,
        raw_docs_dir=tmp_path / "docs",
        chroma_dir=tmp_path / "chroma",
        runs_dir=tmp_path / "runs",
        datasets_dir=tmp_path / "datasets",
        openai_enable_llm=False,
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "case.txt").write_text("车门无法关闭 常见原因 门锁执行器损坏。", encoding="utf-8")
    repo = RdfFaultTreeRepository(TTL)
    rag = DocumentRag(settings.raw_docs_dir, settings.chroma_dir)
    return DiagnosticEngine(repo, rag, settings)


def test_engine_starts_case_and_plans(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    state = engine.start_case(IntakeRequest(raw_input="车门无法关闭"))

    assert state.work_order
    assert state.work_order.extraction_method == "SIMPLE_INPUT"
    assert state.coverage_decision
    assert state.coverage_decision.status == CoverageStatus.COVERED
    assert state.intake.phenomenon == "车门无法关闭"
    assert state.matched_trees[0].tree_id == "FT_002"
    assert state.active_tree_id == "FT_002"
    assert state.candidate_causes
    assert state.planned_actions
    assert state.gate_result.status == GateStatus.GRAY
    assert state.final_report
    assert state.replay_trace
    assert all(action.tool_name == "human_input" for action in state.planned_actions)
    assert build_langgraph_app(engine) is not None


def test_human_results_update_state(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    state = engine.start_case(IntakeRequest(raw_input="车门无法关闭"))
    action = state.planned_actions[0]

    state = engine.apply_human_test(
        state,
        {
            "test_id": action.test_id,
            "result": "检测完成，支持该分支",
            "passed": True,
            "supports_cause_id": action.target_cause_id,
            "supports_node_id": action.target_node_id,
            "strength": 0.8,
        },
    )

    assert action.test_id in {item.test_id for item in state.executed_tests}
    assert state.evidence_chain
    assert state.replay_trace


def test_start_work_order_classifies_and_advances_active_node(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    order = WorkOrder(
        order_id="WO-DR-260520-001",
        failure_phenomenon="车门无法关闭",
        title="左后门二道锁不上",
        description="左后门可推至关闭位置，但门锁不能进入二道锁。",
        executed_checks=[
            "T101: 关闭后无法锁止",
            "T102: 门锁无完整锁止声",
            "T105: 外部驱动执行器动作无力",
        ],
        expected_leaf_symptom_id="S105",
    )

    state = engine.start_work_order(order, DiagnosisMode.PRODUCTION)

    assert state.classification.coverage_status == CoverageStatus.COVERED
    assert state.active_tree_id == "FT_002"
    assert state.active_node_id == "S105"
    assert state.gate_result.status == GateStatus.PASS
    assert state.planned_actions
    assert state.planned_actions[0].action_type == "CONFIRMATION_CHECK"
    assert "二道锁" in state.planned_actions[0].reason


def test_report_uses_active_leaf_as_confirmed_root_cause(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    order = WorkOrder(
        order_id="WO-BS-260520-008",
        failure_phenomenon="车机黑屏",
        title="换屏后仍黑屏",
        description="维修站更换显示屏后仍黑屏，主机可被诊断。",
        executed_checks=["T007: 替换良品显示屏后显示恢复；原屏接入良品车仍黑屏。"],
        expected_leaf_symptom_id="S008",
    )

    state = engine.start_work_order(order, DiagnosisMode.PRODUCTION)

    assert state.active_node_id == "S008"
    assert state.gate_result.status == GateStatus.PASS
    assert state.final_report
    assert state.final_report.root_cause == "显示屏模组损坏"
    assert "更换显示屏模组" in state.final_report.recommended_actions
    assert state.planned_actions
    assert state.planned_actions[0].action_type == "CONFIRMATION_CHECK"
    assert "显示IC数据链路" in state.planned_actions[0].reason


def test_rework_guard_detects_prior_screen_replacement_misdiagnosis(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    order = WorkOrder(
        order_id="EV-BS-007",
        failure_phenomenon="车机黑屏",
        description="车机黑屏，维修站先更换屏幕无效，后发现主机主板短路。",
        executed_checks=["T010 3V3网络对地0.6Ω", "替换显示模组无改善", "主板替换后恢复"],
    )

    state = engine.start_work_order(order, DiagnosisMode.PRODUCTION)

    assert state.rework_risk
    assert state.rework_risk.is_rework_suspected
    assert state.rework_risk.is_prior_misdiagnosis_suspected
    assert any("显示屏" in item or "显示模组" in item for item in state.rework_risk.avoided_repeat_actions)
    assert state.planned_actions[0].action_type == "REWORK_COUNTER_CHECK"
    assert state.planned_actions[0].planner_source == "REWORK_GUARD"
    assert "主板" in state.planned_actions[0].reason or "阻抗" in state.planned_actions[0].reason
    assert state.final_report
    assert "返修/误判风险" in state.final_report.markdown


def test_rework_guard_detects_prior_striker_adjustment_misdiagnosis(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    order = WorkOrder(
        order_id="EV-DR-009",
        failure_phenomenon="车门无法关闭",
        description="售后先调整锁扣后返修，后确认密封条变形段干涉。",
        executed_checks=["T106复测合格", "T108密封条C段压缩量异常", "调整锁扣后仍复现"],
    )

    state = engine.start_work_order(order, DiagnosisMode.PRODUCTION)

    assert state.rework_risk
    assert state.rework_risk.is_rework_suspected
    assert state.rework_risk.is_prior_misdiagnosis_suspected
    assert any("锁扣" in item for item in state.rework_risk.avoided_repeat_actions)
    assert state.planned_actions[0].action_type == "REWORK_COUNTER_CHECK"
    assert "密封条" in state.planned_actions[0].reason
    assert state.final_report
    assert "识别到前次处置无效" in state.final_report.markdown


def test_rework_guard_uses_sanitized_similar_history_without_label_leakage(tmp_path: Path) -> None:
    settings = Settings(
        fault_tree_ttl_path=TTL,
        raw_docs_dir=RAW_DOCS,
        chroma_dir=tmp_path / "chroma",
        runs_dir=tmp_path / "runs",
        datasets_dir=tmp_path / "datasets",
        openai_enable_llm=False,
    )
    engine = DiagnosticEngine(RdfFaultTreeRepository(TTL), DocumentRag(RAW_DOCS, tmp_path / "chroma"), settings)
    order = WorkOrder(
        order_id="WO-HIST-BS-001",
        failure_phenomenon="车机黑屏",
        description="维修站准备再次更换屏幕，主机可诊断，需先确认是否存在历史返修风险。",
        executed_checks=["T003 主机在线", "屏幕仍黑屏"],
    )

    state = engine.start_work_order(order, DiagnosisMode.PRODUCTION)

    assert state.rework_risk
    assert state.rework_risk.similar_cases
    assert any("主板" in item for item in state.rework_risk.recommended_checks)
    assert state.planned_actions[0].action_type == "REWORK_COUNTER_CHECK"
    assert state.planned_actions[0].source_refs
    joined = "\n".join(case.summary for case in state.rework_risk.similar_cases)
    assert "expected_final_leaf_id" not in joined
    assert "actual_repair_action" not in joined
    assert "人工复核" not in joined


def test_production_unsupported_fails(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    order = WorkOrder(
        order_id="WO-UN-260520-001",
        failure_phenomenon="空调制冷不足",
        description="压缩机工作但出风温度偏高。",
    )

    state = engine.start_work_order(order, DiagnosisMode.PRODUCTION)

    assert state.coverage_decision.status == CoverageStatus.UNSUPPORTED
    assert state.gate_result.status == GateStatus.FAIL
    assert not state.planned_actions
    assert not state.evidence_chain
    assert state.fault_tree_generation_request is None


def test_direct_fallback_routes_production_unsupported_without_planning(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    engine._graph = None
    order = WorkOrder(
        order_id="WO-UN-DIRECT-001",
        failure_phenomenon="空调制冷不足",
        description="压缩机工作但出风温度偏高。",
    )

    state = engine.start_work_order(order, DiagnosisMode.PRODUCTION)

    assert state.coverage_decision.status == CoverageStatus.UNSUPPORTED
    assert state.gate_result.status == GateStatus.FAIL
    assert not state.planned_actions
    assert not state.evidence_chain
    assert state.fault_tree_generation_request is None


def test_direct_fallback_routes_development_unsupported_to_case_only(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    engine._graph = None
    order = WorkOrder(
        order_id="WO-UN-DIRECT-002",
        failure_phenomenon="空调制冷不足",
        description="压缩机工作但出风温度偏高。",
    )

    state = engine.start_work_order(order, DiagnosisMode.DEVELOPMENT)

    assert state.coverage_decision.status == CoverageStatus.UNSUPPORTED
    assert state.diagnosis_mode == DiagnosisMode.CASE_ONLY_EXPLORATORY
    assert state.gate_result.status == GateStatus.GRAY
    assert state.planned_actions
    assert state.case_only_plan
    assert state.fault_tree_generation_request
    assert state.fault_tree_generation_request.review_status == FaultTreeReviewStatus.DRAFT_REQUESTED
    assert state.fault_tree_request_cluster
    assert state.fault_tree_request_cluster.review_status == FaultTreeReviewStatus.DRAFT_REQUESTED
    assert state.fault_tree_request_cluster.source_case_ids == [state.case_id]


def test_development_unsupported_is_exploratory(tmp_path: Path) -> None:
    engine = build_engine(tmp_path)
    order = WorkOrder(
        order_id="WO-UN-260520-002",
        failure_phenomenon="空调制冷不足",
        description="压缩机工作但出风温度偏高。",
    )

    state = engine.start_work_order(order, DiagnosisMode.DEVELOPMENT)

    assert state.coverage_decision.status == CoverageStatus.UNSUPPORTED
    assert state.diagnosis_mode == DiagnosisMode.CASE_ONLY_EXPLORATORY
    assert state.gate_result.status == GateStatus.GRAY
    assert state.planned_actions
    assert state.planned_actions[0].action_type == "CASE_ONLY_HITL"
    assert state.planned_actions[0].tool_name == "human_input"
    assert state.planned_actions[0].planner_source in {"RULE", "LLM"}
    assert state.planned_actions[0].confidence > 0
    assert state.planned_actions[0].risk_notes
    assert state.case_only_plan
    assert state.case_only_hypotheses
    assert state.fault_tree_generation_request
    assert state.fault_tree_generation_request.candidate_start_symptom
    assert state.fault_tree_generation_request.candidate_root_hypotheses
    assert state.fault_tree_generation_request.candidate_tests
    assert any(
        "不要让 LLM 直接输出最终 FaultTree" in item
        for item in state.fault_tree_generation_request.ontology_build_constraints
    )
    assert state.fault_tree_generation_request.draft
    assert state.fault_tree_request_cluster
    assert state.fault_tree_request_cluster.cluster_id.startswith("FTC-")
    assert state.fault_tree_request_cluster.support_count >= 1
    assert state.fault_tree_request_cluster.recommended_next_step
    assert len(state.planned_actions) >= 3

    action = state.planned_actions[0]
    state = engine.apply_human_test(
        state,
        {
            "test_id": action.test_id,
            "result": "已补充探索性检查，疑似压缩机制冷回路问题。",
            "passed": True,
            "supports_cause_id": action.target_cause_id,
            "strength": 0.95,
            "notes": "开发态探索样例",
        },
    )

    assert state.gate_result.status == GateStatus.GRAY
    assert state.final_report
    assert state.final_report.root_cause is None
    assert state.case_only_findings
    assert state.fault_tree_generation_request
    assert state.fault_tree_generation_request.work_order_id == "WO-UN-260520-002"
    assert state.fault_tree_request_cluster
    assert "动态故障树候选请求" in state.final_report.markdown
    assert "聚类ID" in state.final_report.markdown
    assert any("不可生产 PASS" in note for note in state.gate_result.risk_notes)
