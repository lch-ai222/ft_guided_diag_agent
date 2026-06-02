from ft_diag_agent.models import (
    TreeGenerationArtifact,
    TreeProposal,
    TreeProposalCaseLink,
    TreeProposalEvalResult,
    TreeProposalStatus,
)
from ft_diag_agent.tree_proposals import TreeProposalStore


def _proposal(proposal_id: str = "TP-TEST") -> TreeProposal:
    return TreeProposal(
        proposal_id=proposal_id,
        phenomenon_bucket="右后门异响",
        candidate_start_symptom="右后门颠簸路异响",
        root_cause_families=["锁扣位置偏差"],
        candidate_tests=["锁扣位置测量"],
    )


def test_tree_proposal_store_upserts_proposals_and_snapshots_artifact(tmp_path) -> None:
    store = TreeProposalStore(tmp_path / "tree_proposals")
    proposal = _proposal()
    artifact = TreeGenerationArtifact(job_id="TGJ-TEST")

    store.save_proposal(proposal)
    proposal.confidence_summary = "已完成 HITL 确认。"
    store.save_proposal(proposal)
    snapshot_dir = store.save_artifact_snapshot(proposal, artifact=artifact)

    proposals = store.load_proposals()
    assert len(proposals) == 1
    assert proposals[0].confidence_summary == "已完成 HITL 确认。"
    assert (snapshot_dir / "proposal.json").exists()
    assert (snapshot_dir / "artifact.json").exists()


def test_tree_proposal_review_moves_draft_to_candidate_and_logs(tmp_path) -> None:
    store = TreeProposalStore(tmp_path / "tree_proposals")
    proposal = _proposal()
    store.save_proposal(proposal)

    log = store.review_proposal(
        proposal.proposal_id,
        decision="APPROVE",
        reviewer="quality_expert",
        rationale="HITL 字段已确认，结构校验通过。",
    )

    assert log
    assert log.from_status == TreeProposalStatus.DRAFT_TREE
    assert log.to_status == TreeProposalStatus.CANDIDATE_TREE
    updated = store.get_proposal(proposal.proposal_id)
    assert updated
    assert updated.status == TreeProposalStatus.CANDIDATE_TREE
    assert updated.allowed_next_statuses == [TreeProposalStatus.REJECTED]
    assert store.load_review_logs(proposal.proposal_id)[0].reviewer == "quality_expert"


def test_tree_proposal_store_records_case_links_and_eval_results(tmp_path) -> None:
    store = TreeProposalStore(tmp_path / "tree_proposals")
    proposal = _proposal()
    store.save_proposal(proposal)
    store.append_case_link(
        TreeProposalCaseLink(
            proposal_id=proposal.proposal_id,
            case_id="CASE-1",
            matched_root_cause_family="锁扣位置偏差",
        )
    )
    store.append_eval_result(
        TreeProposalEvalResult(
            proposal_id=proposal.proposal_id,
            eval_suite="tree_proposal_v1",
            status_at_eval=TreeProposalStatus.DRAFT_TREE,
            metrics={"schema_valid": True},
        )
    )

    assert store.load_case_links(proposal.proposal_id)[0].case_id == "CASE-1"
    assert store.load_eval_results(proposal.proposal_id)[0].metrics["schema_valid"] is True
