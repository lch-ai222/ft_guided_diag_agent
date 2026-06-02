from __future__ import annotations

from hashlib import sha1

from ft_diag_agent.models import (
    CoverageStatus,
    DiagnosisMode,
    DiagnosticState,
    FaultTreeDraft,
    FaultTreeGenerationRequest,
    FaultTreeRequestCluster,
    FaultTreeReviewStatus,
    ReplayRecord,
)
from ft_diag_agent.work_orders import work_order_to_intake_text


class DynamicFaultTreeRequestBuilder:
    def build(self, state: DiagnosticState) -> FaultTreeGenerationRequest | None:
        if not _eligible_for_dynamic_request(state):
            return None
        start = _candidate_start_symptom(state)
        domain = _candidate_failure_domain(state)
        roots = _candidate_root_hypotheses(state)
        tests = _candidate_tests(state)
        evidence_ids = [item.evidence_id for item in state.evidence_chain[:8]]
        source_refs = _source_refs(state)
        request = FaultTreeGenerationRequest(
            source_case_id=state.case_id,
            work_order_id=state.work_order.order_id if state.work_order else None,
            trigger_reason=(
                "当前工单在生产故障树库中未覆盖，已进入开发态 case-only 探索；"
                "生成候选故障树请求用于后续本体建模、结构校验和人工审核。"
            ),
            candidate_start_symptom=start,
            candidate_failure_domain=domain,
            candidate_root_hypotheses=roots,
            candidate_tests=tests,
            candidate_transitions=_candidate_transition_notes(start, roots, tests),
            evidence_ids=evidence_ids,
            source_refs=source_refs,
            ontology_build_constraints=_ontology_build_constraints(),
            required_validation_steps=_required_validation_steps(),
        )
        request.draft = FaultTreeDraft(
            request_id=request.request_id,
            candidate_start_symptom=start,
            candidate_symptoms=_candidate_symptoms(start, roots),
            candidate_tests=[
                {
                    "test_name": item,
                    "test_name_status": "SUGGESTED_GROUNDED",
                    "executor_type": "HUMAN",
                }
                for item in tests
            ],
            candidate_transitions=[
                {
                    "source": start,
                    "target": root,
                    "test_id_status": "MISSING",
                    "condition_status": "SUGGESTED_GROUNDED",
                    "note": "需由故障树生成 Agent 在本体写入阶段创建真实 OntologyTest 并补齐 transition。",
                }
                for root in roots
            ],
            validation_notes=[
                "该 draft 不是生产 FaultTree，只是给故障树生成 Agent 的候选建模输入。",
                "最终 FaultTree 必须由 start 节点沿 SymptomTransition 确定性 BFS 重建。",
            ],
            source_refs=source_refs,
        )
        return request

    def build_cluster(
        self,
        state: DiagnosticState,
        request: FaultTreeGenerationRequest | None,
    ) -> FaultTreeRequestCluster | None:
        if not request:
            return None
        supporting_cases = _supporting_case_ids(state, request)
        support_count = 1
        review_notes = [
            "聚类当前仍是单次诊断内的候选种子，尚未跨 replay/runs 做持久化合并。",
            "候选请求达到最小相似案例数后，才建议进入人工 UNDER_REVIEW。",
            "即使进入 UNDER_REVIEW 或 SHADOW_MODE，也不能自动生产 PASS。",
        ]
        if support_count < 3:
            review_notes.append("相似案例数量不足，建议继续收集同类 case-only 工单。")
        return FaultTreeRequestCluster(
            cluster_id=_cluster_id(request),
            cluster_key=_cluster_key(request),
            review_status=FaultTreeReviewStatus.DRAFT_REQUESTED,
            request_ids=[request.request_id],
            source_case_ids=[request.source_case_id],
            supporting_case_ids=supporting_cases,
            representative_start_symptom=request.candidate_start_symptom,
            candidate_failure_domain=request.candidate_failure_domain,
            merged_root_hypotheses=request.candidate_root_hypotheses,
            merged_tests=request.candidate_tests,
            evidence_ids=request.evidence_ids,
            source_refs=request.source_refs,
            support_count=support_count,
            allowed_next_statuses=_allowed_next_statuses(FaultTreeReviewStatus.DRAFT_REQUESTED, support_count),
            recommended_next_step=_recommended_next_step(support_count),
            review_notes=review_notes,
        )


def _eligible_for_dynamic_request(state: DiagnosticState) -> bool:
    return bool(
        state.coverage_decision
        and state.coverage_decision.status == CoverageStatus.UNSUPPORTED
        and state.diagnosis_mode == DiagnosisMode.CASE_ONLY_EXPLORATORY
        and state.work_order
        and (state.case_only_hypotheses or state.case_only_plan or state.case_only_findings)
    )


def _candidate_start_symptom(state: DiagnosticState) -> str:
    if state.work_order:
        for value in [
            state.work_order.failure_phenomenon,
            state.work_order.title,
            state.work_order.description,
        ]:
            if value:
                return _compact(value, 48)
    if state.intake and state.intake.phenomenon:
        return _compact(state.intake.phenomenon, 48)
    return "未覆盖新故障现象"


def _candidate_failure_domain(state: DiagnosticState) -> str | None:
    if state.work_order and state.work_order.business_domain:
        return state.work_order.business_domain
    if state.case_only_hypotheses:
        return state.case_only_hypotheses[0].system_area
    return None


def _candidate_root_hypotheses(state: DiagnosticState) -> list[str]:
    roots: list[str] = []
    for hypothesis in state.case_only_hypotheses:
        parts = [
            hypothesis.system_area,
            hypothesis.component or "",
            hypothesis.failure_mode,
        ]
        roots.append(" / ".join(part for part in parts if part))
    for finding in state.case_only_findings:
        if finding.supports_hypothesis_ids and finding.result:
            roots.append(_compact(finding.result, 72))
    return list(dict.fromkeys(roots))[:6]


def _candidate_tests(state: DiagnosticState) -> list[str]:
    tests: list[str] = []
    for action in state.planned_actions:
        if action.action_type == "CASE_ONLY_HITL":
            tests.append(action.reason)
    for finding in state.case_only_findings:
        if finding.test_id and finding.result:
            tests.append(f"{finding.test_id}: {finding.result}")
    return list(dict.fromkeys(_compact(item, 96) for item in tests if item))[:8]


def _candidate_transition_notes(start: str, roots: list[str], tests: list[str]) -> list[str]:
    if not roots:
        return []
    primary_test = tests[0] if tests else "MISSING 占位检测"
    return [
        f"{start} -> {root}，建议通过 {primary_test} 判定；需在本体建模阶段拆分 inner/root 层级。"
        for root in roots[:6]
    ]


def _candidate_symptoms(start: str, roots: list[str]) -> list[dict[str, str]]:
    symptoms = [
        {
            "symptom_name": start,
            "symptom_level": "start",
            "symptom_name_status": "SUGGESTED_GROUNDED",
        }
    ]
    symptoms.extend(
        {
            "symptom_name": root,
            "symptom_level": "root",
            "symptom_name_status": "SUGGESTED_LOW_CONF",
        }
        for root in roots
    )
    return symptoms


def _source_refs(state: DiagnosticState) -> list[str]:
    refs: list[str] = []
    if state.work_order:
        refs.append(state.work_order.source_path or state.work_order.order_id)
    for evidence in state.evidence_chain[:8]:
        refs.extend(evidence.source_refs)
        if evidence.source_id:
            refs.append(evidence.source_id)
    return list(dict.fromkeys(refs))[:12]


def _supporting_case_ids(
    state: DiagnosticState,
    request: FaultTreeGenerationRequest,
) -> list[str]:
    case_ids: list[str] = []
    if request.work_order_id:
        case_ids.append(request.work_order_id)
    case_ids.append(request.source_case_id)
    for evidence in state.evidence_chain:
        if evidence.source_type in {"RAG", "WORK_ORDER"} and evidence.source_id:
            case_ids.append(evidence.source_id)
    return list(dict.fromkeys(case_ids))[:12]


def _cluster_key(request: FaultTreeGenerationRequest) -> str:
    parts = [
        request.candidate_failure_domain or "unknown",
        request.candidate_start_symptom,
        *request.candidate_root_hypotheses[:3],
    ]
    return "|".join(_normalize_key_part(part) for part in parts if part)


def _cluster_id(request: FaultTreeGenerationRequest) -> str:
    digest = sha1(_cluster_key(request).encode("utf-8")).hexdigest()[:10]
    return f"FTC-{digest}"


def _normalize_key_part(value: str) -> str:
    return "".join(value.lower().split())[:64]


def _allowed_next_statuses(
    status: FaultTreeReviewStatus,
    support_count: int,
) -> list[FaultTreeReviewStatus]:
    if status == FaultTreeReviewStatus.DRAFT_REQUESTED:
        return [FaultTreeReviewStatus.UNDER_REVIEW] if support_count >= 3 else []
    if status == FaultTreeReviewStatus.UNDER_REVIEW:
        return [FaultTreeReviewStatus.SHADOW_MODE, FaultTreeReviewStatus.REJECTED]
    if status == FaultTreeReviewStatus.SHADOW_MODE:
        return [FaultTreeReviewStatus.PRODUCTION_APPROVED, FaultTreeReviewStatus.REJECTED]
    return []


def _recommended_next_step(support_count: int) -> str:
    if support_count >= 3:
        return "相似案例数量已达到人工审核门槛，可提交故障树生成 Agent 做本体建模草案。"
    return "继续收集同类 case-only 工单或补充历史工单证据，暂不建议进入人工审核。"


def _ontology_build_constraints() -> list[str]:
    return [
        "不要让 LLM 直接输出最终 FaultTree 或修改 FaultTree.symptom_ids。",
        "LLM/Agent 只负责维护 FailureSymptom、OntologyTest、OntologyMeasure、SymptomTransition。",
        "FailureSymptom 表达异常状态；OntologyTest 表达检查动作；OntologyMeasure 表达处置措施。",
        "SymptomTransition 必须表达 source -> target 的诊断分解，且必须引用非空 OntologyTest。",
        "缺失字段必须用 MISSING/SUGGESTED_LOW_CONF 等状态显式表达，不能用空字符串冒充已抽取。",
        "最终 FaultTree 必须由 start 节点沿 SymptomTransition 确定性 BFS 重建。",
        "候选树必须人工审核后才能进入生产树库；开发态请求不能让 Gate PASS。",
    ]


def _required_validation_steps() -> list[str]:
    return [
        "查询已有本体实体，避免重复 start/root 节点。",
        "写入前制定本体建模计划。",
        "写入 FailureSymptom / OntologyTest / OntologyMeasure / SymptomTransition。",
        "运行 SHACL 结构校验和 Python 图规则校验。",
        "修复 error 级结构问题。",
        "执行确定性 rebuild_fault_trees。",
        "进入人工审核或 shadow mode，不直接生产放行。",
    ]


def _compact(value: str, limit: int) -> str:
    text = " ".join(value.strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def dynamic_request_source_text(state: DiagnosticState) -> str:
    return work_order_to_intake_text(state.work_order) if state.work_order else ""


def merge_dynamic_tree_clusters(records: list[ReplayRecord]) -> list[FaultTreeRequestCluster]:
    clusters: dict[str, FaultTreeRequestCluster] = {}
    for record in records:
        request_payload = record.state_after.get("fault_tree_generation_request")
        if not request_payload:
            continue
        request = FaultTreeGenerationRequest.model_validate(request_payload)
        cluster_payload = record.state_after.get("fault_tree_request_cluster")
        cluster = (
            FaultTreeRequestCluster.model_validate(cluster_payload)
            if cluster_payload
            else _cluster_from_request(request)
        )
        key = cluster.cluster_id
        if key not in clusters:
            clusters[key] = cluster.model_copy(deep=True)
            continue
        _merge_cluster(clusters[key], cluster)
    for cluster in clusters.values():
        cluster.support_count = len(cluster.source_case_ids)
        cluster.allowed_next_statuses = _allowed_next_statuses(cluster.review_status, cluster.support_count)
        cluster.recommended_next_step = _recommended_next_step(cluster.support_count)
        cluster.review_notes = [
            note
            for note in cluster.review_notes
            if "尚未跨 replay/runs 做持久化合并" not in note
        ]
        cluster.review_notes = list(
            dict.fromkeys(
                [
                    *cluster.review_notes,
                    "该聚类由 runs/*.jsonl 跨诊断记录扫描生成，仍只作为人工审核输入。",
                ]
            )
        )
        if cluster.support_count >= cluster.min_support_for_review:
            cluster.review_notes = list(
                dict.fromkeys(
                    [
                        *cluster.review_notes,
                        "跨 runs 聚类达到人工审核门槛，可提交故障树生成 Agent 做本体建模草案。",
                    ]
                )
            )
    return sorted(clusters.values(), key=lambda item: (-item.support_count, item.cluster_id))


def dynamic_tree_cluster_rows(clusters: list[FaultTreeRequestCluster]) -> list[dict]:
    return [cluster.model_dump(mode="json") for cluster in clusters]


def _cluster_from_request(request: FaultTreeGenerationRequest) -> FaultTreeRequestCluster:
    support_cases = list(dict.fromkeys([item for item in [request.work_order_id, request.source_case_id] if item]))
    return FaultTreeRequestCluster(
        cluster_id=_cluster_id(request),
        cluster_key=_cluster_key(request),
        review_status=FaultTreeReviewStatus.DRAFT_REQUESTED,
        request_ids=[request.request_id],
        source_case_ids=[request.source_case_id],
        supporting_case_ids=support_cases,
        representative_start_symptom=request.candidate_start_symptom,
        candidate_failure_domain=request.candidate_failure_domain,
        merged_root_hypotheses=request.candidate_root_hypotheses,
        merged_tests=request.candidate_tests,
        evidence_ids=request.evidence_ids,
        source_refs=request.source_refs,
        support_count=1,
        allowed_next_statuses=_allowed_next_statuses(FaultTreeReviewStatus.DRAFT_REQUESTED, 1),
        recommended_next_step=_recommended_next_step(1),
        review_notes=["由历史 replay 中的动态树请求恢复出的聚类。"],
    )


def _merge_cluster(target: FaultTreeRequestCluster, source: FaultTreeRequestCluster) -> None:
    target.request_ids = _merge_unique(target.request_ids, source.request_ids)
    target.source_case_ids = _merge_unique(target.source_case_ids, source.source_case_ids)
    target.supporting_case_ids = _merge_unique(target.supporting_case_ids, source.supporting_case_ids)
    target.merged_root_hypotheses = _merge_unique(target.merged_root_hypotheses, source.merged_root_hypotheses)[:12]
    target.merged_tests = _merge_unique(target.merged_tests, source.merged_tests)[:16]
    target.evidence_ids = _merge_unique(target.evidence_ids, source.evidence_ids)[:24]
    target.source_refs = _merge_unique(target.source_refs, source.source_refs)[:24]
    target.review_notes = _merge_unique(target.review_notes, source.review_notes)


def _merge_unique(left: list, right: list) -> list:
    return list(dict.fromkeys([*left, *right]))
