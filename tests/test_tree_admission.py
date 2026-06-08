from ft_diag_agent.models import TreeProposalReviewLog, TreeProposalStatus
from ft_diag_agent.tree_admission import build_gray_admission_package, build_release_admission_package
from ft_diag_agent.tree_generation_eval import evaluate_tree_generation_extraction
from ft_diag_agent.tree_proposal_eval import evaluate_tree_proposal, evaluate_tree_proposal_replay_shadow
from ft_diag_agent.tree_release import build_tree_release_artifact
from tests.test_tree_proposal_eval import _supporting_replay, _valid_artifact
from tests.test_tree_proposal_precheck import _proposal


def _candidate_review(proposal):
    return TreeProposalReviewLog(
        proposal_id=proposal.proposal_id,
        from_status=TreeProposalStatus.DRAFT_TREE,
        to_status=TreeProposalStatus.CANDIDATE_TREE,
        decision="APPROVE",
        rationale="结构审核通过。",
    )


def _gray_review(proposal):
    return TreeProposalReviewLog(
        proposal_id=proposal.proposal_id,
        from_status=TreeProposalStatus.CANDIDATE_TREE,
        to_status=TreeProposalStatus.GRAY_TREE,
        decision="APPROVE",
        rationale="shadow eval 通过。",
    )


def _passed_evals(proposal, artifact):
    return [
        evaluate_tree_generation_extraction(
            proposal,
            artifact,
            source_texts=[
                "右后门颠簸路异响。通过锁扣位置测量发现锁扣位置偏差。锁扣 Z/Y 向偏差导致预紧不足。"
            ],
        ),
        evaluate_tree_proposal(proposal, artifact),
        evaluate_tree_proposal_replay_shadow(
            proposal,
            artifact,
            [_supporting_replay("CASE-1"), _supporting_replay("CASE-2"), _supporting_replay("CASE-3")],
        ),
    ]


def test_gray_admission_blocks_without_shadow_eval() -> None:
    proposal = _proposal()
    proposal.status = TreeProposalStatus.CANDIDATE_TREE
    artifact = _valid_artifact()
    package = build_gray_admission_package(
        proposal,
        artifact=artifact,
        eval_results=[evaluate_tree_proposal(proposal, artifact)],
        review_logs=[_candidate_review(proposal)],
    )

    assert package.ready_for_review is False
    assert any(item.material_id == "GRAY_SHADOW_EVAL" and item.status == "MISSING" for item in package.materials)
    assert "缺少 replay-based Tree Proposal Eval / shadow diagnosis 对比结果。" in package.blockers


def test_gray_admission_ready_when_materials_are_complete() -> None:
    proposal = _proposal()
    proposal.status = TreeProposalStatus.CANDIDATE_TREE
    proposal.candidate_failure_domain = "车身"
    artifact = _valid_artifact()
    package = build_gray_admission_package(
        proposal,
        artifact=artifact,
        eval_results=_passed_evals(proposal, artifact),
        review_logs=[_candidate_review(proposal)],
    )

    assert package.ready_for_review is True
    assert package.blockers == []
    assert any(item.material_id == "GRAY_SHADOW_EVAL" and item.status == "SATISFIED" for item in package.materials)


def test_release_admission_blocks_without_release_artifact() -> None:
    proposal = _proposal()
    proposal.status = TreeProposalStatus.GRAY_TREE
    artifact = _valid_artifact()
    package = build_release_admission_package(
        proposal,
        eval_results=_passed_evals(proposal, artifact),
        review_logs=[_gray_review(proposal)],
    )

    assert package.ready_for_review is False
    assert "缺少 release artifact。" in package.blockers
    assert any(item.material_id == "RELEASE_ARTIFACT" and item.status == "MISSING" for item in package.materials)


def test_release_admission_blocks_without_formal_signoff() -> None:
    proposal = _proposal()
    proposal.status = TreeProposalStatus.GRAY_TREE
    artifact = _valid_artifact()
    evals = _passed_evals(proposal, artifact)
    reviews = [_gray_review(proposal)]
    release_artifact = build_tree_release_artifact(
        proposal,
        artifact,
        eval_results=evals,
        review_logs=reviews,
        generated_by="release_owner",
    )

    package = build_release_admission_package(
        proposal,
        eval_results=evals,
        review_logs=reviews,
        release_artifact=release_artifact,
    )

    assert package.ready_for_review is False
    assert any(item.material_id == "RELEASE_FORMAL_SIGNOFF" and item.status == "MISSING" for item in package.materials)
    assert "缺少专家正式发布签核。" in package.blockers


def test_release_admission_ready_when_materials_are_complete() -> None:
    proposal = _proposal()
    proposal.status = TreeProposalStatus.GRAY_TREE
    proposal.candidate_failure_domain = "车身"
    artifact = _valid_artifact()
    evals = _passed_evals(proposal, artifact)
    reviews = [_gray_review(proposal)]
    release_artifact = build_tree_release_artifact(
        proposal,
        artifact,
        eval_results=evals,
        review_logs=reviews,
        generated_by="release_owner",
        formal_signoff_reviewer="release_expert",
        formal_signoff_rationale="材料齐全。",
    )

    package = build_release_admission_package(
        proposal,
        eval_results=evals,
        review_logs=reviews,
        release_artifact=release_artifact,
    )

    assert package.ready_for_review is True
    assert package.blockers == []
    assert any(
        item.material_id == "RELEASE_EXTRACTION_EVAL" and item.status == "SATISFIED"
        for item in package.materials
    )
    assert any(item.material_id == "RELEASE_TTL_DIFF" and item.status == "SATISFIED" for item in package.materials)
