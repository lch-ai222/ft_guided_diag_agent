from ft_diag_agent.models import (
    FieldStatus,
    OntologyEntityDraft,
    OntologyEntityType,
    ReplayRecord,
    SymptomTransitionDraft,
    TreeGenerationArtifact,
    TreeProposal,
)
from ft_diag_agent.tree_generation_eval import (
    TREE_GENERATION_EXTRACTION_EVAL_SUITE,
    evaluate_tree_generation_extraction,
    run_tree_generation_extraction_eval,
)
from ft_diag_agent.tree_proposal_eval import (
    TREE_PROPOSAL_REPLAY_SHADOW_EVAL_SUITE,
    evaluate_tree_proposal,
    evaluate_tree_proposal_replay_shadow,
    run_tree_proposal_eval,
    run_tree_proposal_replay_shadow_eval,
)
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


def _supporting_replay(case_id: str = "CASE-1") -> ReplayRecord:
    return ReplayRecord(
        state_after={
            "case_id": case_id,
            "work_order": {
                "order_id": case_id,
                "failure_phenomenon": "右后门颠簸路异响",
                "executed_checks": ["锁扣位置测量超差，锁扣位置偏差"],
            },
            "executed_tests": [
                {
                    "test_id": "T_LOCK",
                    "result": "锁扣位置测量超差，确认锁扣位置偏差",
                    "passed": True,
                }
            ],
            "evidence_chain": [
                {
                    "evidence_id": "EV-1",
                    "source_type": "HITL",
                    "source_id": "T_LOCK",
                    "claim": "锁扣位置测量超差，支持锁扣位置偏差",
                }
            ],
            "final_report": {"root_cause": "锁扣位置偏差", "gate_status": "GRAY"},
        },
        planner_output=[
            {
                "test_id": "T_LOCK",
                "reason": "通过锁扣位置测量确认锁扣位置偏差",
            }
        ],
        gate_result={"status": "GRAY"},
    )


def test_replay_shadow_eval_marks_supporting_replay_ready() -> None:
    proposal = _proposal()
    proposal.source_case_ids = ["CASE-1"]
    proposal.evidence_ids = ["EV-1"]

    result = evaluate_tree_proposal_replay_shadow(
        proposal,
        _valid_artifact(),
        [_supporting_replay()],
    )

    assert result.eval_suite == TREE_PROPOSAL_REPLAY_SHADOW_EVAL_SUITE
    assert result.metrics["shadow_ready"] is True
    assert result.metrics["shadow_relevant_case_count"] == 1
    assert result.metrics["shadow_support_rate"] == 1.0
    assert result.unsafe_findings == []


def test_replay_shadow_eval_blocks_when_replay_is_missing() -> None:
    result = evaluate_tree_proposal_replay_shadow(_proposal(), _valid_artifact(), [])

    assert result.metrics["shadow_ready"] is False
    assert "REPLAY_RECORDS_MISSING" in result.unsafe_findings
    assert "SHADOW_RELEVANT_CASES_MISSING" in result.unsafe_findings


def test_run_replay_shadow_eval_persists_and_can_filter_by_suite(tmp_path) -> None:
    store = TreeProposalStore(tmp_path / "tree_proposals")
    proposal = _proposal()
    proposal.source_case_ids = ["CASE-1"]
    proposal.evidence_ids = ["EV-1"]
    store.save_proposal(proposal)
    store.save_artifact_snapshot(proposal, artifact=_valid_artifact())

    result = run_tree_proposal_replay_shadow_eval(
        store,
        proposal.proposal_id,
        records=[_supporting_replay()],
    )

    assert result
    assert result.metrics["shadow_ready"] is True
    assert store.latest_eval_result(proposal.proposal_id, TREE_PROPOSAL_REPLAY_SHADOW_EVAL_SUITE)
    assert len(store.load_eval_results(proposal.proposal_id, eval_suite=TREE_PROPOSAL_REPLAY_SHADOW_EVAL_SUITE)) == 1


def test_tree_generation_extraction_eval_marks_grounded_artifact_ready() -> None:
    artifact = _valid_artifact()
    for entity in [*artifact.symptoms, *artifact.tests]:
        entity.name_status = FieldStatus.EXTRACTED_EXPLICIT
        entity.description_status = FieldStatus.EXTRACTED_EXPLICIT
    artifact.transitions[0].condition_status = FieldStatus.EXTRACTED_EXPLICIT
    artifact.transitions[0].description_status = FieldStatus.EXTRACTED_EXPLICIT
    result = evaluate_tree_generation_extraction(
        _proposal(),
        artifact,
        source_texts=[
            "右后门颠簸路异响，通过锁扣位置测量发现锁扣位置偏差，锁扣 Z/Y 向偏差导致预紧不足。"
        ],
    )

    assert result.eval_suite == TREE_GENERATION_EXTRACTION_EVAL_SUITE
    assert result.metrics["ontology_structure_score"] == 1.0
    assert result.metrics["grounding_precision"] == 1.0
    assert result.metrics["hallucination_rate"] == 0.0
    assert result.metrics["path_coherence_score"] == 1.0
    assert "ONTOLOGY_STRUCTURE_BLOCKED" not in result.unsafe_findings


def test_tree_generation_extraction_eval_flags_low_grounding_and_hallucination() -> None:
    artifact = _valid_artifact()
    for entity in [*artifact.symptoms, *artifact.tests]:
        entity.source_refs = []
        entity.chunk_ids = []
        entity.name_status = FieldStatus.EXTRACTED_EXPLICIT
        entity.description_status = FieldStatus.EXTRACTED_EXPLICIT
    artifact.transitions[0].source_refs = []
    artifact.transitions[0].chunk_ids = []
    artifact.transitions[0].condition_status = FieldStatus.EXTRACTED_EXPLICIT
    artifact.transitions[0].description_status = FieldStatus.EXTRACTED_EXPLICIT

    result = evaluate_tree_generation_extraction(
        _proposal(),
        artifact,
        source_texts=["右后门颠簸路出现客户抱怨，现场暂无锁扣相关原文证据。"],
    )

    assert "GROUNDING_LOW" in result.unsafe_findings
    assert "HALLUCINATION_HIGH" in result.unsafe_findings
    assert result.metrics["hallucination_rate"] > 0


def test_tree_generation_extraction_eval_excludes_confirmed_fields_from_hallucination() -> None:
    artifact = _valid_artifact()
    for entity in [*artifact.symptoms, *artifact.tests]:
        entity.source_refs = []
        entity.chunk_ids = []
        entity.name_status = FieldStatus.CONFIRMED
        entity.description_status = FieldStatus.CONFIRMED
    artifact.transitions[0].source_refs = []
    artifact.transitions[0].chunk_ids = []
    artifact.transitions[0].condition_status = FieldStatus.CONFIRMED
    artifact.transitions[0].description_status = FieldStatus.CONFIRMED

    result = evaluate_tree_generation_extraction(_proposal(), artifact, source_texts=["右后门颠簸路异响。"])

    assert result.metrics["hallucination_rate"] is None
    assert "HALLUCINATION_HIGH" not in result.unsafe_findings


def test_tree_generation_extraction_eval_detects_source_recall_gap_and_duplicates() -> None:
    artifact = _valid_artifact()
    artifact.symptoms.append(artifact.symptoms[-1].model_copy(update={"entity_id": "S_ROOT_DUP"}))
    result = evaluate_tree_generation_extraction(
        _proposal(),
        artifact,
        source_texts=[
            "右后门颠簸路异响。锁扣位置偏差。内饰卡扣松动。排水孔堵盖松动。锁扣位置测量。"
        ],
    )

    assert result.metrics["source_fact_recall"] < 1.0
    assert result.metrics["duplicate_semantic_rate"] > 0


def test_run_tree_generation_extraction_eval_persists_result(tmp_path) -> None:
    store = TreeProposalStore(tmp_path / "tree_proposals")
    proposal = _proposal()
    store.save_proposal(proposal)
    store.save_artifact_snapshot(proposal, artifact=_valid_artifact())

    result = run_tree_generation_extraction_eval(store, proposal.proposal_id)

    assert result
    assert store.latest_eval_result(proposal.proposal_id, TREE_GENERATION_EXTRACTION_EVAL_SUITE)
