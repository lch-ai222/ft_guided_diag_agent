from ft_diag_agent.models import TreeProposal, TreeProposalCaseLink
from ft_diag_agent.tree_generation_eval import evaluate_tree_generation_extraction
from ft_diag_agent.tree_proposal_eval import evaluate_tree_proposal
from ft_diag_agent.tree_proposal_precheck import assess_tree_proposal_promotion
from ft_diag_agent.tree_proposal_view import (
    artifact_node_rows,
    artifact_transition_rows,
    proposal_skeleton_mermaid,
    proposal_skeleton_node_rows,
    proposal_skeleton_transition_rows,
    tree_proposal_lifecycle_steps,
)
from tests.test_tree_proposal_eval import _valid_artifact


def _proposal() -> TreeProposal:
    return TreeProposal(
        proposal_id="TP-VIEW",
        phenomenon_bucket="右后门异响",
        candidate_start_symptom="右后门颠簸路异响",
        root_cause_families=["锁扣位置偏差"],
        candidate_tests=["锁扣位置测量"],
        source_case_ids=["CASE-1", "CASE-2", "CASE-3"],
        evidence_ids=["EV-1"],
        source_refs=["doc.md:0"],
    )


def test_lifecycle_steps_show_completed_artifact_flow_until_review() -> None:
    proposal = _proposal()
    artifact = _valid_artifact()
    eval_result = evaluate_tree_proposal(proposal, artifact)
    extraction_eval = evaluate_tree_generation_extraction(
        proposal,
        artifact,
        source_texts=[
            "右后门颠簸路异响。通过锁扣位置测量发现锁扣位置偏差。锁扣 Z/Y 向偏差导致预紧不足。"
        ],
    )
    case_links = [
        TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-1"),
        TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-2"),
        TreeProposalCaseLink(proposal_id=proposal.proposal_id, case_id="CASE-3"),
    ]
    precheck = assess_tree_proposal_promotion(
        proposal,
        artifact=artifact,
        case_links=case_links,
        eval_results=[eval_result, extraction_eval],
    )

    steps = tree_proposal_lifecycle_steps(
        proposal,
        artifact=artifact,
        case_links=case_links,
        eval_results=[eval_result, extraction_eval],
        precheck=precheck,
    )
    by_id = {step.step_id: step for step in steps}

    assert by_id["source"].status == "DONE"
    assert by_id["draft"].status == "DONE"
    assert by_id["structure"].status == "DONE"
    assert by_id["hitl"].status == "DONE"
    assert by_id["eval"].status == "DONE"
    assert by_id["candidate_review"].status == "CURRENT"
    assert by_id["gray"].status == "PENDING"
    assert by_id["release"].status == "PENDING"


def test_lifecycle_steps_mark_no_artifact_proposal_as_blocked_skeleton() -> None:
    proposal = _proposal()
    proposal.source_case_ids = []
    precheck = assess_tree_proposal_promotion(proposal)

    steps = tree_proposal_lifecycle_steps(proposal, precheck=precheck)
    by_id = {step.step_id: step for step in steps}

    assert by_id["source"].status == "WARNING"
    assert by_id["draft"].status == "BLOCKED"
    assert by_id["structure"].status == "BLOCKED"
    assert by_id["hitl"].status == "PENDING"
    assert by_id["eval"].status == "CURRENT"
    assert by_id["candidate_review"].status == "BLOCKED"


def test_proposal_skeleton_mermaid_and_rows_are_human_readable() -> None:
    proposal = _proposal()

    mermaid = proposal_skeleton_mermaid(proposal)
    node_rows = proposal_skeleton_node_rows(proposal)
    transition_rows = proposal_skeleton_transition_rows(proposal)

    assert "L1 start" in mermaid
    assert "L3 root" in mermaid
    assert "锁扣位置测量" in mermaid
    assert node_rows[0]["level"] == "L1 start"
    assert node_rows[1]["level"] == "L3 root"
    assert transition_rows[0]["test"] == "锁扣位置测量"


def test_artifact_rows_expose_levels_status_and_transition_tests() -> None:
    artifact = _valid_artifact()

    node_rows = artifact_node_rows(artifact)
    transition_rows = artifact_transition_rows(artifact)

    assert {row["level"] for row in node_rows} == {"start", "root"}
    assert all(row["name_status"] == "CONFIRMED" for row in node_rows)
    assert transition_rows[0]["source"] == "右后门颠簸路异响"
    assert transition_rows[0]["target"] == "锁扣位置偏差"
    assert transition_rows[0]["tests"] == ["锁扣位置测量"]
