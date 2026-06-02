import json
from pathlib import Path

from ft_diag_agent.eval import (
    default_eval_cases,
    export_datasets,
    load_labeled_eval_cases_v1,
    run_eval_cases,
    write_eval_outputs,
)
from ft_diag_agent.fault_tree import RdfFaultTreeRepository
from ft_diag_agent.models import (
    CoverageStatus,
    DiagnosisMode,
    DiagnosticState,
    FaultTreeReviewStatus,
    GateStatus,
    ReplayRecord,
    WorkOrder,
)
from ft_diag_agent.rag import DocumentRag
from ft_diag_agent.settings import Settings
from ft_diag_agent.workflow import DiagnosticEngine

ROOT = Path(__file__).resolve().parents[1]
TTL = ROOT / "corrected_fault_tree_instances.ttl"
RAW_DOCS = ROOT / "data/raw_docs"


def test_export_empty_datasets(tmp_path: Path) -> None:
    summary = export_datasets([], tmp_path)

    assert summary["records"] == 0
    assert (tmp_path / "planner_sft.jsonl").exists()
    assert (tmp_path / "offline_eval_summary.json").exists()


def test_replay_record_schema() -> None:
    state = DiagnosticState()
    record = ReplayRecord(
        state_before=state.model_dump(mode="json"),
        state_after=state.model_dump(mode="json"),
        gate_result={"status": GateStatus.GRAY},
    )

    assert record.gate_result["status"] == GateStatus.GRAY


def test_export_datasets_clusters_dynamic_tree_requests_across_runs(tmp_path: Path) -> None:
    settings = Settings(
        fault_tree_ttl_path=TTL,
        raw_docs_dir=RAW_DOCS,
        chroma_dir=tmp_path / "chroma",
        runs_dir=tmp_path / "runs",
        datasets_dir=tmp_path / "datasets",
        llm_enable=False,
    )
    engine = DiagnosticEngine(
        RdfFaultTreeRepository(TTL),
        DocumentRag(RAW_DOCS, tmp_path / "chroma"),
        settings,
    )
    for index in range(3):
        state = engine.start_work_order(
            WorkOrder(
                order_id=f"WO-AC-CLUSTER-{index + 1:03d}",
                failure_phenomenon="空调制冷不足",
                business_domain="热管理",
                description="压缩机工作但出风温度偏高，用户反馈长时间行驶后仍不制冷。",
                executed_checks=["出风口温度高", "压缩机有工作声音"],
            ),
            DiagnosisMode.DEVELOPMENT,
        )
        assert state.coverage_decision
        assert state.coverage_decision.status == CoverageStatus.UNSUPPORTED
        assert state.fault_tree_generation_request

    summary = export_datasets(engine.replay_store.iter_records(), tmp_path / "datasets")
    cluster_path = tmp_path / "datasets" / "dynamic_tree_clusters.jsonl"
    rows = [json.loads(line) for line in cluster_path.read_text(encoding="utf-8").splitlines()]
    top_cluster = rows[0]

    assert summary["dynamic_tree_clusters"] == 1
    assert summary["dynamic_tree_review_ready_clusters"] == 1
    assert top_cluster["support_count"] == 3
    assert top_cluster["allowed_next_statuses"] == [FaultTreeReviewStatus.UNDER_REVIEW]
    assert len(top_cluster["request_ids"]) == 3
    assert "空调制冷不足" in top_cluster["representative_start_symptom"]


def test_rag_uses_project_local_index(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    chroma = tmp_path / "chroma"
    docs.mkdir()
    (docs / "sop_FT_002_door_close.md").write_text("T101 车门无法关闭 门锁检查", encoding="utf-8")

    rag = DocumentRag(docs, chroma)
    count = rag.build_index()
    evidence = rag.search("车门无法关闭", top_k=2)

    assert count >= 1
    assert chroma.exists()
    assert evidence


def test_rag_csv_reader_tolerates_bom_and_labeled_eval_csv(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    chroma = tmp_path / "chroma"
    docs.mkdir()
    source = RAW_DOCS / "diagnostic_eval_labeled_cases_v1" / "diagnostic_eval_cases_v1.csv"
    (docs / "diagnostic_eval_cases_v1.csv").write_text(source.read_text(encoding="utf-8-sig"), encoding="utf-8")

    rag = DocumentRag(docs, chroma)
    evidence = rag.search("EV-BS-007 更换屏幕无效", top_k=2, filters={"doc_type": "EVAL_CASE"})

    assert evidence
    joined = "\n".join(item.claim for item in evidence)
    assert "expected_final_leaf_id" not in joined
    assert "actual_repair_action" not in joined
    assert "human_review_conclusion" not in joined


def test_diagnostic_eval_runs_default_suite(tmp_path: Path) -> None:
    settings = Settings(
        fault_tree_ttl_path=TTL,
        raw_docs_dir=RAW_DOCS,
        chroma_dir=tmp_path / "chroma",
        runs_dir=tmp_path / "runs",
        datasets_dir=tmp_path / "datasets",
        llm_enable=False,
    )
    engine = DiagnosticEngine(
        RdfFaultTreeRepository(TTL),
        DocumentRag(RAW_DOCS, tmp_path / "chroma"),
        settings,
    )
    cases = default_eval_cases(RAW_DOCS)

    summary = run_eval_cases(engine, cases)
    paths = write_eval_outputs(summary, tmp_path / "eval_results")

    assert summary.cases == 21
    assert summary.coverage_accuracy == 1.0
    assert summary.tree_selection_accuracy == 1.0
    assert summary.final_leaf_accuracy == 1.0
    assert summary.gate_accuracy == 1.0
    assert summary.gate_mispass_count == 0
    assert summary.wrong_tree_misdiagnosis_count == 0
    assert summary.case_only_hypothesis_hit_rate == 1.0
    assert summary.next_action_hit_rate == 1.0
    assert Path(paths["summary"]).exists()
    assert Path(paths["results"]).exists()
    assert Path(paths["details"]).exists()


def test_diagnostic_eval_runs_labeled_v1_without_label_leakage(tmp_path: Path) -> None:
    settings = Settings(
        fault_tree_ttl_path=TTL,
        raw_docs_dir=RAW_DOCS,
        chroma_dir=tmp_path / "chroma",
        runs_dir=tmp_path / "runs",
        datasets_dir=tmp_path / "datasets",
        llm_enable=False,
    )
    engine = DiagnosticEngine(
        RdfFaultTreeRepository(TTL),
        DocumentRag(RAW_DOCS, tmp_path / "chroma"),
        settings,
    )
    cases = load_labeled_eval_cases_v1()

    assert len(cases) == 38
    assert all(not case.work_order.expected_leaf_symptom_id for case in cases if case.work_order)

    summary = run_eval_cases(engine, cases)

    assert summary.cases == 38
    assert summary.route_accuracy == 1.0
    assert summary.coverage_accuracy == 1.0
    assert summary.final_leaf_accuracy == 1.0
    assert summary.gate_accuracy == 1.0
    assert summary.production_gate_safety_rate == 1.0
    assert summary.rework_or_misdiagnosis_identification_rate == 1.0
    assert summary.gate_mispass_count == 0
    assert summary.guardrail_misroute_count == 0
    assert summary.group_metrics["NON_TREE_CASE_ONLY"]["next_action_hit_rate"] == 1.0
