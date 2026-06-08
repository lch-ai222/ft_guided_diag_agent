from __future__ import annotations

from typing import Any, Literal

from ft_diag_agent.models import (
    TreeAdmissionMaterial,
    TreeAdmissionPackage,
    TreeGenerationArtifact,
    TreeProposal,
    TreeProposalCaseLink,
    TreeProposalEvalResult,
    TreeProposalReviewLog,
    TreeProposalStatus,
    TreeReleaseArtifact,
)
from ft_diag_agent.tree_generation_eval import TREE_GENERATION_EXTRACTION_EVAL_SUITE
from ft_diag_agent.tree_proposal_eval import (
    TREE_PROPOSAL_EVAL_SUITE,
    TREE_PROPOSAL_REPLAY_SHADOW_EVAL_SUITE,
)

MIN_GRAY_SUPPORT_CASES = 3
MIN_GRAY_EVIDENCE_BINDING_RATE = 0.6


def build_gray_admission_package(
    proposal: TreeProposal,
    *,
    artifact: TreeGenerationArtifact | None = None,
    case_links: list[TreeProposalCaseLink] | None = None,
    eval_results: list[TreeProposalEvalResult] | None = None,
    review_logs: list[TreeProposalReviewLog] | None = None,
) -> TreeAdmissionPackage:
    case_links = case_links or []
    eval_results = eval_results or []
    review_logs = review_logs or []
    structure_eval = _latest_eval(eval_results, TREE_PROPOSAL_EVAL_SUITE)
    shadow_eval = _latest_eval(eval_results, TREE_PROPOSAL_REPLAY_SHADOW_EVAL_SUITE)
    support_case_count = len({*proposal.source_case_ids, *(link.case_id for link in case_links)})
    metrics: dict[str, Any] = {
        "support_case_count": support_case_count,
    }
    if structure_eval:
        metrics.update({f"structure_{key}": value for key, value in structure_eval.metrics.items()})
    if shadow_eval:
        metrics.update({f"shadow_{key}": value for key, value in shadow_eval.metrics.items()})
    materials = [
        _material(
            "GRAY_ARTIFACT",
            "GRAY",
            "TreeGenerationArtifact",
            "ARTIFACT",
            bool(artifact),
            "已存在 TreeGenerationArtifact，可审查 proposed tree。",
            "缺少 TreeGenerationArtifact。",
            source_path=f"artifacts/{proposal.proposal_id}/artifact.json" if artifact else None,
            action="先运行树生成/本体建模，生成 artifact 快照。",
        ),
        _material(
            "GRAY_SOURCE_CASES",
            "GRAY",
            "Source cases / case links",
            "CASE_LINK",
            support_case_count > 0,
            f"已关联 {support_case_count} 个 source case。",
            "缺少 source cases / TreeProposalCaseLink。",
            status_override="WARNING" if 0 < support_case_count < MIN_GRAY_SUPPORT_CASES else None,
            warning_detail=f"支持案例数 {support_case_count}/{MIN_GRAY_SUPPORT_CASES}，低于建议门槛。",
            action="继续收集同类 case-only 工单或补充历史案例证据。",
        ),
        _material(
            "GRAY_EVIDENCE",
            "GRAY",
            "Evidence / source refs",
            "EVIDENCE",
            bool(proposal.evidence_ids or proposal.source_refs),
            "已绑定来源证据或 source refs。",
            "缺少 evidence_ids/source_refs。",
            action="补充原文、工单、RAG 或人工确认来源引用。",
        ),
        _structure_eval_material(structure_eval),
        _shadow_eval_material(shadow_eval),
        _material(
            "GRAY_CANDIDATE_APPROVAL",
            "GRAY",
            "DRAFT -> CANDIDATE review log",
            "REVIEW_LOG",
            _has_candidate_approval(review_logs),
            "已存在 DRAFT_TREE -> CANDIDATE_TREE 专家审核日志。",
            "缺少 DRAFT_TREE -> CANDIDATE_TREE 专家审核日志。",
            action="先完成候选树专家初审并记录审核日志。",
        ),
        _material(
            "GRAY_SCOPE",
            "GRAY",
            "Applicable scope",
            "PROPOSAL_FIELD",
            bool(proposal.candidate_failure_domain),
            f"candidate_failure_domain={proposal.candidate_failure_domain}",
            "缺少 candidate_failure_domain，GRAY_TREE 适用边界仍需专家补充。",
            status_override="WARNING" if not proposal.candidate_failure_domain else None,
            action="补充适用车型、工厂、工位和边界条件。",
        ),
    ]
    return _package(
        proposal,
        TreeProposalStatus.GRAY_TREE,
        "GRAY",
        materials,
        metrics=metrics,
    )


def build_release_admission_package(
    proposal: TreeProposal,
    *,
    eval_results: list[TreeProposalEvalResult] | None = None,
    review_logs: list[TreeProposalReviewLog] | None = None,
    release_artifact: TreeReleaseArtifact | None = None,
) -> TreeAdmissionPackage:
    eval_results = eval_results or []
    review_logs = review_logs or []
    extraction_eval = _latest_eval(eval_results, TREE_GENERATION_EXTRACTION_EVAL_SUITE)
    shadow_eval = _latest_eval(eval_results, TREE_PROPOSAL_REPLAY_SHADOW_EVAL_SUITE)
    metrics: dict[str, Any] = {}
    if extraction_eval:
        metrics.update({f"extraction_{key}": value for key, value in extraction_eval.metrics.items()})
    if shadow_eval:
        metrics.update({f"shadow_{key}": value for key, value in shadow_eval.metrics.items()})
    if release_artifact:
        metrics.update(
            {
                "release_materials_ready": release_artifact.release_materials_ready,
                "release_blocker_count": len(release_artifact.blockers),
                "release_warning_count": len(release_artifact.warnings),
            }
        )
    materials = [
        _material(
            "RELEASE_ARTIFACT",
            "RELEASED",
            "Release artifact",
            "RELEASE_ARTIFACT",
            bool(release_artifact),
            "已生成 release artifact。",
            "缺少 release artifact。",
            source_path=f"artifacts/{proposal.proposal_id}/release/release_artifact.json"
            if release_artifact
            else None,
            action="生成发布前审核材料包。",
        ),
        _release_manifest_material(proposal, release_artifact),
        _rollback_material(proposal, release_artifact),
        _ttl_diff_material(proposal, release_artifact),
        _release_internal_blocker_material(release_artifact),
        _extraction_eval_material(extraction_eval),
        _material(
            "RELEASE_SHADOW_VALIDATION",
            "RELEASED",
            "Golden / shadow validation",
            "EVAL_RESULT",
            bool(shadow_eval and not shadow_eval.unsafe_findings and shadow_eval.metrics.get("shadow_ready") is True),
            "golden/shadow validation 记录可追溯。",
            "缺少 golden set / shadow validation 通过记录。",
            source_id=shadow_eval.eval_id if shadow_eval else None,
            action="运行并通过 replay/shadow eval，再生成发布材料包。",
        ),
        _material(
            "RELEASE_GRAY_APPROVAL",
            "RELEASED",
            "CANDIDATE -> GRAY review log",
            "REVIEW_LOG",
            _has_gray_approval(review_logs),
            "已存在 CANDIDATE_TREE -> GRAY_TREE 专家审核日志。",
            "缺少 CANDIDATE_TREE -> GRAY_TREE 专家审核日志。",
            action="先完成人工审核进入 GRAY_TREE。",
        ),
        _formal_signoff_material(release_artifact),
    ]
    return _package(
        proposal,
        TreeProposalStatus.RELEASED_TREE,
        "RELEASED",
        materials,
        metrics=metrics,
    )


def _package(
    proposal: TreeProposal,
    target_status: TreeProposalStatus,
    stage: Literal["GRAY", "RELEASED"],
    materials: list[TreeAdmissionMaterial],
    *,
    metrics: dict[str, Any],
) -> TreeAdmissionPackage:
    blockers = [
        item.detail
        for item in materials
        if item.status in {"BLOCKED", "MISSING"}
    ]
    warnings = [item.detail for item in materials if item.status == "WARNING"]
    satisfied = [item.detail for item in materials if item.status == "SATISFIED"]
    recommended_actions = [item.recommended_action for item in materials if item.recommended_action]
    return TreeAdmissionPackage(
        proposal_id=proposal.proposal_id,
        current_status=proposal.status,
        target_status=target_status,
        stage=stage,
        ready_for_review=not blockers,
        materials=materials,
        blockers=list(dict.fromkeys(blockers)),
        warnings=list(dict.fromkeys(warnings)),
        satisfied=list(dict.fromkeys(satisfied)),
        recommended_actions=list(dict.fromkeys(action for action in recommended_actions if action)),
        metrics=metrics,
    )


def _material(
    material_id: str,
    stage: Literal["GRAY", "RELEASED"],
    name: str,
    source_type: str,
    condition: bool,
    ok_detail: str,
    missing_detail: str,
    *,
    source_id: str | None = None,
    source_path: str | None = None,
    action: str | None = None,
    status_override: Literal["SATISFIED", "WARNING", "BLOCKED", "MISSING"] | None = None,
    warning_detail: str | None = None,
) -> TreeAdmissionMaterial:
    if status_override:
        status = status_override
    else:
        status = "SATISFIED" if condition else "MISSING"
    detail = (
        ok_detail
        if condition and status == "SATISFIED"
        else warning_detail
        if status == "WARNING"
        else missing_detail
    )
    return TreeAdmissionMaterial(
        material_id=material_id,
        stage=stage,
        name=name,
        status=status,
        source_type=source_type,
        source_id=source_id,
        source_path=source_path,
        detail=detail or "",
        recommended_action=None if status == "SATISFIED" else action,
    )


def _structure_eval_material(eval_result: TreeProposalEvalResult | None) -> TreeAdmissionMaterial:
    if not eval_result:
        return _material(
            "GRAY_STRUCTURE_EVAL",
            "GRAY",
            "Structure Tree Proposal Eval",
            "EVAL_RESULT",
            False,
            "",
            "缺少结构 Tree Proposal Eval。",
            action="运行 Tree Proposal Eval。",
        )
    if eval_result.unsafe_findings:
        return TreeAdmissionMaterial(
            material_id="GRAY_STRUCTURE_EVAL",
            stage="GRAY",
            name="Structure Tree Proposal Eval",
            status="BLOCKED",
            source_type="EVAL_RESULT",
            source_id=eval_result.eval_id,
            detail="结构 Tree Proposal Eval 仍有 unsafe findings：" + "；".join(eval_result.unsafe_findings),
            recommended_action="先修复 Tree Proposal Eval unsafe findings。",
        )
    evidence_rate = eval_result.metrics.get("evidence_binding_rate")
    status = (
        "WARNING"
        if isinstance(evidence_rate, float) and evidence_rate < MIN_GRAY_EVIDENCE_BINDING_RATE
        else "SATISFIED"
    )
    return TreeAdmissionMaterial(
        material_id="GRAY_STRUCTURE_EVAL",
        stage="GRAY",
        name="Structure Tree Proposal Eval",
        status=status,
        source_type="EVAL_RESULT",
        source_id=eval_result.eval_id,
        detail=(
            f"证据绑定率 {evidence_rate:.0%}，低于建议阈值 {MIN_GRAY_EVIDENCE_BINDING_RATE:.0%}。"
            if status == "WARNING" and isinstance(evidence_rate, float)
            else "Tree Proposal Eval 无 unsafe findings。"
        ),
        recommended_action="提高实体、检查项和 transition 的 source_refs/chunk_ids 覆盖。"
        if status == "WARNING"
        else None,
    )


def _shadow_eval_material(eval_result: TreeProposalEvalResult | None) -> TreeAdmissionMaterial:
    if not eval_result:
        return _material(
            "GRAY_SHADOW_EVAL",
            "GRAY",
            "Replay / shadow eval",
            "EVAL_RESULT",
            False,
            "",
            "缺少 replay-based Tree Proposal Eval / shadow diagnosis 对比结果。",
            action="运行 replay-based Tree Proposal Eval / shadow diagnosis simulation。",
        )
    if eval_result.unsafe_findings or eval_result.metrics.get("shadow_ready") is not True:
        findings = "；".join(eval_result.unsafe_findings) or "shadow_ready != true"
        return TreeAdmissionMaterial(
            material_id="GRAY_SHADOW_EVAL",
            stage="GRAY",
            name="Replay / shadow eval",
            status="BLOCKED",
            source_type="EVAL_RESULT",
            source_id=eval_result.eval_id,
            detail="shadow diagnosis 存在阻塞项：" + findings,
            recommended_action="先修复 shadow diagnosis unsafe findings，再提交 GRAY_TREE 审核。",
        )
    return TreeAdmissionMaterial(
        material_id="GRAY_SHADOW_EVAL",
        stage="GRAY",
        name="Replay / shadow eval",
        status="SATISFIED",
        source_type="EVAL_RESULT",
        source_id=eval_result.eval_id,
        detail="replay-based Tree Proposal Eval / shadow diagnosis 无 unsafe findings。",
    )


def _extraction_eval_material(eval_result: TreeProposalEvalResult | None) -> TreeAdmissionMaterial:
    if not eval_result:
        return _material(
            "RELEASE_EXTRACTION_EVAL",
            "RELEASED",
            "Tree Generation Extraction Eval",
            "EVAL_RESULT",
            False,
            "",
            "缺少 Tree Generation Extraction Eval。",
            action="运行抽取质量评测 tree_generation_extraction_v1。",
        )
    blocking_findings = {
        "ARTIFACT_MISSING",
        "ONTOLOGY_STRUCTURE_BLOCKED",
        "PATH_COHERENCE_BLOCKED",
        "HALLUCINATION_HIGH",
    }
    findings = [item for item in eval_result.unsafe_findings if item in blocking_findings]
    metrics = eval_result.metrics
    if findings or metrics.get("candidate_ready") is False:
        return TreeAdmissionMaterial(
            material_id="RELEASE_EXTRACTION_EVAL",
            stage="RELEASED",
            name="Tree Generation Extraction Eval",
            status="BLOCKED",
            source_type="EVAL_RESULT",
            source_id=eval_result.eval_id,
            detail="抽取质量评测存在发布阻塞项：" + "；".join(findings or ["candidate_ready=false"]),
            recommended_action="修复抽取结构、grounding、幻觉或链路逻辑问题后重跑评测。",
        )
    warnings: list[str] = []
    if "GROUNDING_LOW" in eval_result.unsafe_findings:
        warnings.append("grounding precision 偏低")
    if metrics.get("source_fact_recall_status") == "not_available":
        warnings.append("source fact recall 暂不可用")
    if warnings:
        return TreeAdmissionMaterial(
            material_id="RELEASE_EXTRACTION_EVAL",
            stage="RELEASED",
            name="Tree Generation Extraction Eval",
            status="WARNING",
            source_type="EVAL_RESULT",
            source_id=eval_result.eval_id,
            detail="抽取质量评测通过发布阻塞门槛，但仍有提示：" + "；".join(warnings),
            recommended_action="发布前由专家确认 warning 风险是否可接受，并优先补充 source_refs/source chunks。",
        )
    return TreeAdmissionMaterial(
        material_id="RELEASE_EXTRACTION_EVAL",
        stage="RELEASED",
        name="Tree Generation Extraction Eval",
        status="SATISFIED",
        source_type="EVAL_RESULT",
        source_id=eval_result.eval_id,
        detail="tree_generation_extraction_v1 无发布阻塞项。",
    )


def _release_manifest_material(
    proposal: TreeProposal,
    release_artifact: TreeReleaseArtifact | None,
) -> TreeAdmissionMaterial:
    return _material(
        "RELEASE_MANIFEST",
        "RELEASED",
        "Release manifest",
        "RELEASE_ARTIFACT",
        bool(release_artifact and release_artifact.manifest),
        "已生成 release manifest。",
        "缺少 release manifest。",
        source_id=release_artifact.manifest.manifest_id if release_artifact else None,
        source_path=f"artifacts/{proposal.proposal_id}/release/manifest.json" if release_artifact else None,
        action="生成发布前审核材料包。",
    )


def _rollback_material(
    proposal: TreeProposal,
    release_artifact: TreeReleaseArtifact | None,
) -> TreeAdmissionMaterial:
    return _material(
        "RELEASE_ROLLBACK",
        "RELEASED",
        "Rollback metadata",
        "RELEASE_ARTIFACT",
        bool(release_artifact and release_artifact.rollback),
        "已生成 rollback metadata。",
        "缺少 rollback metadata。",
        source_id=release_artifact.rollback.rollback_id if release_artifact else None,
        source_path=f"artifacts/{proposal.proposal_id}/release/rollback_metadata.json"
        if release_artifact
        else None,
        action="生成发布前审核材料包。",
    )


def _ttl_diff_material(
    proposal: TreeProposal,
    release_artifact: TreeReleaseArtifact | None,
) -> TreeAdmissionMaterial:
    return _material(
        "RELEASE_TTL_DIFF",
        "RELEASED",
        "TTL diff / preview",
        "RELEASE_ARTIFACT",
        bool(
            release_artifact
            and release_artifact.ttl_diff_md.strip()
            and release_artifact.generated_ttl_preview.strip()
        ),
        "已生成 TTL diff 和 TTL preview。",
        "缺少正式 TTL diff / release artifact。",
        source_path=f"artifacts/{proposal.proposal_id}/release/ttl_diff.md" if release_artifact else None,
        action="生成发布前审核材料包。",
    )


def _release_internal_blocker_material(
    release_artifact: TreeReleaseArtifact | None,
) -> TreeAdmissionMaterial:
    if not release_artifact:
        return TreeAdmissionMaterial(
            material_id="RELEASE_INTERNAL_BLOCKERS",
            stage="RELEASED",
            name="Release artifact blockers",
            status="MISSING",
            source_type="RELEASE_ARTIFACT",
            detail="缺少 release artifact，无法检查内部阻塞项。",
            recommended_action="生成发布前审核材料包。",
        )
    if release_artifact.blockers:
        return TreeAdmissionMaterial(
            material_id="RELEASE_INTERNAL_BLOCKERS",
            stage="RELEASED",
            name="Release artifact blockers",
            status="BLOCKED",
            source_type="RELEASE_ARTIFACT",
            source_id=release_artifact.release_artifact_id,
            detail="发布材料阻塞：" + "；".join(release_artifact.blockers),
            recommended_action="修复 release artifact blockers 后重新生成发布材料包。",
        )
    if release_artifact.warnings:
        return TreeAdmissionMaterial(
            material_id="RELEASE_INTERNAL_BLOCKERS",
            stage="RELEASED",
            name="Release artifact blockers",
            status="WARNING",
            source_type="RELEASE_ARTIFACT",
            source_id=release_artifact.release_artifact_id,
            detail="发布材料警告：" + "；".join(release_artifact.warnings),
            recommended_action="发布前由专家确认 warning 风险是否可接受。",
        )
    return TreeAdmissionMaterial(
        material_id="RELEASE_INTERNAL_BLOCKERS",
        stage="RELEASED",
        name="Release artifact blockers",
        status="SATISFIED",
        source_type="RELEASE_ARTIFACT",
        source_id=release_artifact.release_artifact_id,
        detail="release artifact 无内部阻塞项。",
    )


def _formal_signoff_material(release_artifact: TreeReleaseArtifact | None) -> TreeAdmissionMaterial:
    reviewer = release_artifact.manifest.formal_signoff_reviewer if release_artifact else None
    return _material(
        "RELEASE_FORMAL_SIGNOFF",
        "RELEASED",
        "Formal release signoff",
        "RELEASE_ARTIFACT",
        bool(reviewer),
        f"已记录专家正式发布签核：{reviewer}。",
        "缺少专家正式发布签核。",
        source_id=reviewer,
        action="由发布责任专家补充 formal signoff reviewer 和 rationale。",
    )


def _latest_eval(
    eval_results: list[TreeProposalEvalResult],
    eval_suite: str,
) -> TreeProposalEvalResult | None:
    matching = [item for item in eval_results if item.eval_suite == eval_suite]
    if not matching:
        return None
    return sorted(matching, key=lambda item: (item.created_at, item.eval_id))[-1]


def _has_candidate_approval(review_logs: list[TreeProposalReviewLog]) -> bool:
    return any(
        log.decision == "APPROVE"
        and log.from_status == TreeProposalStatus.DRAFT_TREE
        and log.to_status == TreeProposalStatus.CANDIDATE_TREE
        for log in review_logs
    )


def _has_gray_approval(review_logs: list[TreeProposalReviewLog]) -> bool:
    return any(
        log.decision == "APPROVE"
        and log.from_status == TreeProposalStatus.CANDIDATE_TREE
        and log.to_status == TreeProposalStatus.GRAY_TREE
        for log in review_logs
    )
