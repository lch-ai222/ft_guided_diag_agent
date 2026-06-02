from ft_diag_agent.models import (
    FieldStatus,
    OntologyEntityDraft,
    OntologyEntityType,
    SymptomTransitionDraft,
    TreeGenerationArtifact,
    TreeProposal,
)
from ft_diag_agent.tree_proposal_eval import evaluate_tree_proposal, run_tree_proposal_eval
from ft_diag_agent.tree_proposals import TreeProposalStore


def _proposal() -> TreeProposal:
    return TreeProposal(
        proposal_id="TP-EVAL",
        phenomenon_bucket="右后门异响",
        candidate_start_symptom="右后门颠簸路异响",
        root_cause_families=["锁扣位置偏差"],
        candidate_tests=["锁扣位置测量"],
    )


def _valid_artifact() -> TreeGenerationArtifact:
    return TreeGenerationArtifact(
        job_id="TGJ-EVAL",
        symptoms=[
            OntologyEntityDraft(
                entity_id="S_START",
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name="右后门颠簸路异响",
                name_status=FieldStatus.CONFIRMED,
                level="start",
                description="颠簸路出现异响",
                description_status=FieldStatus.CONFIRMED,
                source_refs=["doc.md:0"],
            ),
            OntologyEntityDraft(
                entity_id="S_ROOT",
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name="锁扣位置偏差",
                name_status=FieldStatus.CONFIRMED,
                level="root",
                description="锁扣 Z/Y 向偏差导致预紧不足",
                description_status=FieldStatus.CONFIRMED,
                source_refs=["doc.md:0"],
            ),
        ],
        tests=[
            OntologyEntityDraft(
                entity_id="T_LOCK",
                entity_type=OntologyEntityType.ONTOLOGY_TEST,
                name="锁扣位置测量",
                name_status=FieldStatus.CONFIRMED,
                description="测量锁扣 Z/Y 向位置",
                description_status=FieldStatus.CONFIRMED,
                source_refs=["doc.md:0"],
            )
        ],
        transitions=[
            SymptomTransitionDraft(
                transition_id="TR_LOCK",
                source_id="S_START",
                target_id="S_ROOT",
                test_ids=["T_LOCK"],
                condition="锁扣位置测量超差",
                condition_status=FieldStatus.CONFIRMED,
                description="通过锁扣位置测量定位根因",
                description_status=FieldStatus.CONFIRMED,
                source_refs=["doc.md:0"],
            )
        ],
    )


def test_tree_proposal_eval_marks_confirmed_valid_artifact_ready() -> None:
    result = evaluate_tree_proposal(_proposal(), _valid_artifact())

    assert result.metrics["schema_valid"] is True
    assert result.metrics["candidate_ready"] is True
    assert result.metrics["roots_with_tests_rate"] == 1.0
    assert result.metrics["hitl_pending_count"] == 0
    assert result.unsafe_findings == []


def test_tree_proposal_eval_blocks_pending_hitl_and_missing_tests() -> None:
    artifact = _valid_artifact()
    artifact.transitions[0].test_ids = []
    artifact.symptoms[1].description_status = FieldStatus.MISSING

    result = evaluate_tree_proposal(_proposal(), artifact)

    assert result.metrics["candidate_ready"] is False
    assert "TRANSITION_TEST_MISSING" in result.unsafe_findings
    assert "HITL_PENDING" in result.unsafe_findings


def test_run_tree_proposal_eval_persists_result_and_handles_missing_artifact(tmp_path) -> None:
    store = TreeProposalStore(tmp_path / "tree_proposals")
    proposal = _proposal()
    store.save_proposal(proposal)

    missing = run_tree_proposal_eval(store, proposal.proposal_id)

    assert missing
    assert "ARTIFACT_MISSING" in missing.unsafe_findings
    assert store.load_eval_results(proposal.proposal_id)[0].metrics["artifact_present"] is False

    store.save_artifact_snapshot(proposal, artifact=_valid_artifact())
    valid = run_tree_proposal_eval(store, proposal.proposal_id)

    assert valid
    assert valid.metrics["schema_valid"] is True
    assert len(store.load_eval_results(proposal.proposal_id)) == 2
