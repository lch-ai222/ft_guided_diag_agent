from __future__ import annotations

from ft_diag_agent.models import (
    TreeGenerationArtifact,
    TreeProposal,
    TreeProposalEvalResult,
    TreeProposalReviewLog,
    TreeProposalStatus,
    TreeReleaseArtifact,
    TreeReleaseManifest,
    TreeRollbackMetadata,
    utc_now_iso,
)
from ft_diag_agent.tree_generation_eval import TREE_GENERATION_EXTRACTION_EVAL_SUITE
from ft_diag_agent.tree_proposal_eval import (
    TREE_PROPOSAL_EVAL_SUITE,
    TREE_PROPOSAL_REPLAY_SHADOW_EVAL_SUITE,
)


def build_tree_release_artifact(
    proposal: TreeProposal,
    artifact: TreeGenerationArtifact | None,
    *,
    eval_results: list[TreeProposalEvalResult] | None = None,
    review_logs: list[TreeProposalReviewLog] | None = None,
    generated_by: str | None = None,
    formal_signoff_reviewer: str | None = None,
    formal_signoff_rationale: str | None = None,
    release_version: str | None = None,
) -> TreeReleaseArtifact:
    eval_results = eval_results or []
    review_logs = review_logs or []
    version = release_version or _release_version()
    candidate_tree_id = _candidate_tree_id(proposal)
    source_eval_ids = _source_eval_ids(eval_results)
    source_eval_suites = _source_eval_suites(eval_results)
    source_review_ids = _source_review_ids(review_logs)
    generated_ttl = _ttl_preview(candidate_tree_id, proposal, artifact, version)
    ttl_diff = _ttl_diff(candidate_tree_id, proposal, artifact, generated_ttl)
    blockers = _release_blockers(
        proposal,
        artifact,
        eval_results,
        review_logs,
        generated_ttl,
        formal_signoff_reviewer=formal_signoff_reviewer,
    )
    warnings = _release_warnings(proposal, artifact)
    manifest = TreeReleaseManifest(
        proposal_id=proposal.proposal_id,
        release_version=version,
        candidate_tree_id=candidate_tree_id,
        source_status=proposal.status,
        candidate_start_symptom=proposal.candidate_start_symptom,
        applicable_scope=proposal.candidate_failure_domain or "PENDING_REVIEW",
        source_eval_ids=source_eval_ids,
        source_eval_suites=source_eval_suites,
        source_review_ids=source_review_ids,
        artifact_refs=[
            f"artifacts/{proposal.proposal_id}/artifact.json",
            f"artifacts/{proposal.proposal_id}/release/generated_ttl_preview.ttl",
            f"artifacts/{proposal.proposal_id}/release/ttl_diff.md",
            f"artifacts/{proposal.proposal_id}/release/rollback_metadata.json",
        ],
        safety_constraints=[
            "Only RELEASED_TREE can enter production diagnosis.",
            "Generated release materials do not mutate corrected_fault_tree_instances.ttl.",
            "Gate must not PASS on DRAFT_TREE, CANDIDATE_TREE, or GRAY_TREE.",
        ],
        release_notes=[
            f"Generated from TreeProposal {proposal.proposal_id}.",
            "Reviewer must inspect TTL diff, rollback metadata, eval results, and review logs before release.",
        ],
        generated_by=generated_by,
        formal_signoff_reviewer=formal_signoff_reviewer,
        formal_signoff_rationale=formal_signoff_rationale,
        formal_signoff_at=utc_now_iso() if formal_signoff_reviewer else None,
    )
    rollback = TreeRollbackMetadata(
        proposal_id=proposal.proposal_id,
        release_version=version,
        rollback_triggers=[
            "Wrong route or wrong root cause observed in production monitoring.",
            "Gate mispass or unsafe PASS risk detected.",
            "Expert review finds evidence binding or test executability regression.",
        ],
        rollback_steps=[
            "Remove the released tree version from the Released Tree registry.",
            "Restore the previous TTL registry state referenced by rollback_target.",
            "Re-run classification, diagnosis, and tree proposal eval suites.",
            "Record rollback decision and evidence in TreeProposal review logs.",
        ],
        owner=generated_by,
    )
    return TreeReleaseArtifact(
        proposal_id=proposal.proposal_id,
        manifest=manifest,
        rollback=rollback,
        ttl_diff_md=ttl_diff,
        generated_ttl_preview=generated_ttl,
        source_eval_ids=source_eval_ids,
        source_eval_suites=source_eval_suites,
        source_review_ids=source_review_ids,
        release_materials_ready=not blockers,
        blockers=blockers,
        warnings=warnings,
    )


def _release_blockers(
    proposal: TreeProposal,
    artifact: TreeGenerationArtifact | None,
    eval_results: list[TreeProposalEvalResult],
    review_logs: list[TreeProposalReviewLog],
    generated_ttl: str,
    formal_signoff_reviewer: str | None,
) -> list[str]:
    blockers: list[str] = []
    if proposal.status != TreeProposalStatus.GRAY_TREE:
        blockers.append("proposal 尚未处于 GRAY_TREE。")
    if artifact is None:
        blockers.append("缺少 TreeGenerationArtifact。")
    if not generated_ttl.strip():
        blockers.append("缺少 generated TTL preview。")
    structure_eval = _latest_eval(eval_results, TREE_PROPOSAL_EVAL_SUITE)
    if not structure_eval:
        blockers.append("缺少结构 Tree Proposal Eval。")
    elif structure_eval.unsafe_findings:
        blockers.append("结构 Tree Proposal Eval 仍有 unsafe findings。")
    extraction_eval = _latest_eval(eval_results, TREE_GENERATION_EXTRACTION_EVAL_SUITE)
    if not extraction_eval:
        blockers.append("缺少 Tree Generation Extraction Eval。")
    elif _extraction_eval_has_release_blocker(extraction_eval):
        blockers.append("Tree Generation Extraction Eval 存在发布阻塞项。")
    shadow_eval = _latest_eval(eval_results, TREE_PROPOSAL_REPLAY_SHADOW_EVAL_SUITE)
    if not shadow_eval:
        blockers.append("缺少 replay/shadow eval。")
    elif shadow_eval.unsafe_findings or shadow_eval.metrics.get("shadow_ready") is not True:
        blockers.append("replay/shadow eval 未通过。")
    if not _has_gray_approval(review_logs):
        blockers.append("缺少 CANDIDATE_TREE -> GRAY_TREE 专家审核日志。")
    if not formal_signoff_reviewer:
        blockers.append("缺少专家正式发布签核。")
    return list(dict.fromkeys(blockers))


def _release_warnings(
    proposal: TreeProposal,
    artifact: TreeGenerationArtifact | None,
) -> list[str]:
    warnings: list[str] = []
    if proposal.candidate_failure_domain is None:
        warnings.append("缺少 candidate_failure_domain，release manifest applicable_scope 仍需专家补充。")
    if artifact and not artifact.measures:
        warnings.append("artifact 缺少 OntologyMeasure，发布前建议补齐处置措施。")
    return warnings


def _ttl_preview(
    candidate_tree_id: str,
    proposal: TreeProposal,
    artifact: TreeGenerationArtifact | None,
    version: str,
) -> str:
    if not artifact:
        return ""
    symptom_lines = []
    for symptom in artifact.symptoms:
        subject = _resource("FailureSymptom", symptom.entity_id)
        symptom_lines.append(
            "\n".join(
                [
                    f"{subject} a qtl:FailureSymptom ;",
                    f'  qtl:symptomId "{_escape_ttl(symptom.entity_id)}" ;',
                    f'  qtl:symptomName "{_escape_ttl(symptom.name or symptom.entity_id)}" ;',
                    f'  qtl:symptomNameStatus "{_escape_ttl(str(symptom.name_status))}" ;',
                    f'  qtl:symptomLevel "{_escape_ttl(symptom.level or "inner")}" ;',
                    f'  qtl:symptomDesc "{_escape_ttl(symptom.description or "")}" ;',
                    f'  qtl:symptomDescStatus "{_escape_ttl(str(symptom.description_status))}" .',
                ]
            )
        )
    test_lines = []
    for test in artifact.tests:
        subject = _resource("OntologyTest", test.entity_id)
        test_lines.append(
            "\n".join(
                [
                    f"{subject} a qtl:OntologyTest ;",
                    f'  qtl:testId "{_escape_ttl(test.entity_id)}" ;',
                    f'  qtl:testName "{_escape_ttl(test.name or test.entity_id)}" ;',
                    f'  qtl:testNameStatus "{_escape_ttl(str(test.name_status))}" ;',
                    f'  qtl:testDesc "{_escape_ttl(test.description or "")}" ;',
                    f'  qtl:testDescStatus "{_escape_ttl(str(test.description_status))}" .',
                ]
            )
        )
    transition_lines = []
    for transition in artifact.transitions:
        test_refs = ", ".join(_resource("OntologyTest", test_id) for test_id in transition.test_ids)
        transition_lines.append(
            "\n".join(
                [
                    f"{_resource('SymptomTransition', transition.transition_id)} a qtl:SymptomTransition ;",
                    f"  qtl:transitionSource {_resource('FailureSymptom', transition.source_id)} ;",
                    f"  qtl:transitionTarget {_resource('FailureSymptom', transition.target_id)} ;",
                    f"  qtl:testId {test_refs or 'qtl:OntologyTest_MISSING_TEST'} ;",
                    f'  qtl:condition "{_escape_ttl(transition.condition or "")}" ;',
                    f'  qtl:conditionStatus "{_escape_ttl(str(transition.condition_status))}" ;',
                    f'  qtl:transitionDesc "{_escape_ttl(transition.description or "")}" ;',
                    f'  qtl:transitionDescStatus "{_escape_ttl(str(transition.description_status))}" .',
                ]
            )
        )
    symptom_ids = ", ".join(_resource("FailureSymptom", symptom.entity_id) for symptom in artifact.symptoms)
    tree = "\n".join(
        [
            f"{_resource('FaultTree', candidate_tree_id)} a qtl:FaultTree ;",
            f'  qtl:treeId "{_escape_ttl(candidate_tree_id)}" ;',
            f'  qtl:treeName "{_escape_ttl(proposal.candidate_start_symptom)}" ;',
            f'  qtl:applicableScope "{_escape_ttl(proposal.candidate_failure_domain or "PENDING_REVIEW")}" ;',
            f'  qtl:version "{_escape_ttl(version)}" ;',
            "  qtl:hasSymptom qtl:FailureSymptom_MISSING_SYMPTOM ."
            if not symptom_ids
            else f"  qtl:hasSymptom {symptom_ids} .",
        ]
    )
    return "\n\n".join(
        [
            "@prefix qtl: <http://lianshan.ai/ontology/qlt_fta#> .",
            *symptom_lines,
            *test_lines,
            *transition_lines,
            tree,
        ]
    )


def _ttl_diff(
    candidate_tree_id: str,
    proposal: TreeProposal,
    artifact: TreeGenerationArtifact | None,
    generated_ttl: str,
) -> str:
    symptom_count = len(artifact.symptoms) if artifact else 0
    test_count = len(artifact.tests) if artifact else 0
    transition_count = len(artifact.transitions) if artifact else 0
    return "\n".join(
        [
            f"# TTL Release Diff Preview for {proposal.proposal_id}",
            "",
            "This is a review artifact only. It has not been written to production TTL.",
            "",
            "## Proposed Additions",
            "",
            f"- FaultTree: `{candidate_tree_id}`",
            f"- Start symptom: `{proposal.candidate_start_symptom}`",
            f"- Failure domain: `{proposal.candidate_failure_domain or 'PENDING_REVIEW'}`",
            f"- FailureSymptom count: `{symptom_count}`",
            f"- OntologyTest count: `{test_count}`",
            f"- SymptomTransition count: `{transition_count}`",
            "",
            "## Generated TTL Preview",
            "",
            "```ttl",
            generated_ttl,
            "```",
        ]
    )


def _latest_eval(
    eval_results: list[TreeProposalEvalResult],
    eval_suite: str,
) -> TreeProposalEvalResult | None:
    matching = [item for item in eval_results if item.eval_suite == eval_suite]
    if not matching:
        return None
    return sorted(matching, key=lambda item: (item.created_at, item.eval_id))[-1]


def _has_gray_approval(review_logs: list[TreeProposalReviewLog]) -> bool:
    return any(
        log.decision == "APPROVE"
        and log.from_status == TreeProposalStatus.CANDIDATE_TREE
        and log.to_status == TreeProposalStatus.GRAY_TREE
        for log in review_logs
    )


def _extraction_eval_has_release_blocker(eval_result: TreeProposalEvalResult) -> bool:
    blocking_findings = {
        "ARTIFACT_MISSING",
        "ONTOLOGY_STRUCTURE_BLOCKED",
        "PATH_COHERENCE_BLOCKED",
        "HALLUCINATION_HIGH",
    }
    if any(finding in blocking_findings for finding in eval_result.unsafe_findings):
        return True
    metrics = eval_result.metrics
    if metrics.get("candidate_ready") is False:
        return True
    hallucination_rate = metrics.get("hallucination_rate")
    if isinstance(hallucination_rate, int | float) and hallucination_rate > 0.2:
        return True
    ontology_score = metrics.get("ontology_structure_score")
    if isinstance(ontology_score, int | float) and ontology_score < 1.0:
        return True
    path_score = metrics.get("path_coherence_score")
    if isinstance(path_score, int | float) and path_score < 1.0:
        return True
    return False


def _source_eval_ids(eval_results: list[TreeProposalEvalResult]) -> list[str]:
    return [item.eval_id for item in eval_results if not item.unsafe_findings]


def _source_eval_suites(eval_results: list[TreeProposalEvalResult]) -> list[str]:
    return list(dict.fromkeys(item.eval_suite for item in eval_results if not item.unsafe_findings))


def _source_review_ids(review_logs: list[TreeProposalReviewLog]) -> list[str]:
    return [item.review_id for item in review_logs if item.decision == "APPROVE"]


def _candidate_tree_id(proposal: TreeProposal) -> str:
    return "FT_RELEASE_" + "".join(ch for ch in proposal.proposal_id.upper() if ch.isalnum())[-12:]


def _release_version() -> str:
    return "v" + utc_now_iso().replace("-", "").replace(":", "").split(".")[0]


def _escape_ttl(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _resource(entity_type: str, entity_id: str) -> str:
    return f"qtl:{entity_type}_{_safe_ttl_id(entity_id)}"


def _safe_ttl_id(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(value).strip())
    text = text.strip("_") or "UNNAMED"
    if text[0].isdigit():
        text = f"ID_{text}"
    return text
