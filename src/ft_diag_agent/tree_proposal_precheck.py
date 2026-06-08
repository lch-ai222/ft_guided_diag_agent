from __future__ import annotations

from ft_diag_agent.models import (
    TreeAdmissionPackage,
    TreeGenerationArtifact,
    TreeProposal,
    TreeProposalCaseLink,
    TreeProposalEvalResult,
    TreeProposalPromotionPrecheck,
    TreeProposalReviewLog,
    TreeProposalStatus,
    TreeReleaseArtifact,
)
from ft_diag_agent.tree_admission import build_gray_admission_package, build_release_admission_package
from ft_diag_agent.tree_generation_eval import TREE_GENERATION_EXTRACTION_EVAL_SUITE
from ft_diag_agent.tree_proposal_analytics import TreeProposalAggregateReport
from ft_diag_agent.tree_proposal_eval import (
    TREE_PROPOSAL_EVAL_SUITE,
    evaluate_tree_proposal,
)

MIN_DRAFT_SUPPORT_CASES = 3
MIN_DRAFT_EVIDENCE_BINDING_RATE = 0.6


def assess_tree_proposal_promotion(
    proposal: TreeProposal,
    *,
    artifact: TreeGenerationArtifact | None = None,
    case_links: list[TreeProposalCaseLink] | None = None,
    eval_results: list[TreeProposalEvalResult] | None = None,
    review_logs: list[TreeProposalReviewLog] | None = None,
    release_artifact: TreeReleaseArtifact | None = None,
    aggregate_report: TreeProposalAggregateReport | None = None,
) -> TreeProposalPromotionPrecheck:
    target_status = _target_status(proposal.status)
    if target_status is None:
        return TreeProposalPromotionPrecheck(
            proposal_id=proposal.proposal_id,
            current_status=proposal.status,
            target_status=None,
            verdict="NOT_APPLICABLE",
            recommended_actions=["当前状态没有可执行的自动预审晋升目标。"],
        )
    if proposal.status == TreeProposalStatus.DRAFT_TREE:
        return _assess_draft_to_candidate(
            proposal,
            target_status,
            artifact=artifact,
            case_links=case_links or [],
            eval_results=eval_results or [],
            aggregate_report=aggregate_report,
        )
    if proposal.status == TreeProposalStatus.CANDIDATE_TREE:
        return _assess_candidate_to_gray(
            proposal,
            target_status,
            artifact=artifact,
            case_links=case_links or [],
            eval_results=eval_results or [],
            review_logs=review_logs or [],
            aggregate_report=aggregate_report,
        )
    if proposal.status == TreeProposalStatus.GRAY_TREE:
        return _assess_gray_to_released(
            proposal,
            target_status,
            release_artifact=release_artifact,
            eval_results=eval_results or [],
            review_logs=review_logs or [],
            aggregate_report=aggregate_report,
        )
    return TreeProposalPromotionPrecheck(
        proposal_id=proposal.proposal_id,
        current_status=proposal.status,
        target_status=target_status,
        verdict="BLOCKED",
        blockers=[f"不支持从 {proposal.status} 执行预审晋升。"],
        recommended_actions=["请通过人工审核确认该状态是否仍应保留。"],
    )


def _assess_draft_to_candidate(
    proposal: TreeProposal,
    target_status: TreeProposalStatus,
    *,
    artifact: TreeGenerationArtifact | None,
    case_links: list[TreeProposalCaseLink],
    eval_results: list[TreeProposalEvalResult],
    aggregate_report: TreeProposalAggregateReport | None,
) -> TreeProposalPromotionPrecheck:
    blockers: list[str] = []
    warnings: list[str] = []
    satisfied: list[str] = []
    recommended_actions: list[str] = []
    latest_eval = _latest_eval(eval_results, TREE_PROPOSAL_EVAL_SUITE)
    latest_extraction_eval = _latest_eval(eval_results, TREE_GENERATION_EXTRACTION_EVAL_SUITE)
    live_eval = evaluate_tree_proposal(proposal, artifact) if artifact else None
    effective_eval = latest_eval or live_eval
    metrics = dict(effective_eval.metrics) if effective_eval else {}
    if latest_extraction_eval:
        metrics.update(
            {
                f"extraction_{key}": value
                for key, value in latest_extraction_eval.metrics.items()
                if key
                not in {
                    "source_fact_rows",
                    "artifact_grounding_rows",
                    "path_coherence_rows",
                }
            }
        )

    _require(
        not _is_missing_text(proposal.candidate_start_symptom),
        "入口现象已明确。",
        "入口现象缺失或仍是 MISSING 占位。",
        "补充或确认 start 入口异常。",
        blockers,
        satisfied,
        recommended_actions,
    )
    _require(
        bool(proposal.root_cause_families),
        "已存在候选 root cause family。",
        "缺少候选 root cause family。",
        "补充至少一个候选 root cause family。",
        blockers,
        satisfied,
        recommended_actions,
    )
    _require(
        bool(proposal.candidate_tests),
        "已存在候选检查项。",
        "缺少候选检查项。",
        "补充每个关键 root 对应的可执行检查项。",
        blockers,
        satisfied,
        recommended_actions,
    )
    _require(
        bool(proposal.evidence_ids or proposal.source_refs),
        "已绑定来源证据或 source refs。",
        "缺少 evidence_ids/source_refs。",
        "补充原文、工单、RAG 或人工确认来源引用。",
        blockers,
        satisfied,
        recommended_actions,
    )
    case_count = _support_case_count(proposal, case_links)
    _require(
        case_count > 0,
        "已关联 source case 或 case link。",
        "缺少 source cases / TreeProposalCaseLink。",
        "从诊断 replay、case-only request 或人工审核补充关联案例。",
        blockers,
        satisfied,
        recommended_actions,
    )
    if case_count and case_count < MIN_DRAFT_SUPPORT_CASES:
        warnings.append(f"支持案例数 {case_count}/{MIN_DRAFT_SUPPORT_CASES}，低于建议门槛。")
        recommended_actions.append("继续收集同类 case-only 工单或补充历史案例证据。")
    elif case_count >= MIN_DRAFT_SUPPORT_CASES:
        satisfied.append(f"支持案例数达到建议门槛：{case_count}/{MIN_DRAFT_SUPPORT_CASES}。")

    if artifact is None:
        blockers.append("缺少 TreeGenerationArtifact，本体草案尚不能做结构预审。")
        recommended_actions.append("先运行树生成/本体建模，生成 artifact 快照后再提交 CANDIDATE_TREE 审核。")
    else:
        satisfied.append("已存在 TreeGenerationArtifact 快照。")

    if effective_eval is None:
        blockers.append("缺少 Tree Proposal Eval 结果。")
        recommended_actions.append("运行 Tree Proposal Eval。")
    else:
        if effective_eval.unsafe_findings:
            blockers.extend(_eval_blocker_message(item) for item in effective_eval.unsafe_findings)
            recommended_actions.append("先修复 Tree Proposal Eval unsafe findings，再提交晋升审核。")
        else:
            satisfied.append("Tree Proposal Eval 无 unsafe findings。")
        evidence_rate = metrics.get("evidence_binding_rate")
        if isinstance(evidence_rate, float) and evidence_rate < MIN_DRAFT_EVIDENCE_BINDING_RATE:
            warnings.append(f"证据绑定率 {evidence_rate:.0%}，低于建议阈值 {MIN_DRAFT_EVIDENCE_BINDING_RATE:.0%}。")
            recommended_actions.append("提高实体、检查项和 transition 的 source_refs/chunk_ids 覆盖。")
        if metrics.get("hitl_pending_count", 0):
            blockers.append(f"仍有 {metrics.get('hitl_pending_count')} 个树生成 HITL 待确认字段。")
            recommended_actions.append("先完成 MISSING / EXTRACTED_INFERRED 字段确认。")

    if latest_extraction_eval is None:
        blockers.append("缺少 Tree Generation Extraction Eval 结果。")
        recommended_actions.append("运行抽取质量评测 tree_generation_extraction_v1。")
    else:
        blocking_findings = {
            "ONTOLOGY_STRUCTURE_BLOCKED",
            "PATH_COHERENCE_BLOCKED",
            "HALLUCINATION_HIGH",
            "ARTIFACT_MISSING",
        }
        extraction_blockers = [item for item in latest_extraction_eval.unsafe_findings if item in blocking_findings]
        if extraction_blockers:
            blockers.extend(_extraction_eval_blocker_message(item) for item in extraction_blockers)
            recommended_actions.append("先修复抽取质量评测 blocker，再提交 CANDIDATE_TREE 审核。")
        else:
            satisfied.append("Tree Generation Extraction Eval 无结构/幻觉/链路阻塞项。")
        if "GROUNDING_LOW" in latest_extraction_eval.unsafe_findings:
            warnings.append("抽取质量评测提示 grounding 覆盖不足，建议补充 source_refs/chunk_ids。")
            recommended_actions.append("提高实体、检查项和 transition 的原文 grounding 覆盖。")
        recall_status = latest_extraction_eval.metrics.get("source_fact_recall_status")
        source_recall = latest_extraction_eval.metrics.get("source_fact_recall")
        if recall_status == "not_available":
            warnings.append("source_fact_recall 暂不可用；当前资料不足以做原文事实召回近似评估。")
        elif isinstance(source_recall, float) and source_recall < 0.6:
            warnings.append(f"原文事实召回率 {source_recall:.0%}，建议补充漏抽检查。")
            recommended_actions.append("复核 source_fact_rows 中 covered=false 的原文事实。")

    return _precheck(
        proposal,
        target_status,
        blockers=[*blockers, *(aggregate_report.blockers if aggregate_report else [])],
        warnings=[*warnings, *(aggregate_report.warnings if aggregate_report else [])],
        satisfied=[*satisfied, *(aggregate_report.satisfied if aggregate_report else [])],
        recommended_actions=[
            *recommended_actions,
            *(aggregate_report.recommended_actions if aggregate_report else []),
        ],
        metrics={
            **metrics,
            "support_case_count": case_count,
            **(aggregate_report.metrics if aggregate_report else {}),
        },
    )


def _assess_candidate_to_gray(
    proposal: TreeProposal,
    target_status: TreeProposalStatus,
    *,
    artifact: TreeGenerationArtifact | None,
    case_links: list[TreeProposalCaseLink],
    eval_results: list[TreeProposalEvalResult],
    review_logs: list[TreeProposalReviewLog],
    aggregate_report: TreeProposalAggregateReport | None,
) -> TreeProposalPromotionPrecheck:
    package = build_gray_admission_package(
        proposal,
        artifact=artifact,
        case_links=case_links,
        eval_results=eval_results,
        review_logs=review_logs,
    )
    return _precheck_from_package(proposal, target_status, package, aggregate_report=aggregate_report)


def _assess_gray_to_released(
    proposal: TreeProposal,
    target_status: TreeProposalStatus,
    *,
    release_artifact: TreeReleaseArtifact | None,
    eval_results: list[TreeProposalEvalResult],
    review_logs: list[TreeProposalReviewLog],
    aggregate_report: TreeProposalAggregateReport | None,
) -> TreeProposalPromotionPrecheck:
    package = build_release_admission_package(
        proposal,
        eval_results=eval_results,
        review_logs=review_logs,
        release_artifact=release_artifact,
    )
    return _precheck_from_package(proposal, target_status, package, aggregate_report=aggregate_report)


def _precheck(
    proposal: TreeProposal,
    target_status: TreeProposalStatus,
    *,
    blockers: list[str],
    warnings: list[str],
    satisfied: list[str],
    recommended_actions: list[str],
    metrics: dict,
) -> TreeProposalPromotionPrecheck:
    clean_blockers = _unique(blockers)
    clean_warnings = _unique(warnings)
    verdict = "BLOCKED" if clean_blockers else "NEEDS_MORE_EVIDENCE" if clean_warnings else "READY_FOR_REVIEW"
    return TreeProposalPromotionPrecheck(
        proposal_id=proposal.proposal_id,
        current_status=proposal.status,
        target_status=target_status,
        verdict=verdict,
        blockers=clean_blockers,
        warnings=clean_warnings,
        satisfied=_unique(satisfied),
        recommended_actions=_unique(recommended_actions),
        metrics=metrics,
    )


def _precheck_from_package(
    proposal: TreeProposal,
    target_status: TreeProposalStatus,
    package: TreeAdmissionPackage,
    *,
    aggregate_report: TreeProposalAggregateReport | None = None,
) -> TreeProposalPromotionPrecheck:
    return _precheck(
        proposal,
        target_status,
        blockers=[*package.blockers, *(aggregate_report.blockers if aggregate_report else [])],
        warnings=[*package.warnings, *(aggregate_report.warnings if aggregate_report else [])],
        satisfied=[*package.satisfied, *(aggregate_report.satisfied if aggregate_report else [])],
        recommended_actions=[
            *package.recommended_actions,
            *(aggregate_report.recommended_actions if aggregate_report else []),
        ],
        metrics={
            **package.metrics,
            "admission_package_id": package.package_id,
            **(aggregate_report.metrics if aggregate_report else {}),
        },
    )


def _target_status(status: TreeProposalStatus) -> TreeProposalStatus | None:
    if status == TreeProposalStatus.DRAFT_TREE:
        return TreeProposalStatus.CANDIDATE_TREE
    if status == TreeProposalStatus.CANDIDATE_TREE:
        return TreeProposalStatus.GRAY_TREE
    if status == TreeProposalStatus.GRAY_TREE:
        return TreeProposalStatus.RELEASED_TREE
    return None


def _require(
    condition: bool,
    ok: str,
    blocker: str,
    action: str,
    blockers: list[str],
    satisfied: list[str],
    actions: list[str],
) -> None:
    if condition:
        satisfied.append(ok)
    else:
        blockers.append(blocker)
        actions.append(action)


def _support_case_count(proposal: TreeProposal, case_links: list[TreeProposalCaseLink]) -> int:
    return len({*proposal.source_case_ids, *(link.case_id for link in case_links)})


def _is_missing_text(value: str | None) -> bool:
    if not value:
        return True
    normalized = value.strip().lower()
    return normalized.startswith("missing") or normalized.startswith("missing ") or "missing 占位" in normalized


def _eval_blocker_message(finding: str) -> str:
    messages = {
        "VALIDATION_ERRORS": "Tree Proposal Eval 阻塞：结构校验仍有 ERROR。",
        "ROOT_MISSING": "Tree Proposal Eval 阻塞：缺少 root FailureSymptom。",
        "TEST_MISSING": "Tree Proposal Eval 阻塞：缺少 OntologyTest。",
        "TRANSITION_MISSING": "Tree Proposal Eval 阻塞：缺少 SymptomTransition。",
        "TRANSITION_TEST_MISSING": "Tree Proposal Eval 阻塞：存在未绑定 test 的 transition。",
        "HITL_PENDING": "Tree Proposal Eval 阻塞：仍有待确认 HITL 字段。",
        "EVIDENCE_BINDING_LOW": "Tree Proposal Eval 阻塞：证据绑定率过低。",
        "ARTIFACT_MISSING": "Tree Proposal Eval 阻塞：缺少 artifact 快照。",
    }
    return messages.get(finding, f"Tree Proposal Eval 阻塞：{finding}")


def _extraction_eval_blocker_message(finding: str) -> str:
    messages = {
        "ARTIFACT_MISSING": "Extraction Eval 阻塞：缺少 artifact 快照。",
        "ONTOLOGY_STRUCTURE_BLOCKED": "Extraction Eval 阻塞：本体结构评分未达晋升门槛。",
        "PATH_COHERENCE_BLOCKED": "Extraction Eval 阻塞：L1/L2/L3 诊断链路逻辑不通顺。",
        "HALLUCINATION_HIGH": "Extraction Eval 阻塞：未确认且缺少来源支撑的抽取项比例过高。",
    }
    return messages.get(finding, f"Extraction Eval 阻塞：{finding}")


def _latest_eval(
    eval_results: list[TreeProposalEvalResult],
    eval_suite: str,
) -> TreeProposalEvalResult | None:
    matching = [item for item in eval_results if item.eval_suite == eval_suite]
    if not matching:
        return None
    return sorted(matching, key=lambda item: (item.created_at, item.eval_id))[-1]


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))
