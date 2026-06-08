from ft_diag_agent.models import (
    FaultTreeGenerationRequest,
    FaultTreeRequestCluster,
    FaultTreeReviewStatus,
    TreeChangeType,
    TreeGenerationArtifact,
    TreeProposal,
    TreeProposalCaseLink,
    TreeProposalEvalResult,
    TreeProposalKind,
    TreeProposalStatus,
)
from ft_diag_agent.tree_proposals import TreeProposalStore
from ft_diag_agent.tree_release import build_tree_release_artifact
from tests.test_tree_proposal_eval import _valid_artifact


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
    assert updated.allowed_next_statuses == [TreeProposalStatus.GRAY_TREE, TreeProposalStatus.REJECTED]
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


def test_tree_proposal_store_saves_release_artifact(tmp_path) -> None:
    store = TreeProposalStore(tmp_path / "tree_proposals")
    proposal = _proposal()
    proposal.status = TreeProposalStatus.GRAY_TREE
    release_artifact = build_tree_release_artifact(
        proposal,
        _valid_artifact(),
        formal_signoff_reviewer="release_expert",
        formal_signoff_rationale="测试签核。",
        release_version="v-test",
    )

    release_dir = store.save_release_artifact(release_artifact)
    loaded = store.load_release_artifact(proposal.proposal_id)

    assert (release_dir / "manifest.json").exists()
    assert (release_dir / "rollback_metadata.json").exists()
    assert (release_dir / "ttl_diff.md").exists()
    assert loaded
    assert loaded.manifest.release_version == "v-test"


def test_tree_proposal_store_upserts_tree_change_proposal(tmp_path) -> None:
    store = TreeProposalStore(tmp_path / "tree_proposals")
    proposal = TreeProposal(
        proposal_id="TP-CHG-1",
        proposal_kind=TreeProposalKind.TREE_CHANGE,
        source_type="COVERED_TREE_DRIFT",
        target_tree_id="FT_002",
        change_types=[TreeChangeType.UPDATE_TEST, TreeChangeType.UPDATE_THRESHOLD],
        change_summary="工艺变更后锁扣位置测量阈值需复核。",
        change_patch={"target_tree_id": "FT_002", "mode": "review_patch_only"},
        drift_signals=["阈值/判定标准变化"],
        phenomenon_bucket="车门无法关闭",
        candidate_start_symptom="车门无法关闭",
        root_cause_families=["锁扣位置偏差"],
        candidate_tests=["锁扣位置测量阈值复核"],
        source_case_ids=["CASE-CHANGE"],
    )

    first = store.upsert_tree_change_proposal(proposal)
    second = store.upsert_tree_change_proposal(proposal)

    proposals = store.load_proposals()
    links = store.load_case_links(first.proposal_id)
    assert first.proposal_id == second.proposal_id
    assert len(proposals) == 1
    assert proposals[0].proposal_kind == TreeProposalKind.TREE_CHANGE
    assert proposals[0].target_tree_id == "FT_002"
    assert proposals[0].change_types == [TreeChangeType.UPDATE_TEST, TreeChangeType.UPDATE_THRESHOLD]
    assert len(links) == 1
    assert links[0].link_type == "AMBIGUOUS"


def test_store_upserts_work_order_trigger_request_as_traceable_draft(tmp_path) -> None:
    store = TreeProposalStore(tmp_path / "tree_proposals")
    request = FaultTreeGenerationRequest(
        request_id="FTGR-CASE-1",
        source_case_id="CASE-1",
        work_order_id="WO-1",
        trigger_reason="unsupported development case-only",
        candidate_start_symptom="高速行驶时仪表偶发动力受限",
        candidate_failure_domain="动力系统",
        candidate_root_hypotheses=["电池包单体压差异常", "VCU 扭矩保护触发"],
        candidate_tests=["读取 BMS 冻结帧", "检查 VCU 扭矩限制状态"],
        evidence_ids=["EV-1"],
        source_refs=["runs/CASE-1.jsonl"],
    )

    first = store.upsert_from_generation_request(request)
    second = store.upsert_from_generation_request(request)

    proposals = store.load_proposals()
    links = store.load_case_links(first.proposal_id)
    assert first.proposal_id == second.proposal_id
    assert len(proposals) == 1
    assert proposals[0].source_type == "WORK_ORDER_TRIGGER"
    assert proposals[0].source_request_id == "FTGR-CASE-1"
    assert proposals[0].source_case_ids == ["CASE-1", "WO-1"]
    assert proposals[0].root_cause_families == ["电池包单体压差异常", "VCU 扭矩保护触发"]
    assert proposals[0].candidate_tests == ["读取 BMS 冻结帧", "检查 VCU 扭矩限制状态"]
    assert len(links) == 1
    assert links[0].case_id == "CASE-1"
    assert links[0].work_order_id == "WO-1"


def test_store_upserts_dynamic_cluster_as_traceable_draft(tmp_path) -> None:
    store = TreeProposalStore(tmp_path / "tree_proposals")
    cluster = FaultTreeRequestCluster(
        cluster_id="FTC-DOOR-NVH",
        cluster_key="body|door-nvh",
        review_status=FaultTreeReviewStatus.DRAFT_REQUESTED,
        request_ids=["FTGR-1", "FTGR-2"],
        source_case_ids=["CASE-1", "CASE-2"],
        supporting_case_ids=["WO-1", "WO-2"],
        representative_start_symptom="右后门颠簸路异响",
        candidate_failure_domain="车身",
        merged_root_hypotheses=["防撞梁焊点焊核尺寸不足", "门锁扣位置偏差"],
        merged_tests=["焊点外观和焊核尺寸检查", "锁扣位置测量"],
        evidence_ids=["EV-1", "EV-2"],
        source_refs=["runs/CASE-1.jsonl", "runs/CASE-2.jsonl"],
        support_count=2,
        min_support_for_review=3,
        allowed_next_statuses=[],
        recommended_next_step="继续收集同类 case-only 工单。",
    )

    proposal = store.upsert_from_request_cluster(cluster)
    store.upsert_from_request_cluster(cluster)

    proposals = store.load_proposals()
    links = store.load_case_links(proposal.proposal_id)
    assert len(proposals) == 1
    assert proposals[0].source_type == "DYNAMIC_CLUSTER"
    assert proposals[0].source_cluster_id == "FTC-DOOR-NVH"
    assert proposals[0].source_case_ids == ["CASE-1", "CASE-2", "WO-1", "WO-2"]
    assert proposals[0].candidate_transitions[0].startswith("右后门颠簸路异响 -> 防撞梁焊点焊核尺寸不足")
    assert any("支持案例数 2/3" in note for note in proposals[0].risk_notes)
    assert len(links) == 4
    assert {link.case_id for link in links} == {"CASE-1", "CASE-2", "WO-1", "WO-2"}
