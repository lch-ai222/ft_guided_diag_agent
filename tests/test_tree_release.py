from ft_diag_agent.models import TreeProposalReviewLog, TreeProposalStatus
from ft_diag_agent.tree_generation_eval import evaluate_tree_generation_extraction
from ft_diag_agent.tree_proposal_eval import evaluate_tree_proposal, evaluate_tree_proposal_replay_shadow
from ft_diag_agent.tree_release import build_tree_release_artifact
from tests.test_tree_proposal_eval import _supporting_replay, _valid_artifact
from tests.test_tree_proposal_precheck import _proposal


def _gray_proposal():
    proposal = _proposal()
    proposal.status = TreeProposalStatus.GRAY_TREE
    proposal.candidate_failure_domain = "车身"
    return proposal


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


def _gray_review_log(proposal):
    return TreeProposalReviewLog(
        proposal_id=proposal.proposal_id,
        from_status=TreeProposalStatus.CANDIDATE_TREE,
        to_status=TreeProposalStatus.GRAY_TREE,
        decision="APPROVE",
        reviewer="gray_expert",
        rationale="shadow eval 通过，允许进入灰度验证。",
    )


def test_release_artifact_blocks_without_formal_signoff() -> None:
    proposal = _gray_proposal()
    artifact = _valid_artifact()

    release_artifact = build_tree_release_artifact(
        proposal,
        artifact,
        eval_results=_passed_evals(proposal, artifact),
        review_logs=[_gray_review_log(proposal)],
        generated_by="release_owner",
        release_version="v20260605-test",
    )

    assert release_artifact.release_materials_ready is False
    assert "缺少专家正式发布签核。" in release_artifact.blockers
    assert release_artifact.ttl_diff_md
    assert release_artifact.generated_ttl_preview


def test_release_artifact_blocks_without_extraction_eval() -> None:
    proposal = _gray_proposal()
    artifact = _valid_artifact()
    evals = [item for item in _passed_evals(proposal, artifact) if item.eval_suite != "tree_generation_extraction_v1"]

    release_artifact = build_tree_release_artifact(
        proposal,
        artifact,
        eval_results=evals,
        review_logs=[_gray_review_log(proposal)],
        generated_by="release_owner",
        formal_signoff_reviewer="release_expert",
        formal_signoff_rationale="结构、shadow eval 和回滚材料已复核。",
        release_version="v20260605-test",
    )

    assert release_artifact.release_materials_ready is False
    assert "缺少 Tree Generation Extraction Eval。" in release_artifact.blockers


def test_release_artifact_ready_with_eval_gray_review_and_formal_signoff() -> None:
    proposal = _gray_proposal()
    artifact = _valid_artifact()

    release_artifact = build_tree_release_artifact(
        proposal,
        artifact,
        eval_results=_passed_evals(proposal, artifact),
        review_logs=[_gray_review_log(proposal)],
        generated_by="release_owner",
        formal_signoff_reviewer="release_expert",
        formal_signoff_rationale="结构、shadow eval 和回滚材料已复核。",
        release_version="v20260605-test",
    )

    assert release_artifact.release_materials_ready is True
    assert release_artifact.blockers == []
    assert release_artifact.manifest.formal_signoff_reviewer == "release_expert"
    assert "tree_generation_extraction_v1" in release_artifact.source_eval_suites
    assert "FaultTree" in release_artifact.generated_ttl_preview
