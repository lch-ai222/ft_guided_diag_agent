from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ft_diag_agent.dynamic_tree import dynamic_tree_cluster_rows, merge_dynamic_tree_clusters
from ft_diag_agent.fault_tree import RdfFaultTreeRepository
from ft_diag_agent.models import (
    CoverageStatus,
    DiagnosisMode,
    GateStatus,
    ReplayRecord,
    WorkOrder,
    utc_now_iso,
)
from ft_diag_agent.rag import DocumentRag
from ft_diag_agent.settings import Settings
from ft_diag_agent.work_orders import parse_pasted_work_order_text, parse_work_order_files
from ft_diag_agent.workflow import DiagnosticEngine


class EvalCase(BaseModel):
    case_id: str
    eval_group: str | None = None
    description: str | None = None
    input_type: Literal["work_order", "raw_text"] = "work_order"
    diagnosis_mode: DiagnosisMode = DiagnosisMode.PRODUCTION
    work_order: WorkOrder | None = None
    raw_text: str | None = None
    expected_route: str | None = None
    expected_coverage: CoverageStatus | None = None
    expected_tree_id: str | None = None
    expected_leaf_symptom_id: str | None = None
    expected_gate_status: GateStatus | None = None
    expected_business_outcome: GateStatus | None = None
    expected_root_cause: str | None = None
    expected_case_only_hypothesis_hit: bool | None = None
    expected_next_action_hit_text: str | None = None
    expected_hypothesis_keywords: list[str] = Field(default_factory=list)
    expected_action_keywords: list[str] = Field(default_factory=list)
    is_rework: bool = False
    is_prior_misdiagnosis: bool = False
    annotation_notes: str | None = None


class EvalCaseResult(BaseModel):
    case_id: str
    eval_group: str | None = None
    description: str | None = None
    diagnosis_mode: DiagnosisMode
    expected_route: str | None = None
    expected_tree_id: str | None = None
    expected_leaf_symptom_id: str | None = None
    expected_gate_status: GateStatus | None = None
    expected_next_action_hit_text: str | None = None
    expected_action_keywords: list[str] = Field(default_factory=list)
    predicted_route: str | None = None
    coverage_status: CoverageStatus | None = None
    tree_id: str | None = None
    active_node_id: str | None = None
    gate_status: GateStatus | None = None
    expected_business_outcome: GateStatus | None = None
    production_gate_safe: bool | None = None
    route_correct: bool | None = None
    coverage_correct: bool | None = None
    tree_correct: bool | None = None
    leaf_correct: bool | None = None
    gate_correct: bool | None = None
    hypothesis_hit: bool | None = None
    next_action_hit: bool | None = None
    reject_correct: bool | None = None
    rework_or_misdiagnosis_identified: bool | None = None
    gate_mispass: bool = False
    guardrail_misroute: bool = False
    wrong_tree_misdiagnosis: bool = False
    failure_tags: list[str] = Field(default_factory=list)
    short_error_reason: str | None = None
    executed_tests: list[dict[str, Any]] = Field(default_factory=list)
    evidence_summary: list[dict[str, Any]] = Field(default_factory=list)
    planned_actions: list[dict[str, Any]] = Field(default_factory=list)
    case_only_hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    rework_risk: dict[str, Any] | None = None
    risk_notes: list[str] = Field(default_factory=list)
    replay_trace: list[dict[str, Any]] = Field(default_factory=list)


class EvalSuiteSummary(BaseModel):
    cases: int
    covered_cases: int
    unsupported_cases: int
    coverage_accuracy: float | None = None
    route_accuracy: float | None = None
    tree_selection_accuracy: float | None = None
    final_leaf_accuracy: float | None = None
    gate_accuracy: float | None = None
    production_gate_safety_rate: float | None = None
    case_only_hypothesis_hit_rate: float | None = None
    next_action_hit_rate: float | None = None
    reject_accuracy: float | None = None
    rework_or_misdiagnosis_identification_rate: float | None = None
    gate_mispass_count: int = 0
    guardrail_misroute_count: int = 0
    wrong_tree_misdiagnosis_count: int = 0
    group_metrics: dict[str, dict[str, Any]] = Field(default_factory=dict)
    results: list[EvalCaseResult] = Field(default_factory=list)


class EvalRunMetadata(BaseModel):
    run_id: str
    suite: str
    created_at: str = Field(default_factory=utc_now_iso)
    cases: int = 0
    config: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class EvalConfusionRow(BaseModel):
    dimension: Literal["tree", "node", "test"]
    expected: str
    predicted: str
    count: int
    case_ids: list[str] = Field(default_factory=list)
    failure_tags: list[str] = Field(default_factory=list)


class EvalConfusionReport(BaseModel):
    tree: list[EvalConfusionRow] = Field(default_factory=list)
    node: list[EvalConfusionRow] = Field(default_factory=list)
    test: list[EvalConfusionRow] = Field(default_factory=list)


class EvalMetricDelta(BaseModel):
    metric: str
    baseline: float | int | None = None
    current: float | int | None = None
    delta: float | int | None = None
    status: Literal["IMPROVED", "REGRESSED", "STABLE", "INFO"] = "INFO"
    affected_case_ids: list[str] = Field(default_factory=list)


class EvalRunComparison(BaseModel):
    baseline_run_id: str
    current_run_id: str
    metric_deltas: list[EvalMetricDelta] = Field(default_factory=list)
    regressions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    newly_failed_cases: list[str] = Field(default_factory=list)
    resolved_failed_cases: list[str] = Field(default_factory=list)


class EvalRunArtifact(BaseModel):
    metadata: EvalRunMetadata
    summary: EvalSuiteSummary
    confusion: EvalConfusionReport


POWERTRAIN_UNSUPPORTED_CASE = """## 工单 01｜MOCK-NFT-01-01｜车辆加速无力/动力受限

### 基本信息

- 工单编号：`MOCK-NFT-01-01`
- VIN：`LXMOCKPWR001001`
- 创建时间：2026-05-10 09:20:00
- 车型/工厂：X 平台 / 常州工厂
- 业务域：动力系统
- 故障标签：动力受限
- 期望路由：`NON_TREE_DIAGNOSIS`
- 期望故障树：`NONE`
- 证据充分性：`SUFFICIENT`

### 用户/现场描述

仪表提示“动力受限，请安全停车”，SOC 62%，无黑屏，无车门闭合抱怨。

### 初步检查记录

- 读取 VCU/BMS/DCDC DTC
- 检查高压互锁状态
- 复核加速踏板开度与电机扭矩请求一致性

### 现场发现

P1A0B-VCU 扭矩降额；BMS 单体压差瞬时升高
"""

LABELED_EVAL_V1_PATH = Path("data/raw_docs/diagnostic_eval_labeled_cases_v1/diagnostic_eval_cases_v1.jsonl")
EVAL_TREND_METRICS = [
    "coverage_accuracy",
    "route_accuracy",
    "tree_selection_accuracy",
    "final_leaf_accuracy",
    "gate_accuracy",
    "production_gate_safety_rate",
    "case_only_hypothesis_hit_rate",
    "next_action_hit_rate",
    "reject_accuracy",
    "rework_or_misdiagnosis_identification_rate",
    "gate_mispass_count",
    "guardrail_misroute_count",
    "wrong_tree_misdiagnosis_count",
]
EVAL_LOWER_IS_BETTER = {
    "gate_mispass_count",
    "guardrail_misroute_count",
    "wrong_tree_misdiagnosis_count",
}
EVAL_CRITICAL_REGRESSION_METRICS = {
    "production_gate_safety_rate",
    "gate_mispass_count",
    "wrong_tree_misdiagnosis_count",
}
EVAL_METRIC_FIELDS = {
    "coverage_accuracy": "coverage_correct",
    "route_accuracy": "route_correct",
    "tree_selection_accuracy": "tree_correct",
    "final_leaf_accuracy": "leaf_correct",
    "gate_accuracy": "gate_correct",
    "production_gate_safety_rate": "production_gate_safe",
    "case_only_hypothesis_hit_rate": "hypothesis_hit",
    "next_action_hit_rate": "next_action_hit",
    "reject_accuracy": "reject_correct",
    "rework_or_misdiagnosis_identification_rate": "rework_or_misdiagnosis_identified",
}


def load_replay_records(runs_dir: str | Path) -> list[ReplayRecord]:
    records: list[ReplayRecord] = []
    for path in sorted(Path(runs_dir).glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    records.append(ReplayRecord.model_validate(json.loads(line)))
    return records


def export_datasets(records: list[ReplayRecord], datasets_dir: str | Path) -> dict[str, Any]:
    out = Path(datasets_dir)
    out.mkdir(parents=True, exist_ok=True)
    dynamic_clusters = merge_dynamic_tree_clusters(records)
    dynamic_cluster_count = _write_jsonl(
        out / "dynamic_tree_clusters.jsonl",
        dynamic_tree_cluster_rows(dynamic_clusters),
    )
    planner_count = _write_jsonl(
        out / "planner_sft.jsonl",
        [
            {
                "instruction": "Given the diagnostic state, generate the next diagnostic actions.",
                "input": record.state_before,
                "output": record.planner_output,
            }
            for record in records
            if record.planner_output
        ],
    )
    report_count = _write_jsonl(
        out / "report_sft.jsonl",
        [
            {
                "instruction": "Generate a traceable diagnostic report from the final state.",
                "input": record.state_after,
                "output": record.state_after.get("final_report"),
            }
            for record in records
            if record.state_after.get("final_report")
        ],
    )
    preference_count = _write_jsonl(
        out / "preference_pairs.jsonl",
        [
            {
                "prompt": record.state_before,
                "chosen": record.planner_output if record.accepted else record.human_decision,
                "rejected": record.human_decision if record.accepted else record.planner_output,
                "rejected_reason": record.rejected_reason,
            }
            for record in records
            if record.accepted is not None and record.human_decision
        ],
    )
    summary = {
        "records": len(records),
        "planner_sft": planner_count,
        "report_sft": report_count,
        "preference_pairs": preference_count,
        "dynamic_tree_clusters": dynamic_cluster_count,
        "dynamic_tree_review_ready_clusters": sum(
            1 for cluster in dynamic_clusters if cluster.allowed_next_statuses
        ),
        "gate_pass": sum(
            1 for record in records if record.gate_result.get("status") == "PASS"
        ),
        "gate_gray": sum(
            1 for record in records if record.gate_result.get("status") == "GRAY"
        ),
        "gate_fail": sum(
            1 for record in records if record.gate_result.get("status") == "FAIL"
        ),
        "tree_selection_accuracy": _tree_selection_accuracy(records),
        "unsupported_count": sum(
            1
            for record in records
            if _nested(record.state_after, "coverage_decision", "status") == "UNSUPPORTED"
        ),
        "final_leaf_accuracy": _final_leaf_accuracy(records),
        "wrong_tree_misdiagnosis_count": _wrong_tree_misdiagnosis_count(records),
    }
    (out / "offline_eval_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def default_eval_cases(raw_docs_dir: str | Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for order in parse_work_order_files(raw_docs_dir):
        expected_tree = _expected_tree_from_leaf(order.expected_leaf_symptom_id)
        cases.append(
            EvalCase(
                case_id=order.order_id,
                description=order.title or order.failure_phenomenon,
                diagnosis_mode=DiagnosisMode.PRODUCTION,
                work_order=order,
                expected_coverage=CoverageStatus.COVERED,
                expected_tree_id=expected_tree,
                expected_leaf_symptom_id=order.expected_leaf_symptom_id,
                expected_gate_status=_expected_gate_from_order(order),
            )
        )
    cases.append(
        EvalCase(
            case_id="MOCK-NFT-01-01",
            description="非故障树覆盖：动力受限开发态探索",
            input_type="raw_text",
            diagnosis_mode=DiagnosisMode.DEVELOPMENT,
            raw_text=POWERTRAIN_UNSUPPORTED_CASE,
            expected_coverage=CoverageStatus.UNSUPPORTED,
            expected_gate_status=GateStatus.GRAY,
            expected_hypothesis_keywords=["BMS", "VCU", "扭矩"],
            expected_action_keywords=["冻结帧", "单体", "扭矩"],
        )
    )
    return cases


def load_labeled_eval_cases_v1(path: str | Path | None = None) -> list[EvalCase]:
    source = Path(path) if path else LABELED_EVAL_V1_PATH
    cases: list[EvalCase] = []
    with source.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            cases.append(_labeled_row_to_eval_case(row))
    return cases


def load_eval_cases(path: str | Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                cases.append(EvalCase.model_validate(json.loads(line)))
    return cases


def _labeled_row_to_eval_case(row: dict[str, Any]) -> EvalCase:
    expected_route = _none_if_marker(row.get("expected_route"))
    expected_tree = _none_if_marker(row.get("expected_tree_id"))
    expected_leaf = _none_if_marker(row.get("expected_final_leaf_id"))
    expected_gate = _gate_or_none(row.get("expected_gate"))
    business_outcome = _gate_or_none(row.get("repair_validation_result")) or expected_gate
    scoring_gate = (
        GateStatus.GRAY
        if expected_route == "CASE_ONLY_DIAGNOSIS" and expected_gate == GateStatus.PASS
        else expected_gate
    )
    mode = DiagnosisMode.PRODUCTION if expected_route == "TREE_DIAGNOSIS" else DiagnosisMode.DEVELOPMENT
    if expected_route == "REJECT_OR_NEED_MORE_EVIDENCE":
        mode = DiagnosisMode.PRODUCTION
    work_order = WorkOrder(
        order_id=str(row["case_id"]),
        title=_none_if_marker(row.get("failure_type")),
        failure_phenomenon=str(row.get("failure_type") or row.get("case_description") or ""),
        vehicle_project=_none_if_marker(row.get("vehicle_project")),
        business_domain=_none_if_marker(row.get("domain")),
        source=_none_if_marker(row.get("source")),
        severity=_none_if_marker(row.get("severity")),
        description=_none_if_marker(row.get("case_description")),
        executed_checks=_split_evidence(str(row.get("observed_evidence") or "")),
        raw_text="\n".join(
            part
            for part in [
                str(row.get("failure_type") or ""),
                str(row.get("case_description") or ""),
                str(row.get("observed_evidence") or ""),
            ]
            if part
        ),
        extraction_method="LABELED_EVAL_V1",
        source_path=str(row.get("_source_path") or LABELED_EVAL_V1_PATH),
    )
    return EvalCase(
        case_id=str(row["case_id"]),
        eval_group=_none_if_marker(row.get("eval_group")),
        description=_none_if_marker(row.get("case_description")),
        diagnosis_mode=mode,
        work_order=work_order,
        expected_route=expected_route,
        expected_coverage=CoverageStatus.COVERED if expected_route == "TREE_DIAGNOSIS" else CoverageStatus.UNSUPPORTED,
        expected_tree_id=expected_tree,
        expected_leaf_symptom_id=expected_leaf,
        expected_gate_status=scoring_gate,
        expected_business_outcome=business_outcome,
        expected_root_cause=_none_if_marker(row.get("expected_final_root_cause")),
        expected_case_only_hypothesis_hit=_bool_or_none(row.get("expected_case_only_hypothesis_hit")),
        expected_next_action_hit_text=_none_if_marker(row.get("expected_next_action_hit")),
        expected_hypothesis_keywords=(
            _keywords_from_text(row.get("expected_final_root_cause"))
            if _bool_or_none(row.get("expected_case_only_hypothesis_hit"))
            else []
        ),
        expected_action_keywords=_keywords_from_text(row.get("expected_next_action_hit")),
        is_rework=bool(_bool_or_none(row.get("is_rework"))),
        is_prior_misdiagnosis=bool(_bool_or_none(row.get("is_prior_misdiagnosis"))),
        annotation_notes=_none_if_marker(row.get("annotation_notes")),
    )


def run_eval_cases(engine: DiagnosticEngine, cases: list[EvalCase]) -> EvalSuiteSummary:
    results = [_run_eval_case(engine, case) for case in cases]
    return EvalSuiteSummary(
        cases=len(results),
        covered_cases=sum(1 for result in results if result.coverage_status == CoverageStatus.COVERED),
        unsupported_cases=sum(1 for result in results if result.coverage_status == CoverageStatus.UNSUPPORTED),
        coverage_accuracy=_ratio(result.coverage_correct for result in results),
        route_accuracy=_ratio(result.route_correct for result in results),
        tree_selection_accuracy=_ratio(result.tree_correct for result in results),
        final_leaf_accuracy=_ratio(result.leaf_correct for result in results),
        gate_accuracy=_ratio(result.gate_correct for result in results),
        production_gate_safety_rate=_ratio(result.production_gate_safe for result in results),
        case_only_hypothesis_hit_rate=_ratio(result.hypothesis_hit for result in results),
        next_action_hit_rate=_ratio(result.next_action_hit for result in results),
        reject_accuracy=_ratio(result.reject_correct for result in results),
        rework_or_misdiagnosis_identification_rate=_ratio(
            result.rework_or_misdiagnosis_identified for result in results
        ),
        gate_mispass_count=sum(1 for result in results if result.gate_mispass),
        guardrail_misroute_count=sum(1 for result in results if result.guardrail_misroute),
        wrong_tree_misdiagnosis_count=sum(1 for result in results if result.wrong_tree_misdiagnosis),
        group_metrics=_group_metrics(results),
        results=results,
    )


def write_eval_outputs(summary: EvalSuiteSummary, output_dir: str | Path) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "diagnostic_eval_summary.json"
    results_path = out / "diagnostic_eval_results.jsonl"
    details_path = out / "diagnostic_eval_details.jsonl"
    summary_without_rows = summary.model_copy(update={"results": []})
    summary_path.write_text(
        json.dumps(summary_without_rows.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_jsonl(
        results_path,
        [result.model_dump(mode="json") for result in summary.results],
    )
    _write_jsonl(
        details_path,
        [_eval_detail_row(result) for result in summary.results],
    )
    return {
        "summary": str(summary_path),
        "results": str(results_path),
        "details": str(details_path),
    }


def write_eval_run(
    summary: EvalSuiteSummary,
    eval_runs_dir: str | Path,
    suite: str,
    *,
    run_id: str | None = None,
    config: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> EvalRunArtifact:
    metadata = EvalRunMetadata(
        run_id=run_id or _default_eval_run_id(suite),
        suite=suite,
        cases=summary.cases,
        config=config or {},
        notes=notes or [],
    )
    artifact = EvalRunArtifact(
        metadata=metadata,
        summary=summary,
        confusion=build_eval_confusion(summary.results),
    )
    run_dir = Path(eval_runs_dir) / metadata.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_without_rows = summary.model_copy(update={"results": []})
    (run_dir / "run_metadata.json").write_text(
        json.dumps(metadata.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(summary_without_rows.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_jsonl(run_dir / "results.jsonl", [result.model_dump(mode="json") for result in summary.results])
    _write_jsonl(run_dir / "details.jsonl", [_eval_detail_row(result) for result in summary.results])
    confusion = artifact.confusion
    (run_dir / "confusion_tree.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in confusion.tree], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "confusion_node.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in confusion.node], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "confusion_test.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in confusion.test], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return artifact


def list_eval_runs(eval_runs_dir: str | Path) -> list[EvalRunMetadata]:
    root = Path(eval_runs_dir)
    if not root.exists():
        return []
    runs: list[EvalRunMetadata] = []
    for metadata_path in sorted(root.glob("*/run_metadata.json")):
        try:
            runs.append(EvalRunMetadata.model_validate(json.loads(metadata_path.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, ValueError):
            continue
    return sorted(runs, key=lambda item: item.created_at, reverse=True)


def load_eval_run(eval_runs_dir: str | Path, run_id: str) -> EvalRunArtifact:
    run_dir = Path(eval_runs_dir) / run_id
    metadata = EvalRunMetadata.model_validate(
        json.loads((run_dir / "run_metadata.json").read_text(encoding="utf-8"))
    )
    summary = EvalSuiteSummary.model_validate(json.loads((run_dir / "summary.json").read_text(encoding="utf-8")))
    results = _read_jsonl(run_dir / "results.jsonl")
    summary.results = [EvalCaseResult.model_validate(item) for item in results]
    confusion = EvalConfusionReport(
        tree=[
            EvalConfusionRow.model_validate(item)
            for item in _read_json_array(run_dir / "confusion_tree.json")
        ],
        node=[
            EvalConfusionRow.model_validate(item)
            for item in _read_json_array(run_dir / "confusion_node.json")
        ],
        test=[
            EvalConfusionRow.model_validate(item)
            for item in _read_json_array(run_dir / "confusion_test.json")
        ],
    )
    return EvalRunArtifact(metadata=metadata, summary=summary, confusion=confusion)


def build_eval_confusion(results: list[EvalCaseResult]) -> EvalConfusionReport:
    return EvalConfusionReport(
        tree=_confusion_rows(
            "tree",
            [(_label(result.expected_tree_id), _label(result.tree_id), result) for result in results],
        ),
        node=_confusion_rows(
            "node",
            [
                (
                    _label(result.expected_leaf_symptom_id),
                    _label(result.active_node_id),
                    result,
                )
                for result in results
            ],
        ),
        test=_confusion_rows(
            "test",
            [
                (
                    _expected_test_label(result),
                    _planned_test_label(result.planned_actions),
                    result,
                )
                for result in results
            ],
        ),
    )


def compare_eval_runs(baseline: EvalRunArtifact, current: EvalRunArtifact) -> EvalRunComparison:
    baseline_failed = {item.case_id for item in baseline.summary.results if item.failure_tags}
    current_failed = {item.case_id for item in current.summary.results if item.failure_tags}
    metric_deltas = [
        _metric_delta(metric, baseline.summary, current.summary, baseline.summary.results, current.summary.results)
        for metric in EVAL_TREND_METRICS
    ]
    regressions: list[str] = []
    warnings: list[str] = []
    for item in metric_deltas:
        if item.status != "REGRESSED":
            continue
        message = f"{item.metric}: {item.baseline} -> {item.current}"
        if item.metric in EVAL_CRITICAL_REGRESSION_METRICS:
            regressions.append(message)
        else:
            warnings.append(message)
    return EvalRunComparison(
        baseline_run_id=baseline.metadata.run_id,
        current_run_id=current.metadata.run_id,
        metric_deltas=metric_deltas,
        regressions=regressions,
        warnings=warnings,
        newly_failed_cases=sorted(current_failed - baseline_failed),
        resolved_failed_cases=sorted(baseline_failed - current_failed),
    )


def run_diagnostic_eval(
    engine: DiagnosticEngine,
    cases_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    suite: str = "default",
    eval_runs_dir: str | Path | None = None,
) -> EvalSuiteSummary:
    if cases_path:
        cases = load_eval_cases(cases_path)
    elif suite == "labeled_v1":
        cases = load_labeled_eval_cases_v1()
    else:
        cases = default_eval_cases(engine.settings.raw_docs_dir)
    summary = run_eval_cases(engine, cases)
    if output_dir:
        write_eval_outputs(summary, output_dir)
    if eval_runs_dir:
        write_eval_run(
            summary,
            eval_runs_dir,
            suite,
            config={
                "cases_path": str(cases_path) if cases_path else None,
                "output_dir": str(output_dir) if output_dir else None,
            },
        )
    return summary


def _run_eval_case(engine: DiagnosticEngine, case: EvalCase) -> EvalCaseResult:
    work_order = case.work_order
    if case.input_type == "raw_text":
        work_order = parse_pasted_work_order_text(case.raw_text or "", settings=engine.settings)
    if not work_order:
        raise ValueError(f"Eval case {case.case_id} has no usable work order input.")

    state = engine.start_work_order(work_order, case.diagnosis_mode)
    coverage_status = state.coverage_decision.status if state.coverage_decision else None
    gate_status = state.gate_result.status if state.gate_result else None
    predicted_route = _predicted_route(state, coverage_status, gate_status)
    planned_actions = [action.model_dump(mode="json") for action in state.planned_actions]
    hypotheses = [hypothesis.model_dump(mode="json") for hypothesis in state.case_only_hypotheses]
    gate_mispass = (
        gate_status == GateStatus.PASS
        and (
            case.expected_route in {"CASE_ONLY_DIAGNOSIS", "REJECT_OR_NEED_MORE_EVIDENCE"}
            or case.expected_gate_status in {GateStatus.GRAY, GateStatus.FAIL}
        )
    )
    wrong_tree_misdiagnosis = (
        case.expected_tree_id not in {None, "NONE", "FT_002_OR_CASE_ONLY"}
        and state.active_tree_id is not None
        and not _expected_match(state.active_tree_id, case.expected_tree_id)
        and gate_status == GateStatus.PASS
    )
    text_for_hypotheses = " ".join(
        [
            *[
                " ".join(
                    [
                        item.get("system_area") or "",
                        item.get("component") or "",
                        item.get("failure_mode") or "",
                        item.get("rationale") or "",
                    ]
                )
                for item in hypotheses
            ],
            *[item.get("reason") or "" for item in planned_actions],
        ]
    )
    text_for_actions = " ".join(
        [
            text_for_hypotheses,
            *[
                " ".join(
                    [
                        item.test_id,
                        item.result,
                        str(item.value or ""),
                        item.notes or "",
                    ]
                )
                for item in state.executed_tests
            ],
            *[item.claim for item in state.evidence_chain],
            *[
                " ".join(
                    [
                        item.get("test_id") or "",
                        item.get("reason") or "",
                        " ".join(item.get("risk_notes") or []),
                    ]
                )
                for item in planned_actions
            ],
        ]
    )
    all_decision_text = " ".join(
        [
            text_for_hypotheses,
            text_for_actions,
            " ".join(state.data_quality_notes),
            " ".join(state.gate_result.blocking_reasons if state.gate_result else []),
            " ".join(state.gate_result.required_actions if state.gate_result else []),
            " ".join(state.gate_result.risk_notes if state.gate_result else []),
            state.final_report.markdown if state.final_report else "",
        ]
    )
    production_gate_safe = _production_gate_safe(case, gate_status)
    route_correct = _route_correct(predicted_route, case, state)
    leaf_correct = _expected_match_or_none(state.active_node_id, case.expected_leaf_symptom_id)
    gate_correct = _equals_if_expected(gate_status, case.expected_gate_status)
    next_action_hit = _keyword_hit(text_for_actions, case.expected_action_keywords)
    failure_tags = _failure_tags(
        route_correct=route_correct,
        coverage_correct=_coverage_correct(coverage_status, case),
        tree_correct=_expected_match_or_none(state.active_tree_id, case.expected_tree_id),
        leaf_correct=leaf_correct,
        gate_correct=gate_correct,
        next_action_hit=next_action_hit,
        gate_mispass=gate_mispass,
        wrong_tree_misdiagnosis=wrong_tree_misdiagnosis,
    )
    return EvalCaseResult(
        case_id=case.case_id,
        eval_group=case.eval_group,
        description=case.description,
        diagnosis_mode=case.diagnosis_mode,
        expected_route=case.expected_route,
        expected_tree_id=case.expected_tree_id,
        expected_leaf_symptom_id=case.expected_leaf_symptom_id,
        expected_gate_status=case.expected_gate_status,
        expected_next_action_hit_text=case.expected_next_action_hit_text,
        expected_action_keywords=case.expected_action_keywords,
        predicted_route=predicted_route,
        coverage_status=coverage_status,
        tree_id=state.active_tree_id,
        active_node_id=state.active_node_id,
        gate_status=gate_status,
        expected_business_outcome=case.expected_business_outcome,
        production_gate_safe=production_gate_safe,
        route_correct=route_correct,
        coverage_correct=_coverage_correct(coverage_status, case),
        tree_correct=_expected_match_or_none(state.active_tree_id, case.expected_tree_id),
        leaf_correct=leaf_correct,
        gate_correct=gate_correct,
        hypothesis_hit=_expected_keyword_hit(
            text_for_hypotheses,
            case.expected_hypothesis_keywords,
            case.expected_case_only_hypothesis_hit,
        ),
        next_action_hit=next_action_hit,
        reject_correct=_reject_correct(predicted_route, gate_status, case),
        rework_or_misdiagnosis_identified=_rework_or_misdiagnosis_identified(all_decision_text, case),
        gate_mispass=gate_mispass,
        guardrail_misroute=_guardrail_misroute(case, state, predicted_route, gate_status),
        wrong_tree_misdiagnosis=wrong_tree_misdiagnosis,
        failure_tags=failure_tags,
        short_error_reason=_short_error_reason(failure_tags),
        executed_tests=[test.model_dump(mode="json") for test in state.executed_tests],
        evidence_summary=[
            {
                "source_type": item.source_type,
                "source_id": item.source_id,
                "claim": item.claim,
                "supports_node_id": item.supports_node_id,
                "supports_cause_id": item.supports_cause_id,
                "strength": item.strength,
            }
            for item in state.evidence_chain[:12]
        ],
        planned_actions=planned_actions,
        case_only_hypotheses=hypotheses,
        rework_risk=state.rework_risk.model_dump(mode="json") if state.rework_risk else None,
        risk_notes=state.gate_result.risk_notes if state.gate_result else [],
        replay_trace=[record.model_dump(mode="json") for record in state.replay_trace],
    )


def _eval_detail_row(result: EvalCaseResult) -> dict[str, Any]:
    return {
        "case_id": result.case_id,
        "eval_group": result.eval_group,
        "expected_route": result.expected_route,
        "predicted_route": result.predicted_route,
        "expected_tree_id": result.expected_tree_id,
        "predicted_tree_id": result.tree_id,
        "expected_leaf_symptom_id": result.expected_leaf_symptom_id,
        "predicted_leaf_symptom_id": result.active_node_id,
        "expected_gate_status": result.expected_gate_status,
        "predicted_gate_status": result.gate_status,
        "expected_next_action_hit_text": result.expected_next_action_hit_text,
        "expected_action_keywords": result.expected_action_keywords,
        "actual_planned_actions": result.planned_actions,
        "executed_tests": result.executed_tests,
        "evidence_summary": result.evidence_summary,
        "failure_tags": result.failure_tags,
        "short_error_reason": result.short_error_reason,
        "gate_mispass": result.gate_mispass,
        "guardrail_misroute": result.guardrail_misroute,
        "wrong_tree_misdiagnosis": result.wrong_tree_misdiagnosis,
        "replay_trace": result.replay_trace,
    }


def _failure_tags(
    *,
    route_correct: bool | None,
    coverage_correct: bool | None,
    tree_correct: bool | None,
    leaf_correct: bool | None,
    gate_correct: bool | None,
    next_action_hit: bool | None,
    gate_mispass: bool,
    wrong_tree_misdiagnosis: bool,
) -> list[str]:
    tags: list[str] = []
    checks = [
        ("route_mismatch", route_correct),
        ("coverage_mismatch", coverage_correct),
        ("tree_mismatch", tree_correct),
        ("leaf_mismatch", leaf_correct),
        ("gate_mismatch", gate_correct),
        ("next_action_miss", next_action_hit),
    ]
    for tag, value in checks:
        if value is False:
            tags.append(tag)
    if gate_mispass:
        tags.append("gate_mispass")
    if wrong_tree_misdiagnosis:
        tags.append("wrong_tree_misdiagnosis")
    return tags


def _short_error_reason(failure_tags: list[str]) -> str | None:
    if not failure_tags:
        return None
    labels = {
        "route_mismatch": "路由不符合标注",
        "coverage_mismatch": "覆盖判断不符合标注",
        "tree_mismatch": "故障树选择不符合标注",
        "leaf_mismatch": "最终叶子不符合标注",
        "gate_mismatch": "Gate 状态不符合标注",
        "next_action_miss": "Planner 下一步动作未命中标注关键词",
        "gate_mispass": "Gate 存在误放行",
        "wrong_tree_misdiagnosis": "错误故障树导致误诊",
    }
    return "；".join(labels.get(tag, tag) for tag in failure_tags)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _read_json_array(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else []


def _default_eval_run_id(suite: str) -> str:
    stamp = utc_now_iso().replace(":", "").replace("+", "Z").replace(".", "-")
    safe_suite = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in suite)
    return f"{stamp}_{safe_suite}"


def _confusion_rows(
    dimension: Literal["tree", "node", "test"],
    pairs: list[tuple[str, str, EvalCaseResult]],
) -> list[EvalConfusionRow]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for expected, predicted, result in pairs:
        key = (expected, predicted)
        bucket = buckets.setdefault(key, {"case_ids": [], "failure_tags": set()})
        bucket["case_ids"].append(result.case_id)
        bucket["failure_tags"].update(result.failure_tags)
    rows = [
        EvalConfusionRow(
            dimension=dimension,
            expected=expected,
            predicted=predicted,
            count=len(payload["case_ids"]),
            case_ids=payload["case_ids"],
            failure_tags=sorted(payload["failure_tags"]),
        )
        for (expected, predicted), payload in buckets.items()
    ]
    return sorted(rows, key=lambda item: (-item.count, item.expected, item.predicted))


def _metric_delta(
    metric: str,
    baseline: EvalSuiteSummary,
    current: EvalSuiteSummary,
    baseline_results: list[EvalCaseResult],
    current_results: list[EvalCaseResult],
) -> EvalMetricDelta:
    baseline_value = getattr(baseline, metric)
    current_value = getattr(current, metric)
    delta = _delta_value(baseline_value, current_value)
    status = _delta_status(metric, delta)
    return EvalMetricDelta(
        metric=metric,
        baseline=baseline_value,
        current=current_value,
        delta=delta,
        status=status,
        affected_case_ids=_affected_cases_for_metric(metric, baseline_results, current_results),
    )


def _delta_value(
    baseline: float | int | None,
    current: float | int | None,
) -> float | int | None:
    if baseline is None or current is None:
        return None
    value = current - baseline
    return round(value, 4) if isinstance(value, float) else value


def _delta_status(metric: str, delta: float | int | None) -> Literal["IMPROVED", "REGRESSED", "STABLE", "INFO"]:
    if delta is None:
        return "INFO"
    if delta == 0:
        return "STABLE"
    lower_is_better = metric in EVAL_LOWER_IS_BETTER
    if lower_is_better:
        return "IMPROVED" if delta < 0 else "REGRESSED"
    return "IMPROVED" if delta > 0 else "REGRESSED"


def _affected_cases_for_metric(
    metric: str,
    baseline_results: list[EvalCaseResult],
    current_results: list[EvalCaseResult],
) -> list[str]:
    current_by_case = {item.case_id: item for item in current_results}
    baseline_by_case = {item.case_id: item for item in baseline_results}
    if metric in {"gate_mispass_count", "wrong_tree_misdiagnosis_count", "guardrail_misroute_count"}:
        flag = {
            "gate_mispass_count": "gate_mispass",
            "wrong_tree_misdiagnosis_count": "wrong_tree_misdiagnosis",
            "guardrail_misroute_count": "guardrail_misroute",
        }[metric]
        return sorted(case_id for case_id, item in current_by_case.items() if getattr(item, flag))
    field = EVAL_METRIC_FIELDS.get(metric)
    if not field:
        return []
    affected: list[str] = []
    for case_id, current in current_by_case.items():
        current_value = getattr(current, field)
        baseline_value = getattr(baseline_by_case.get(case_id), field, None)
        if current_value is False and baseline_value is not False:
            affected.append(case_id)
    return sorted(affected)


def _label(value: Any) -> str:
    if value is None:
        return "NONE"
    text = str(getattr(value, "value", value)).strip()
    return text or "NONE"


def _expected_test_label(result: EvalCaseResult) -> str:
    if result.expected_next_action_hit_text:
        return result.expected_next_action_hit_text
    if result.expected_action_keywords:
        return " / ".join(result.expected_action_keywords)
    return "UNSCORED"


def _planned_test_label(planned_actions: list[dict[str, Any]]) -> str:
    labels: list[str] = []
    for action in planned_actions:
        label = action.get("test_id") or action.get("action_type") or action.get("action_id")
        if label and label not in labels:
            labels.append(str(label))
    return " / ".join(labels[:6]) if labels else "NONE"


def _predicted_route(
    state: Any,
    coverage_status: CoverageStatus | None,
    gate_status: GateStatus | None,
) -> str:
    if gate_status == GateStatus.FAIL and coverage_status == CoverageStatus.UNSUPPORTED:
        return "REJECT_OR_NEED_MORE_EVIDENCE"
    if state.diagnosis_mode == DiagnosisMode.CASE_ONLY_EXPLORATORY:
        return "CASE_ONLY_DIAGNOSIS"
    if coverage_status == CoverageStatus.COVERED:
        return "TREE_DIAGNOSIS"
    if gate_status in {GateStatus.GRAY, GateStatus.FAIL}:
        return "REJECT_OR_NEED_MORE_EVIDENCE"
    return "UNKNOWN"


def _coverage_correct(actual: CoverageStatus | None, case: EvalCase) -> bool | None:
    if case.expected_tree_id == "FT_002_OR_CASE_ONLY":
        return actual in {CoverageStatus.COVERED, CoverageStatus.UNSUPPORTED}
    return _equals_if_expected(actual, case.expected_coverage)


def _route_correct(predicted_route: str, case: EvalCase, state: Any) -> bool | None:
    if not case.expected_route:
        return None
    if case.expected_tree_id == "FT_002_OR_CASE_ONLY":
        return predicted_route in {"TREE_DIAGNOSIS", "CASE_ONLY_DIAGNOSIS"} and (
            state.active_tree_id in {None, "FT_002"}
        )
    return predicted_route == case.expected_route


def _production_gate_safe(case: EvalCase, gate_status: GateStatus | None) -> bool | None:
    if not case.expected_route:
        return None
    if case.expected_route == "TREE_DIAGNOSIS":
        return True
    return gate_status != GateStatus.PASS


def _reject_correct(predicted_route: str, gate_status: GateStatus | None, case: EvalCase) -> bool | None:
    if case.expected_route != "REJECT_OR_NEED_MORE_EVIDENCE":
        return None
    if case.expected_gate_status:
        return gate_status == case.expected_gate_status
    return predicted_route == "REJECT_OR_NEED_MORE_EVIDENCE"


def _rework_or_misdiagnosis_identified(text: str, case: EvalCase) -> bool | None:
    if not (case.is_rework or case.is_prior_misdiagnosis):
        return None
    return any(marker in text for marker in ["返修", "误判", "前次", "上次", "重复", "无效", "避免"])


def _guardrail_misroute(
    case: EvalCase,
    state: Any,
    predicted_route: str,
    gate_status: GateStatus | None,
) -> bool:
    if case.eval_group != "ROUTING_GUARDRAIL":
        return False
    if gate_status == GateStatus.PASS and case.expected_route != "TREE_DIAGNOSIS":
        return True
    if case.case_id == "EV-GR-004":
        return bool(state.active_tree_id == "FT_002" and state.active_node_id not in {None, "S109"})
    if predicted_route == "TREE_DIAGNOSIS" and case.expected_tree_id in {None, "NONE"}:
        return True
    return False


def _group_metrics(results: list[EvalCaseResult]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[EvalCaseResult]] = {}
    for result in results:
        groups.setdefault(result.eval_group or "UNKNOWN", []).append(result)
    return {
        group: {
            "cases": len(items),
            "route_accuracy": _ratio(item.route_correct for item in items),
            "tree_selection_accuracy": _ratio(item.tree_correct for item in items),
            "final_leaf_accuracy": _ratio(item.leaf_correct for item in items),
            "gate_accuracy": _ratio(item.gate_correct for item in items),
            "production_gate_safety_rate": _ratio(item.production_gate_safe for item in items),
            "case_only_hypothesis_hit_rate": _ratio(item.hypothesis_hit for item in items),
            "next_action_hit_rate": _ratio(item.next_action_hit for item in items),
            "rework_or_misdiagnosis_identification_rate": _ratio(
                item.rework_or_misdiagnosis_identified for item in items
            ),
            "gate_mispass_count": sum(1 for item in items if item.gate_mispass),
            "guardrail_misroute_count": sum(1 for item in items if item.guardrail_misroute),
        }
        for group, items in sorted(groups.items())
    }


def _ratio(values: Any) -> float | None:
    scored = [value for value in values if value is not None]
    if not scored:
        return None
    return round(sum(1 for value in scored if value) / len(scored), 4)


def _equals_if_expected(actual: Any, expected: Any) -> bool | None:
    if expected is None:
        return None
    return actual == expected


def _expected_match_or_none(actual: str | None, expected: str | None) -> bool | None:
    if expected is None:
        return None
    return _expected_match(actual, expected)


def _expected_match(actual: str | None, expected: str | None) -> bool:
    if expected is None:
        return True
    if actual is None:
        return "NONE" in expected.split("_OR_")
    allowed = set(expected.split("_OR_"))
    return actual in allowed


def _keyword_hit(text: str, keywords: list[str]) -> bool | None:
    if not keywords:
        return None
    normalized = text.lower()
    return any(keyword.lower() in normalized for keyword in keywords)


def _expected_keyword_hit(text: str, keywords: list[str], expected_hit: bool | None) -> bool | None:
    if expected_hit is False:
        return None
    hit = _keyword_hit(text, keywords)
    if expected_hit is True:
        return bool(hit)
    return hit


def _expected_tree_from_leaf(expected_leaf: str | None) -> str | None:
    if not expected_leaf:
        return None
    if expected_leaf.startswith("S0"):
        return "FT_001"
    if expected_leaf.startswith("S1"):
        return "FT_002"
    return None


def _expected_gate_from_order(order: WorkOrder) -> GateStatus | None:
    text = " ".join(
        item
        for item in [
            order.description or "",
            order.repair_action or "",
            order.expected_root_cause or "",
        ]
        if item
    )
    if any(marker in text for marker in ["GRAY", "暂不", "待复测", "缺证据", "未复现"]):
        return GateStatus.GRAY
    return GateStatus.PASS


def _none_if_marker(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"NONE", "NULL", "N/A"}:
        return None
    return text


def _gate_or_none(value: Any) -> GateStatus | None:
    text = _none_if_marker(value)
    if not text:
        return None
    try:
        return GateStatus(text.upper())
    except ValueError:
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _split_evidence(text: str) -> list[str]:
    return [
        item.strip()
        for item in text.replace("；", ";").replace("\n", ";").split(";")
        if item.strip()
    ]


def _keywords_from_text(value: Any) -> list[str]:
    text = _none_if_marker(value)
    if not text:
        return []
    parts = [part.strip() for part in text.replace("；", ";").replace("/", ";").split(";")]
    keywords: list[str] = []
    for part in parts:
        if not part:
            continue
        keywords.append(part)
        for token in ["检查", "读取", "确认", "执行", "采集", "区分", "测", "补充", "要求", "生成"]:
            part = part.replace(token, "")
        cleaned = part.strip()
        if cleaned and cleaned not in keywords:
            keywords.append(cleaned)
    domain_terms = [
        "CC2",
        "冷媒",
        "制动盘",
        "摩擦片",
        "摄像头",
        "稳定杆",
        "球头",
        "座椅通风",
        "风扇",
        "LED",
        "驱动板",
        "TBOX",
        "雨量传感器",
        "撑杆",
        "逆变器",
        "温度传感器",
        "慢充口",
        "亮度",
        "异物",
        "缓存",
        "门锁状态",
        "线束",
        "事故",
        "信息不足",
    ]
    for term in domain_terms:
        if term.lower() in text.lower() and term not in keywords:
            keywords.append(term)
    return keywords[:8]


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _tree_selection_accuracy(records: list[ReplayRecord]) -> float | None:
    total = 0
    correct = 0
    for record in records:
        expected_leaf = _nested(record.state_after, "work_order", "expected_leaf_symptom_id")
        active_tree = record.state_after.get("active_tree_id")
        if not expected_leaf or not active_tree:
            continue
        total += 1
        if (expected_leaf.startswith("S0") and active_tree == "FT_001") or (
            expected_leaf.startswith("S1") and active_tree == "FT_002"
        ):
            correct += 1
    return round(correct / total, 4) if total else None


def _final_leaf_accuracy(records: list[ReplayRecord]) -> float | None:
    total = 0
    correct = 0
    for record in records:
        expected_leaf = _nested(record.state_after, "work_order", "expected_leaf_symptom_id")
        active_node = record.state_after.get("active_node_id")
        if not expected_leaf or not active_node:
            continue
        total += 1
        if expected_leaf == active_node:
            correct += 1
    return round(correct / total, 4) if total else None


def _wrong_tree_misdiagnosis_count(records: list[ReplayRecord]) -> int:
    count = 0
    for record in records:
        expected_leaf = _nested(record.state_after, "work_order", "expected_leaf_symptom_id")
        active_tree = record.state_after.get("active_tree_id")
        gate_status = record.gate_result.get("status")
        if not expected_leaf or not active_tree or gate_status != "PASS":
            continue
        expected_tree = "FT_001" if expected_leaf.startswith("S0") else "FT_002"
        if expected_tree != active_tree:
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--datasets-dir", default="datasets")
    parser.add_argument("--diagnostic-eval", action="store_true")
    parser.add_argument("--eval-suite", choices=["default", "labeled_v1"], default="default")
    parser.add_argument("--eval-cases", default=None)
    parser.add_argument("--eval-output-dir", default="datasets/eval_results")
    parser.add_argument("--eval-runs-dir", default=None)
    parser.add_argument("--ttl-path", default="corrected_fault_tree_instances.ttl")
    parser.add_argument("--raw-docs-dir", default="data/raw_docs")
    parser.add_argument("--chroma-dir", default="data/chroma")
    parser.add_argument("--diagnosis-mode", default="PRODUCTION")
    parser.add_argument("--llm-enable", action="store_true")
    args = parser.parse_args()
    if args.diagnostic_eval:
        settings = Settings(
            fault_tree_ttl_path=Path(args.ttl_path),
            raw_docs_dir=Path(args.raw_docs_dir),
            chroma_dir=Path(args.chroma_dir),
            runs_dir=Path(args.runs_dir),
            datasets_dir=Path(args.datasets_dir),
            diagnosis_mode=args.diagnosis_mode,
            llm_enable=args.llm_enable,
        )
        engine = DiagnosticEngine(
            RdfFaultTreeRepository(settings.fault_tree_ttl_path),
            DocumentRag(settings.raw_docs_dir, settings.chroma_dir),
            settings,
        )
        summary = run_diagnostic_eval(
            engine,
            args.eval_cases,
            args.eval_output_dir,
            args.eval_suite,
            args.eval_runs_dir,
        )
        print(json.dumps(summary.model_dump(mode="json", exclude={"results"}), ensure_ascii=False, indent=2))
        return
    summary = export_datasets(load_replay_records(args.runs_dir), args.datasets_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
