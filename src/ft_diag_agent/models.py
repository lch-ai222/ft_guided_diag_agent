from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class GateStatus(StrEnum):
    PASS = "PASS"
    GRAY = "GRAY"
    FAIL = "FAIL"


class ToolStatus(StrEnum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


class CoverageStatus(StrEnum):
    COVERED = "COVERED"
    UNSUPPORTED = "UNSUPPORTED"
    AMBIGUOUS = "AMBIGUOUS"


class DiagnosisMode(StrEnum):
    PRODUCTION = "PRODUCTION"
    DEVELOPMENT = "DEVELOPMENT"
    CASE_ONLY_EXPLORATORY = "CASE_ONLY_EXPLORATORY"


class DiagnosticWorkflowPhase(StrEnum):
    INIT = "INIT"
    CLASSIFIED = "CLASSIFIED"
    RETRIEVING_CONTEXT = "RETRIEVING_CONTEXT"
    PLANNING = "PLANNING"
    WAITING_HITL = "WAITING_HITL"
    GATE_PASS = "GATE_PASS"
    GATE_GRAY = "GATE_GRAY"
    GATE_FAIL = "GATE_FAIL"
    REPORTED = "REPORTED"


class ExecutorType(StrEnum):
    HUMAN = "HUMAN"
    AUTO_TOOL = "AUTO_TOOL"
    LLM_TOOL = "LLM_TOOL"
    SUBAGENT = "SUBAGENT"
    STUB = "STUB"


class FaultTreeReviewStatus(StrEnum):
    DRAFT_REQUESTED = "DRAFT_REQUESTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    SHADOW_MODE = "SHADOW_MODE"
    PRODUCTION_APPROVED = "PRODUCTION_APPROVED"
    REJECTED = "REJECTED"


class TreeProposalStatus(StrEnum):
    DRAFT_TREE = "DRAFT_TREE"
    CANDIDATE_TREE = "CANDIDATE_TREE"
    GRAY_TREE = "GRAY_TREE"
    RELEASED_TREE = "RELEASED_TREE"
    REJECTED = "REJECTED"


class TreeProposalKind(StrEnum):
    NEW_TREE = "NEW_TREE"
    TREE_CHANGE = "TREE_CHANGE"


class TreeChangeType(StrEnum):
    ADD_BRANCH = "ADD_BRANCH"
    REMOVE_BRANCH = "REMOVE_BRANCH"
    UPDATE_TEST = "UPDATE_TEST"
    UPDATE_THRESHOLD = "UPDATE_THRESHOLD"
    UPDATE_TRANSITION_CONDITION = "UPDATE_TRANSITION_CONDITION"
    UPDATE_EXECUTOR_TYPE = "UPDATE_EXECUTOR_TYPE"
    UPDATE_SCOPE = "UPDATE_SCOPE"
    MERGE_NODE = "MERGE_NODE"
    SPLIT_NODE = "SPLIT_NODE"
    DEPRECATE_TEST = "DEPRECATE_TEST"


class TreeGenerationJobStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class OntologyEntityType(StrEnum):
    FAILURE_SYMPTOM = "FailureSymptom"
    ONTOLOGY_TEST = "OntologyTest"
    ONTOLOGY_MEASURE = "OntologyMeasure"


class FieldStatus(StrEnum):
    MISSING = "MISSING"
    EXTRACTED_EXPLICIT = "EXTRACTED_EXPLICIT"
    EXTRACTED_INFERRED = "EXTRACTED_INFERRED"
    SUGGESTED_LOW_CONF = "SUGGESTED_LOW_CONF"
    SUGGESTED_GROUNDED = "SUGGESTED_GROUNDED"
    CONFIRMED = "CONFIRMED"
    VERIFIED = "VERIFIED"


class TreeGenerationQuality(StrEnum):
    HIGH_CONF_LLM_DRAFT = "HIGH_CONF_LLM_DRAFT"
    NEEDS_REPAIR_LLM_DRAFT = "NEEDS_REPAIR_LLM_DRAFT"
    LOW_CONF_DEBUG_DRAFT = "LOW_CONF_DEBUG_DRAFT"


class CaseOnlyHypothesisStatus(StrEnum):
    OPEN = "OPEN"
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"


class IntakeRequest(BaseModel):
    raw_input: str
    vehicle_project: str | None = None
    vin_list: list[str] = Field(default_factory=list)
    factory: str | None = None
    station: str | None = None
    timestamp: str | None = None
    extra_context: dict[str, Any] = Field(default_factory=dict)


class WorkOrder(BaseModel):
    order_id: str
    title: str | None = None
    failure_phenomenon: str
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
    extraction_method: str | None = None
    extraction_quality_notes: list[str] = Field(default_factory=list)
    raw_text: str = ""
    source_path: str | None = None


class WorkOrderClassification(BaseModel):
    tree_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    coverage_status: CoverageStatus = CoverageStatus.UNSUPPORTED
    diagnosis_mode: DiagnosisMode = DiagnosisMode.PRODUCTION
    matched_phenomenon: str | None = None
    reasoning_summary: str = ""
    signals: list[str] = Field(default_factory=list)


class CoverageDecision(BaseModel):
    status: CoverageStatus
    diagnosis_mode: DiagnosisMode
    tree_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class TestExecutionSpec(BaseModel):
    test_id: str
    executor_type: ExecutorType = ExecutorType.HUMAN
    tool_name: str = "human_input"
    input_schema: dict[str, Any] = Field(default_factory=dict)
    result_schema: dict[str, Any] = Field(default_factory=dict)
    transition_mapping: dict[str, str] = Field(default_factory=dict)


class NormalizedPhenomenon(BaseModel):
    phenomenon: str
    aliases: list[str] = Field(default_factory=list)
    vehicle_info: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    quality_notes: list[str] = Field(default_factory=list)
    llm_used: bool = False


class SymptomNode(BaseModel):
    uri: str
    symptom_id: str
    name: str
    name_status: str | None = None
    level: Literal["start", "inner", "root"] | str | None = None
    description: str | None = None
    description_status: str | None = None
    chunk_ids: list[str] = Field(default_factory=list)
    measure_ids: list[str] = Field(default_factory=list)


class DiagnosticTest(BaseModel):
    uri: str
    test_id: str
    name: str | None = None
    name_status: str | None = None
    unit: str | None = None
    unit_status: str | None = None
    hilim: float | None = None
    hilim_status: str | None = None
    lolim: float | None = None
    lolim_status: str | None = None
    rule: str | None = None
    rule_status: str | None = None
    target: str | None = None
    target_status: str | None = None
    description: str | None = None
    description_status: str | None = None
    chunk_ids: list[str] = Field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.name or self.target or self.test_id


class Measure(BaseModel):
    uri: str
    measure_id: str
    name: str | None = None
    name_status: str | None = None
    description: str | None = None
    description_status: str | None = None
    chunk_ids: list[str] = Field(default_factory=list)


class Transition(BaseModel):
    uri: str
    transition_id: str
    source_id: str
    target_id: str
    test_id: str
    condition: str | None = None
    condition_status: str | None = None
    description: str | None = None
    description_status: str | None = None
    chunk_ids: list[str] = Field(default_factory=list)


class FaultTree(BaseModel):
    uri: str
    tree_id: str
    name: str
    description: str | None = None
    applicable_scope: str | None = None
    version: str | None = None
    symptom_ids: list[str] = Field(default_factory=list)


class DiagnosticPath(BaseModel):
    tree_id: str
    node_ids: list[str]
    transition_ids: list[str]
    test_ids: list[str]
    root_cause_id: str | None = None
    score: float = 0.0
    match_reasons: list[str] = Field(default_factory=list)


class CandidateCause(BaseModel):
    cause_id: str
    name: str
    path: DiagnosticPath
    measure_ids: list[str] = Field(default_factory=list)
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class DiagnosticAction(BaseModel):
    action_id: str = Field(default_factory=lambda: f"A-{uuid4().hex[:10]}")
    action_type: str
    target_node_id: str | None = None
    target_cause_id: str | None = None
    test_id: str | None = None
    tool_name: str
    priority: int
    blocking: bool = True
    expected_result_schema: dict[str, Any] = Field(default_factory=dict)
    reason: str
    source_refs: list[str] = Field(default_factory=list)
    planner_source: str = "RULE"
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    risk_notes: list[str] = Field(default_factory=list)


class CaseOnlyHypothesis(BaseModel):
    hypothesis_id: str = Field(default_factory=lambda: f"H-{uuid4().hex[:8]}")
    system_area: str
    component: str | None = None
    failure_mode: str
    rationale: str
    confidence: float = Field(default=0.3, ge=0.0, le=1.0)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    next_check_ids: list[str] = Field(default_factory=list)
    status: CaseOnlyHypothesisStatus = CaseOnlyHypothesisStatus.OPEN


class ExploratoryDiagnosticPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: f"XP-{uuid4().hex[:8]}")
    objective: str
    summary: str
    planner_source: str = "RULE"
    hypothesis_ids: list[str] = Field(default_factory=list)
    next_action_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    iteration: int = 1
    completed_action_ids: list[str] = Field(default_factory=list)
    stopped_reason: str | None = None


class ExploratoryFinding(BaseModel):
    finding_id: str = Field(default_factory=lambda: f"XF-{uuid4().hex[:8]}")
    action_id: str | None = None
    test_id: str
    result: str
    supports_hypothesis_ids: list[str] = Field(default_factory=list)
    refutes_hypothesis_ids: list[str] = Field(default_factory=list)
    evidence_id: str | None = None
    notes: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class ExecutedTest(BaseModel):
    test_id: str
    result: str
    value: str | float | int | bool | None = None
    passed: bool | None = None
    notes: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class EvidenceItem(BaseModel):
    evidence_id: str = Field(default_factory=lambda: f"E-{uuid4().hex[:10]}")
    source_type: str
    source_id: str
    claim: str
    supports_node_id: str | None = None
    supports_cause_id: str | None = None
    strength: float = Field(default=0.5, ge=0.0, le=1.0)
    source_refs: list[str] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class SimilarReworkCase(BaseModel):
    source_id: str
    summary: str
    similarity_signal: str
    evidence_id: str | None = None
    source_refs: list[str] = Field(default_factory=list)


class ReworkRiskAssessment(BaseModel):
    is_rework_suspected: bool = False
    is_prior_misdiagnosis_suspected: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    prior_actions: list[str] = Field(default_factory=list)
    ineffective_actions: list[str] = Field(default_factory=list)
    avoided_repeat_actions: list[str] = Field(default_factory=list)
    recommended_checks: list[str] = Field(default_factory=list)
    similar_cases: list[SimilarReworkCase] = Field(default_factory=list)
    evidence_snippets: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class FaultTreeDraft(BaseModel):
    draft_id: str = Field(default_factory=lambda: f"FTD-{uuid4().hex[:8]}")
    request_id: str
    review_status: FaultTreeReviewStatus = FaultTreeReviewStatus.DRAFT_REQUESTED
    candidate_start_symptom: str
    candidate_symptoms: list[dict[str, Any]] = Field(default_factory=list)
    candidate_tests: list[dict[str, Any]] = Field(default_factory=list)
    candidate_measures: list[dict[str, Any]] = Field(default_factory=list)
    candidate_transitions: list[dict[str, Any]] = Field(default_factory=list)
    validation_notes: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class FaultTreePromotionDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: f"FTPD-{uuid4().hex[:8]}")
    review_status: FaultTreeReviewStatus
    reviewer: str | None = None
    rationale: str
    allowed_for_production: bool = False
    created_at: str = Field(default_factory=utc_now_iso)


class FaultTreeGenerationRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: f"FTGR-{uuid4().hex[:8]}")
    source_case_id: str
    work_order_id: str | None = None
    trigger_reason: str
    review_status: FaultTreeReviewStatus = FaultTreeReviewStatus.DRAFT_REQUESTED
    candidate_start_symptom: str
    candidate_failure_domain: str | None = None
    candidate_root_hypotheses: list[str] = Field(default_factory=list)
    candidate_tests: list[str] = Field(default_factory=list)
    candidate_transitions: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    ontology_build_constraints: list[str] = Field(default_factory=list)
    required_validation_steps: list[str] = Field(default_factory=list)
    tree_gen_agent_reference: str = "docs/tree_gen_agent.md"
    draft: FaultTreeDraft | None = None
    promotion_decision: FaultTreePromotionDecision | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class FaultTreeRequestCluster(BaseModel):
    cluster_id: str
    cluster_key: str
    review_status: FaultTreeReviewStatus = FaultTreeReviewStatus.DRAFT_REQUESTED
    request_ids: list[str] = Field(default_factory=list)
    source_case_ids: list[str] = Field(default_factory=list)
    supporting_case_ids: list[str] = Field(default_factory=list)
    representative_start_symptom: str
    candidate_failure_domain: str | None = None
    merged_root_hypotheses: list[str] = Field(default_factory=list)
    merged_tests: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    support_count: int = 1
    min_support_for_review: int = 3
    allowed_next_statuses: list[FaultTreeReviewStatus] = Field(default_factory=list)
    recommended_next_step: str
    review_notes: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class TreeGenerationInputDocument(BaseModel):
    document_id: str = Field(default_factory=lambda: f"TGDOC-{uuid4().hex[:8]}")
    source_path: str
    filename: str
    doc_type: str = "UNKNOWN"
    size_bytes: int = 0
    chunk_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class OntologyEntityDraft(BaseModel):
    entity_id: str
    entity_type: OntologyEntityType
    name: str | None = None
    name_status: FieldStatus = FieldStatus.EXTRACTED_INFERRED
    level: Literal["start", "inner", "root"] | None = None
    description: str | None = None
    description_status: FieldStatus = FieldStatus.EXTRACTED_INFERRED
    chunk_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class SymptomTransitionDraft(BaseModel):
    transition_id: str
    source_id: str
    target_id: str
    test_ids: list[str] = Field(default_factory=list)
    condition: str | None = None
    condition_status: FieldStatus = FieldStatus.EXTRACTED_INFERRED
    description: str | None = None
    description_status: FieldStatus = FieldStatus.EXTRACTED_INFERRED
    chunk_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class OntologyValidationIssue(BaseModel):
    severity: Literal["ERROR", "WARNING"]
    rule_id: str
    message: str
    entity_refs: list[str] = Field(default_factory=list)
    repair_hint: str


class OntologyExtractionPlan(BaseModel):
    schema_reference: str = "docs/tree_ontology_schema.md"
    tree_gen_agent_reference: str = "docs/tree_gen_agent.md"
    strategy: str = "LLM_FIRST_MULTI_PASS"
    required_passes: list[str] = Field(
        default_factory=lambda: [
            "PASS_1_ENTITY_EXTRACTION",
            "PASS_2_DEDUP_LEVELING",
            "PASS_3_TRANSITION_BINDING",
            "VALIDATE",
            "REPAIR_IF_NEEDED",
            "DETERMINISTIC_REBUILD",
        ]
    )
    notes: list[str] = Field(default_factory=list)


class OntologyExtractionPass(BaseModel):
    pass_id: str
    pass_type: str
    llm_used: bool = False
    summary: str
    output_counts: dict[str, int] = Field(default_factory=dict)
    output_preview: dict[str, Any] = Field(default_factory=dict)
    raw_output: dict[str, Any] = Field(default_factory=dict)
    raw_text: str | None = None
    issues_before: list[OntologyValidationIssue] = Field(default_factory=list)
    issues_after: list[OntologyValidationIssue] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class TreeGenerationValidationReport(BaseModel):
    is_valid: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    issues: list[OntologyValidationIssue] = Field(default_factory=list)
    start_symptom_count: int = 0
    root_symptom_count: int = 0
    test_count: int = 0
    transition_count: int = 0
    rebuilt_symptom_ids: list[str] = Field(default_factory=list)
    ontology_constraints: list[str] = Field(default_factory=list)


class TreeProposal(BaseModel):
    proposal_id: str = Field(default_factory=lambda: f"TP-{uuid4().hex[:8]}")
    status: TreeProposalStatus = TreeProposalStatus.DRAFT_TREE
    proposal_kind: TreeProposalKind = TreeProposalKind.NEW_TREE
    source_type: str = "BATCH_DOCUMENTS"
    source_job_id: str | None = None
    source_request_id: str | None = None
    source_cluster_id: str | None = None
    target_tree_id: str | None = None
    target_tree_version: str | None = None
    change_types: list[TreeChangeType] = Field(default_factory=list)
    change_summary: str | None = None
    change_patch: dict[str, Any] = Field(default_factory=dict)
    drift_signals: list[str] = Field(default_factory=list)
    phenomenon_bucket: str
    candidate_start_symptom: str
    candidate_failure_domain: str | None = None
    root_cause_families: list[str] = Field(default_factory=list)
    candidate_tests: list[str] = Field(default_factory=list)
    candidate_transitions: list[str] = Field(default_factory=list)
    source_case_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    confidence_summary: str = ""
    risk_notes: list[str] = Field(default_factory=list)
    allowed_next_statuses: list[TreeProposalStatus] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class TreeProposalCaseLink(BaseModel):
    link_id: str = Field(default_factory=lambda: f"TPCL-{uuid4().hex[:8]}")
    proposal_id: str
    case_id: str
    work_order_id: str | None = None
    link_type: Literal["SUPPORTS", "REFUTES", "AMBIGUOUS"] = "SUPPORTS"
    matched_root_cause_family: str | None = None
    useful_tests: list[str] = Field(default_factory=list)
    human_confirmed: bool | None = None
    notes: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class TreeProposalEvalResult(BaseModel):
    eval_id: str = Field(default_factory=lambda: f"TPE-{uuid4().hex[:8]}")
    proposal_id: str
    eval_suite: str
    status_at_eval: TreeProposalStatus
    metrics: dict[str, Any] = Field(default_factory=dict)
    failure_cases: list[dict[str, Any]] = Field(default_factory=list)
    unsafe_findings: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class TreeProposalReviewLog(BaseModel):
    review_id: str = Field(default_factory=lambda: f"TPR-{uuid4().hex[:8]}")
    proposal_id: str
    from_status: TreeProposalStatus
    to_status: TreeProposalStatus
    reviewer: str | None = None
    decision: Literal["APPROVE", "REJECT", "REQUEST_CHANGES"]
    rationale: str
    required_changes: list[str] = Field(default_factory=list)
    precheck_result: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class TreeProposalPromotionPrecheck(BaseModel):
    precheck_id: str = Field(default_factory=lambda: f"TPPC-{uuid4().hex[:8]}")
    proposal_id: str
    current_status: TreeProposalStatus
    target_status: TreeProposalStatus | None = None
    verdict: Literal["READY_FOR_REVIEW", "NEEDS_MORE_EVIDENCE", "BLOCKED", "NOT_APPLICABLE"]
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    satisfied: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class TreeReleaseManifest(BaseModel):
    manifest_id: str = Field(default_factory=lambda: f"TRM-{uuid4().hex[:8]}")
    proposal_id: str
    release_version: str
    candidate_tree_id: str
    source_status: TreeProposalStatus
    candidate_start_symptom: str
    applicable_scope: str = "PENDING_REVIEW"
    source_eval_ids: list[str] = Field(default_factory=list)
    source_eval_suites: list[str] = Field(default_factory=list)
    source_review_ids: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    safety_constraints: list[str] = Field(default_factory=list)
    release_notes: list[str] = Field(default_factory=list)
    generated_by: str | None = None
    formal_signoff_reviewer: str | None = None
    formal_signoff_rationale: str | None = None
    formal_signoff_at: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class TreeRollbackMetadata(BaseModel):
    rollback_id: str = Field(default_factory=lambda: f"TRB-{uuid4().hex[:8]}")
    proposal_id: str
    release_version: str
    rollback_target: str = "previous_released_tree_registry_state"
    rollback_triggers: list[str] = Field(default_factory=list)
    rollback_steps: list[str] = Field(default_factory=list)
    owner: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class TreeReleaseArtifact(BaseModel):
    release_artifact_id: str = Field(default_factory=lambda: f"TRA-{uuid4().hex[:8]}")
    proposal_id: str
    manifest: TreeReleaseManifest
    rollback: TreeRollbackMetadata
    ttl_diff_md: str
    generated_ttl_preview: str
    source_eval_ids: list[str] = Field(default_factory=list)
    source_eval_suites: list[str] = Field(default_factory=list)
    source_review_ids: list[str] = Field(default_factory=list)
    release_materials_ready: bool = False
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class TreeAdmissionMaterial(BaseModel):
    material_id: str
    stage: Literal["GRAY", "RELEASED"]
    name: str
    status: Literal["SATISFIED", "WARNING", "BLOCKED", "MISSING"]
    source_type: str
    source_id: str | None = None
    source_path: str | None = None
    detail: str
    recommended_action: str | None = None


class TreeAdmissionPackage(BaseModel):
    package_id: str = Field(default_factory=lambda: f"TAP-{uuid4().hex[:8]}")
    proposal_id: str
    current_status: TreeProposalStatus
    target_status: TreeProposalStatus
    stage: Literal["GRAY", "RELEASED"]
    ready_for_review: bool = False
    materials: list[TreeAdmissionMaterial] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    satisfied: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class ReleasedTreeRegistryEntry(BaseModel):
    registry_entry_id: str = Field(default_factory=lambda: f"RTR-{uuid4().hex[:8]}")
    proposal_id: str
    release_artifact_id: str
    release_version: str
    candidate_tree_id: str
    candidate_start_symptom: str
    applicable_scope: str = "PENDING_REVIEW"
    ttl_sha256: str
    ttl_preview_path: str | None = None
    production_ttl_path: str | None = None
    source_eval_ids: list[str] = Field(default_factory=list)
    source_review_ids: list[str] = Field(default_factory=list)
    manifest_id: str
    rollback_id: str
    registry_status: Literal["READY_FOR_TTL_WRITE", "REGISTERED", "ROLLED_BACK"] = "READY_FOR_TTL_WRITE"
    created_at: str = Field(default_factory=utc_now_iso)


class ProductionTtlAuditResult(BaseModel):
    audit_id: str = Field(default_factory=lambda: f"PTA-{uuid4().hex[:8]}")
    proposal_id: str
    release_version: str | None = None
    candidate_tree_id: str | None = None
    verdict: Literal["READY_FOR_TTL_WRITE", "BLOCKED"]
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    registry_entry: ReleasedTreeRegistryEntry | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class ProductionTtlWritePlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: f"PTWP-{uuid4().hex[:8]}")
    proposal_id: str
    registry_entry_id: str
    release_artifact_id: str
    release_version: str
    candidate_tree_id: str
    production_ttl_path: str
    backup_path: str
    generated_ttl_sha256: str
    current_ttl_sha256: str
    operation: Literal["WRITE"]
    created_at: str = Field(default_factory=utc_now_iso)


class ProductionTtlWriteResult(BaseModel):
    write_id: str = Field(default_factory=lambda: f"PTW-{uuid4().hex[:8]}")
    proposal_id: str
    registry_entry_id: str | None = None
    release_version: str | None = None
    candidate_tree_id: str | None = None
    verdict: Literal["REGISTERED", "BLOCKED"]
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    write_plan: ProductionTtlWritePlan | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class ProductionTtlRollbackResult(BaseModel):
    rollback_run_id: str = Field(default_factory=lambda: f"PTR-{uuid4().hex[:8]}")
    proposal_id: str
    registry_entry_id: str | None = None
    release_version: str | None = None
    candidate_tree_id: str | None = None
    verdict: Literal["ROLLBACK_READY", "ROLLED_BACK", "BLOCKED"]
    dry_run: bool = True
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    restored_from_backup_path: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class TreeGenerationHitlSuggestionOption(BaseModel):
    option_id: str = Field(default_factory=lambda: f"TGHSO-{uuid4().hex[:8]}")
    value: Any = None
    status: FieldStatus = FieldStatus.SUGGESTED_LOW_CONF
    rationale: str
    confidence: float = Field(default=0.3, ge=0.0, le=1.0)
    source_refs: list[str] = Field(default_factory=list)
    rag_refs: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class TreeGenerationHitlSuggestion(BaseModel):
    suggestion_id: str = Field(default_factory=lambda: f"TGHS-{uuid4().hex[:8]}")
    object_type: str
    object_id: str
    field: str
    current_status: FieldStatus
    current_value: Any = None
    reason: str
    source_refs: list[str] = Field(default_factory=list)
    rag_refs: list[str] = Field(default_factory=list)
    options: list[TreeGenerationHitlSuggestionOption] = Field(default_factory=list)
    recommended_option_id: str | None = None
    generation_summary: str = ""
    llm_model: str | None = None
    raw_output: dict[str, Any] = Field(default_factory=dict)
    raw_text: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class TreeGenerationHitlDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: f"TGHD-{uuid4().hex[:8]}")
    suggestion_id: str
    object_type: str
    object_id: str
    field: str
    action: Literal["ACCEPT_OPTION", "MANUAL_VALUE", "KEEP_CURRENT", "NEEDS_MORE_EVIDENCE", "REJECT"]
    selected_option_id: str | None = None
    value: Any = None
    reviewer: str | None = None
    rationale: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class TreeGenerationArtifact(BaseModel):
    artifact_id: str = Field(default_factory=lambda: f"TGA-{uuid4().hex[:8]}")
    job_id: str
    extraction_quality: TreeGenerationQuality = TreeGenerationQuality.LOW_CONF_DEBUG_DRAFT
    extraction_plan: OntologyExtractionPlan = Field(default_factory=OntologyExtractionPlan)
    extraction_passes: list[OntologyExtractionPass] = Field(default_factory=list)
    symptoms: list[OntologyEntityDraft] = Field(default_factory=list)
    tests: list[OntologyEntityDraft] = Field(default_factory=list)
    measures: list[OntologyEntityDraft] = Field(default_factory=list)
    transitions: list[SymptomTransitionDraft] = Field(default_factory=list)
    validation_report: TreeGenerationValidationReport | None = None
    rebuilt_fault_tree: dict[str, Any] = Field(default_factory=dict)
    stage_timings: list[dict[str, Any]] = Field(default_factory=list)
    hitl_suggestions: list[TreeGenerationHitlSuggestion] = Field(default_factory=list)
    hitl_decisions: list[TreeGenerationHitlDecision] = Field(default_factory=list)
    tree_gen_agent_reference: str = "docs/tree_gen_agent.md"
    created_at: str = Field(default_factory=utc_now_iso)


class TreeGenerationJob(BaseModel):
    job_id: str = Field(default_factory=lambda: f"TGJ-{uuid4().hex[:8]}")
    status: TreeGenerationJobStatus = TreeGenerationJobStatus.CREATED
    entrypoint: Literal["BATCH_DOCUMENTS", "WORK_ORDER_TRIGGER"] = "BATCH_DOCUMENTS"
    title: str
    description: str | None = None
    input_documents: list[TreeGenerationInputDocument] = Field(default_factory=list)
    source_work_order_id: str | None = None
    artifact: TreeGenerationArtifact | None = None
    proposal: TreeProposal | None = None
    error: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class GateResult(BaseModel):
    status: GateStatus
    blocking_reasons: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    can_generate_final_report: bool = False


class ToolCallRecord(BaseModel):
    tool_name: str
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    status: ToolStatus = ToolStatus.SUCCESS
    error: str | None = None
    latency_ms: int | None = None
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class DiagnosisReport(BaseModel):
    case_id: str
    root_cause: str | None = None
    candidate_causes: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    executed_tests: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    gate_status: GateStatus
    risk_notes: list[str] = Field(default_factory=list)
    markdown: str = ""
    created_at: str = Field(default_factory=utc_now_iso)


class ReplayRecord(BaseModel):
    state_before: dict[str, Any] = Field(default_factory=dict)
    planner_output: list[dict[str, Any]] = Field(default_factory=list)
    tool_call: dict[str, Any] = Field(default_factory=dict)
    tool_result: dict[str, Any] = Field(default_factory=dict)
    state_after: dict[str, Any] = Field(default_factory=dict)
    gate_result: dict[str, Any] = Field(default_factory=dict)
    human_decision: dict[str, Any] = Field(default_factory=dict)
    accepted: bool | None = None
    rejected_reason: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class DiagnosticState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    case_id: str = Field(default_factory=lambda: f"CASE-{uuid4().hex[:8]}")
    work_order: WorkOrder | None = None
    classification: WorkOrderClassification | None = None
    coverage_decision: CoverageDecision | None = None
    diagnosis_mode: DiagnosisMode = DiagnosisMode.PRODUCTION
    workflow_phase: DiagnosticWorkflowPhase = DiagnosticWorkflowPhase.INIT
    waiting_for_human: bool = False
    waiting_action_ids: list[str] = Field(default_factory=list)
    workflow_notes: list[str] = Field(default_factory=list)
    active_tree_id: str | None = None
    active_node_id: str | None = None
    intake_request: IntakeRequest | None = None
    intake: NormalizedPhenomenon | None = None
    matched_trees: list[FaultTree] = Field(default_factory=list)
    candidate_paths: list[DiagnosticPath] = Field(default_factory=list)
    candidate_causes: list[CandidateCause] = Field(default_factory=list)
    planned_actions: list[DiagnosticAction] = Field(default_factory=list)
    executed_tests: list[ExecutedTest] = Field(default_factory=list)
    evidence_chain: list[EvidenceItem] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    rework_risk: ReworkRiskAssessment | None = None
    gate_result: GateResult | None = None
    human_feedback: list[dict[str, Any]] = Field(default_factory=list)
    final_report: DiagnosisReport | None = None
    replay_trace: list[ReplayRecord] = Field(default_factory=list)
    data_quality_notes: list[str] = Field(default_factory=list)
    case_only_hypotheses: list[CaseOnlyHypothesis] = Field(default_factory=list)
    case_only_plan: ExploratoryDiagnosticPlan | None = None
    case_only_findings: list[ExploratoryFinding] = Field(default_factory=list)
    fault_tree_generation_request: FaultTreeGenerationRequest | None = None
    fault_tree_request_cluster: FaultTreeRequestCluster | None = None
    tree_change_proposal: TreeProposal | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    def touch(self) -> None:
        self.updated_at = utc_now_iso()
