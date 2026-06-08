from ft_diag_agent.models import (
    TreeProposal,
    TreeProposalCaseLink,
    TreeProposalReviewLog,
    TreeProposalStatus,
)
from ft_diag_agent.tree_generation_eval import evaluate_tree_generation_extraction
from ft_diag_agent.tree_proposal_eval import (
    evaluate_tree_proposal,
    evaluate_tree_proposal_replay_shadow,
)
from ft_diag_agent.tree_proposal_precheck import assess_tree_proposal_promotion
from ft_diag_agent.tree_proposals import TreeProposalStore
from ft_diag_agent.tree_release import build_tree_release_artifact
from tests.test_tree_proposal_eval import _supporting_replay, _valid_artifact


def _proposal() -> TreeProposal:
    return TreeProposal(
        proposal_id="TP-PRECHECK",
        phenomenon_bucket="右后门异响",
        candidate_start_symptom="右后门颠簸路异响",
        root_cause_families=["锁扣位置偏差"],
        candidate_tests=["锁扣位置测量"],
        source_case_ids=["CASE-1", "CASE-2", "CASE-3"],
        evidence_ids=["EV-1"],
        source_refs=["doc.md:0"],
    )


def _extraction_eval(proposal: TreeProposal, artifact):
    return evaluate_tree_generation_extraction(
        proposal,
        artifact,
        source_texts=[
            "右后门颠簸路异响。通过锁扣位置测量发现锁扣位置偏差。锁扣 Z/Y 向偏差导致预紧不足。"
        ],
    )


def test_draft_precheck_ready_when_artifact_eval_evidence_and_cases_are_complete() -> None:
    proposal = _proposal()
    artifact = _valid_artifact()
    eval_result = evaluate_tree_proposal(proposal, artifact)
    extraction_eval = _extraction_eval(proposal, artifact)

    precheck = assess_tree_proposal_promotion(
        proposal,
        artifact=artifact,
        case_links=[
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-1"),
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-2"),
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-3"),
        ],
        eval_results=[eval_result, extraction_eval],
    )

    assert precheck.target_status == TreeProposalStatus.CANDIDATE_TREE
    assert precheck.verdict == "READY_FOR_REVIEW"
    assert precheck.blockers == []
    assert "Tree Proposal Eval 无 unsafe findings。" in precheck.satisfied


def test_draft_precheck_blocks_without_artifact_and_eval() -> None:
    proposal = _proposal()

    precheck = assess_tree_proposal_promotion(proposal)

    assert precheck.verdict == "BLOCKED"
    assert "缺少 TreeGenerationArtifact，本体草案尚不能做结构预审。" in precheck.blockers
    assert "缺少 Tree Proposal Eval 结果。" in precheck.blockers


def test_draft_precheck_warns_for_low_support_case_count() -> None:
    proposal = _proposal()
    proposal.source_case_ids = ["CASE-1"]
    artifact = _valid_artifact()
    eval_result = evaluate_tree_proposal(proposal, artifact)
    extraction_eval = _extraction_eval(proposal, artifact)

    precheck = assess_tree_proposal_promotion(
        proposal,
        artifact=artifact,
        case_links=[TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-1")],
        eval_results=[eval_result, extraction_eval],
    )

    assert precheck.verdict == "NEEDS_MORE_EVIDENCE"
    assert precheck.blockers == []
    assert any("支持案例数 1/3" in item for item in precheck.warnings)


def test_draft_precheck_blocks_without_extraction_eval() -> None:
    proposal = _proposal()
    artifact = _valid_artifact()
    eval_result = evaluate_tree_proposal(proposal, artifact)

    precheck = assess_tree_proposal_promotion(
        proposal,
        artifact=artifact,
        case_links=[
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-1"),
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-2"),
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-3"),
        ],
        eval_results=[eval_result],
    )

    assert precheck.verdict == "BLOCKED"
    assert "缺少 Tree Generation Extraction Eval 结果。" in precheck.blockers


def test_candidate_precheck_blocks_gray_without_replay_eval() -> None:
    proposal = _proposal()
    proposal.status = TreeProposalStatus.CANDIDATE_TREE
    artifact = _valid_artifact()
    eval_result = evaluate_tree_proposal(proposal, artifact)

    precheck = assess_tree_proposal_promotion(
        proposal,
        artifact=artifact,
        case_links=[
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-1"),
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-2"),
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-3"),
        ],
        eval_results=[eval_result],
    )

    assert precheck.target_status == TreeProposalStatus.GRAY_TREE
    assert precheck.verdict == "BLOCKED"
    assert "缺少 replay-based Tree Proposal Eval / shadow diagnosis 对比结果。" in precheck.blockers


def test_candidate_precheck_blocks_gray_when_shadow_eval_has_unsafe_findings() -> None:
    proposal = _proposal()
    proposal.status = TreeProposalStatus.CANDIDATE_TREE
    artifact = _valid_artifact()
    structure_eval = evaluate_tree_proposal(proposal, artifact)
    shadow_eval = evaluate_tree_proposal_replay_shadow(proposal, artifact, [])

    precheck = assess_tree_proposal_promotion(
        proposal,
        artifact=artifact,
        case_links=[
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-1"),
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-2"),
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-3"),
        ],
        eval_results=[structure_eval, shadow_eval],
        review_logs=[
            TreeProposalReviewLog(
                proposal_id=proposal.proposal_id,
                from_status=TreeProposalStatus.DRAFT_TREE,
                to_status=TreeProposalStatus.CANDIDATE_TREE,
                decision="APPROVE",
                rationale="结构审核通过。",
            )
        ],
    )

    assert precheck.verdict == "BLOCKED"
    assert any("shadow diagnosis 存在阻塞项" in item for item in precheck.blockers)


def test_candidate_precheck_allows_gray_review_when_shadow_eval_passes() -> None:
    proposal = _proposal()
    proposal.status = TreeProposalStatus.CANDIDATE_TREE
    artifact = _valid_artifact()
    structure_eval = evaluate_tree_proposal(proposal, artifact)
    shadow_eval = evaluate_tree_proposal_replay_shadow(
        proposal,
        artifact,
        [_supporting_replay("CASE-1"), _supporting_replay("CASE-2"), _supporting_replay("CASE-3")],
        case_links=[
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-1"),
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-2"),
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-3"),
        ],
    )

    precheck = assess_tree_proposal_promotion(
        proposal,
        artifact=artifact,
        case_links=[
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-1"),
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-2"),
            TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-3"),
        ],
        eval_results=[structure_eval, shadow_eval],
        review_logs=[
            TreeProposalReviewLog(
                proposal_id=proposal.proposal_id,
                from_status=TreeProposalStatus.DRAFT_TREE,
                to_status=TreeProposalStatus.CANDIDATE_TREE,
                decision="APPROVE",
                rationale="结构审核通过。",
            )
        ],
    )

    assert precheck.target_status == TreeProposalStatus.GRAY_TREE
    assert precheck.blockers == []
    assert "replay-based Tree Proposal Eval / shadow diagnosis 无 unsafe findings。" in precheck.satisfied


def test_gray_precheck_blocks_release_without_release_artifact() -> None:
    proposal = _proposal()
    proposal.status = TreeProposalStatus.GRAY_TREE
    artifact = _valid_artifact()
    structure_eval = evaluate_tree_proposal(proposal, artifact)
    extraction_eval = _extraction_eval(proposal, artifact)
    shadow_eval = evaluate_tree_proposal_replay_shadow(
        proposal,
        artifact,
        [_supporting_replay("CASE-1"), _supporting_replay("CASE-2"), _supporting_replay("CASE-3")],
    )

    precheck = assess_tree_proposal_promotion(
        proposal,
        artifact=artifact,
        eval_results=[structure_eval, extraction_eval, shadow_eval],
        review_logs=[
            TreeProposalReviewLog(
                proposal_id=proposal.proposal_id,
                from_status=TreeProposalStatus.CANDIDATE_TREE,
                to_status=TreeProposalStatus.GRAY_TREE,
                decision="APPROVE",
                rationale="shadow eval 通过。",
            )
        ],
    )

    assert precheck.target_status == TreeProposalStatus.RELEASED_TREE
    assert precheck.verdict == "BLOCKED"
    assert "缺少 release manifest。" in precheck.blockers


def test_gray_precheck_blocks_release_materials_without_formal_signoff() -> None:
    proposal = _proposal()
    proposal.status = TreeProposalStatus.GRAY_TREE
    artifact = _valid_artifact()
    structure_eval = evaluate_tree_proposal(proposal, artifact)
    extraction_eval = _extraction_eval(proposal, artifact)
    shadow_eval = evaluate_tree_proposal_replay_shadow(
        proposal,
        artifact,
        [_supporting_replay("CASE-1"), _supporting_replay("CASE-2"), _supporting_replay("CASE-3")],
    )
    review_logs = [
        TreeProposalReviewLog(
            proposal_id=proposal.proposal_id,
            from_status=TreeProposalStatus.CANDIDATE_TREE,
            to_status=TreeProposalStatus.GRAY_TREE,
            decision="APPROVE",
            rationale="shadow eval 通过。",
        )
    ]
    release_artifact = build_tree_release_artifact(
        proposal,
        artifact,
        eval_results=[structure_eval, extraction_eval, shadow_eval],
        review_logs=review_logs,
        generated_by="release_owner",
    )

    precheck = assess_tree_proposal_promotion(
        proposal,
        artifact=artifact,
        eval_results=[structure_eval, extraction_eval, shadow_eval],
        review_logs=review_logs,
        release_artifact=release_artifact,
    )

    assert precheck.verdict == "BLOCKED"
    assert any("缺少专家正式发布签核" in item for item in precheck.blockers)


def test_gray_precheck_allows_release_review_when_materials_are_complete() -> None:
    proposal = _proposal()
    proposal.status = TreeProposalStatus.GRAY_TREE
    proposal.candidate_failure_domain = "车身"
    artifact = _valid_artifact()
    structure_eval = evaluate_tree_proposal(proposal, artifact)
    extraction_eval = _extraction_eval(proposal, artifact)
    shadow_eval = evaluate_tree_proposal_replay_shadow(
        proposal,
        artifact,
        [_supporting_replay("CASE-1"), _supporting_replay("CASE-2"), _supporting_replay("CASE-3")],
    )
    review_logs = [
        TreeProposalReviewLog(
            proposal_id=proposal.proposal_id,
            from_status=TreeProposalStatus.CANDIDATE_TREE,
            to_status=TreeProposalStatus.GRAY_TREE,
            decision="APPROVE",
            rationale="shadow eval 通过。",
        )
    ]
    release_artifact = build_tree_release_artifact(
        proposal,
        artifact,
        eval_results=[structure_eval, extraction_eval, shadow_eval],
        review_logs=review_logs,
        generated_by="release_owner",
        formal_signoff_reviewer="release_expert",
        formal_signoff_rationale="发布材料已复核。",
    )

    precheck = assess_tree_proposal_promotion(
        proposal,
        artifact=artifact,
        eval_results=[structure_eval, extraction_eval, shadow_eval],
        review_logs=review_logs,
        release_artifact=release_artifact,
    )

    assert precheck.target_status == TreeProposalStatus.RELEASED_TREE
    assert precheck.blockers == []
    assert any("已记录专家正式发布签核" in item for item in precheck.satisfied)


def test_review_log_persists_precheck_snapshot(tmp_path) -> None:
    store = TreeProposalStore(tmp_path / "tree_proposals")
    proposal = _proposal()
    artifact = _valid_artifact()
    eval_result = evaluate_tree_proposal(proposal, artifact)
    extraction_eval = _extraction_eval(proposal, artifact)
    store.save_proposal(proposal)
    precheck = assess_tree_proposal_promotion(
        proposal,
        artifact=artifact,
        eval_results=[eval_result, extraction_eval],
    )

    log = store.review_proposal(
        proposal.proposal_id,
        decision="APPROVE",
        reviewer="expert",
        rationale="预审通过，人工确认进入候选。",
        precheck_result=precheck.model_dump(mode="json"),
    )

    assert log
    assert log.precheck_result["verdict"] == "READY_FOR_REVIEW"
    assert store.load_review_logs(proposal.proposal_id)[0].precheck_result["target_status"] == "CANDIDATE_TREE"
