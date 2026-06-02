from __future__ import annotations

from ft_diag_agent.models import (
    FieldStatus,
    TreeGenerationArtifact,
    TreeProposal,
    TreeProposalEvalResult,
)
from ft_diag_agent.tree_generation import generation_hitl_items, validate_tree_generation_artifact
from ft_diag_agent.tree_proposals import TreeProposalStore

TREE_PROPOSAL_EVAL_SUITE = "tree_proposal_v1"


def run_tree_proposal_eval(
    store: TreeProposalStore,
    proposal_id: str,
    *,
    persist: bool = True,
) -> TreeProposalEvalResult | None:
    proposal = store.get_proposal(proposal_id)
    if not proposal:
        return None
    artifact = store.load_artifact_snapshot(proposal_id)
    result = evaluate_tree_proposal(proposal, artifact)
    if persist:
        store.append_eval_result(result)
    return result


def evaluate_tree_proposal(
    proposal: TreeProposal,
    artifact: TreeGenerationArtifact | None,
) -> TreeProposalEvalResult:
    if artifact is None:
        return TreeProposalEvalResult(
            proposal_id=proposal.proposal_id,
            eval_suite=TREE_PROPOSAL_EVAL_SUITE,
            status_at_eval=proposal.status,
            metrics={
                "schema_valid": False,
                "artifact_present": False,
                "candidate_ready": False,
            },
            unsafe_findings=["ARTIFACT_MISSING"],
            failure_cases=[{"rule_id": "ARTIFACT_MISSING", "message": "缺少 proposal artifact 快照。"}],
        )

    validation = artifact.validation_report or validate_tree_generation_artifact(artifact)
    hitl_items = generation_hitl_items(artifact)
    roots_with_tests = _roots_with_tests(artifact)
    evidence_binding_rate = _evidence_binding_rate(artifact)
    hitl_confirmed_rate = _hitl_confirmed_rate(artifact)
    missing_test_transition_count = sum(1 for transition in artifact.transitions if not transition.test_ids)
    unsafe_findings = _unsafe_findings(
        validation_error_count=len(validation.errors),
        root_count=validation.root_symptom_count,
        test_count=validation.test_count,
        transition_count=validation.transition_count,
        missing_test_transition_count=missing_test_transition_count,
        hitl_pending_count=len(hitl_items),
        evidence_binding_rate=evidence_binding_rate,
    )
    metrics = {
        "artifact_present": True,
        "schema_valid": validation.is_valid,
        "candidate_ready": not unsafe_findings,
        "validation_error_count": len(validation.errors),
        "validation_warning_count": len(validation.warnings),
        "start_count": validation.start_symptom_count,
        "root_count": validation.root_symptom_count,
        "test_count": validation.test_count,
        "transition_count": validation.transition_count,
        "roots_with_tests_rate": roots_with_tests,
        "missing_test_transition_count": missing_test_transition_count,
        "evidence_binding_rate": evidence_binding_rate,
        "hitl_confirmed_rate": hitl_confirmed_rate,
        "hitl_pending_count": len(hitl_items),
        "hitl_suggestion_count": len(artifact.hitl_suggestions),
        "hitl_decision_count": len(artifact.hitl_decisions),
    }
    failure_cases = [
        {"rule_id": finding, "message": _finding_message(finding)}
        for finding in unsafe_findings
    ]
    return TreeProposalEvalResult(
        proposal_id=proposal.proposal_id,
        eval_suite=TREE_PROPOSAL_EVAL_SUITE,
        status_at_eval=proposal.status,
        metrics=metrics,
        failure_cases=failure_cases,
        unsafe_findings=unsafe_findings,
    )


def _roots_with_tests(artifact: TreeGenerationArtifact) -> float | None:
    root_ids = {symptom.entity_id for symptom in artifact.symptoms if symptom.level == "root"}
    if not root_ids:
        return None
    roots_with_tests = {
        transition.target_id
        for transition in artifact.transitions
        if transition.target_id in root_ids and transition.test_ids
    }
    return len(roots_with_tests) / len(root_ids)


def _evidence_binding_rate(artifact: TreeGenerationArtifact) -> float | None:
    entities = [*artifact.symptoms, *artifact.tests, *artifact.measures]
    transitions = artifact.transitions
    total = len(entities) + len(transitions)
    if total == 0:
        return None
    bound = sum(1 for item in entities if item.source_refs or item.chunk_ids)
    bound += sum(1 for item in transitions if item.source_refs or item.chunk_ids)
    return bound / total


def _hitl_confirmed_rate(artifact: TreeGenerationArtifact) -> float | None:
    statuses: list[FieldStatus] = []
    for entity in [*artifact.symptoms, *artifact.tests, *artifact.measures]:
        statuses.extend([entity.name_status, entity.description_status])
    for transition in artifact.transitions:
        statuses.extend([transition.condition_status, transition.description_status])
    review_statuses = [
        status
        for status in statuses
        if status in {FieldStatus.CONFIRMED, FieldStatus.VERIFIED, FieldStatus.EXTRACTED_INFERRED, FieldStatus.MISSING}
    ]
    if not review_statuses:
        return None
    confirmed = sum(1 for status in review_statuses if status in {FieldStatus.CONFIRMED, FieldStatus.VERIFIED})
    return confirmed / len(review_statuses)


def _unsafe_findings(
    *,
    validation_error_count: int,
    root_count: int,
    test_count: int,
    transition_count: int,
    missing_test_transition_count: int,
    hitl_pending_count: int,
    evidence_binding_rate: float | None,
) -> list[str]:
    findings: list[str] = []
    if validation_error_count:
        findings.append("VALIDATION_ERRORS")
    if root_count < 1:
        findings.append("ROOT_MISSING")
    if test_count < 1:
        findings.append("TEST_MISSING")
    if transition_count < 1:
        findings.append("TRANSITION_MISSING")
    if missing_test_transition_count:
        findings.append("TRANSITION_TEST_MISSING")
    if hitl_pending_count:
        findings.append("HITL_PENDING")
    if evidence_binding_rate is None or evidence_binding_rate < 0.5:
        findings.append("EVIDENCE_BINDING_LOW")
    return findings


def _finding_message(finding: str) -> str:
    messages = {
        "VALIDATION_ERRORS": "结构校验仍有 ERROR。",
        "ROOT_MISSING": "缺少 root FailureSymptom。",
        "TEST_MISSING": "缺少 OntologyTest。",
        "TRANSITION_MISSING": "缺少 SymptomTransition。",
        "TRANSITION_TEST_MISSING": "存在未绑定 test 的 transition。",
        "HITL_PENDING": "仍有 EXTRACTED_INFERRED 或 MISSING 字段未确认。",
        "EVIDENCE_BINDING_LOW": "实体/关系证据绑定率低于 50%。",
        "ARTIFACT_MISSING": "缺少 proposal artifact 快照。",
    }
    return messages.get(finding, finding)
