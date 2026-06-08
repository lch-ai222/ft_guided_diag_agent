from ft_diag_agent.models import (
    TreeProposal,
    TreeProposalCaseLink,
    TreeProposalEvalResult,
    TreeProposalReviewLog,
    TreeProposalStatus,
)
from ft_diag_agent.tree_proposal_analytics import build_tree_proposal_aggregate_report
from ft_diag_agent.tree_proposal_precheck import assess_tree_proposal_promotion


def _proposal(proposal_id: str, bucket: str = "doorclose") -> TreeProposal:
    return TreeProposal(
        proposal_id=proposal_id,
        phenomenon_bucket=bucket,
        candidate_start_symptom="车门关闭异响",
        root_cause_families=["锁扣位置偏差"],
        candidate_tests=["锁扣位置测量"],
        source_case_ids=[f"CASE-{proposal_id}"],
    )


def test_aggregate_groups_bucket_root_family_and_repeated_tests() -> None:
    proposals = [_proposal("TP-1"), _proposal("TP-2")]
    links = [
        TreeProposalCaseLink(
            proposal_id="TP-1",
            case_id="CASE-1",
            matched_root_cause_family="锁扣位置偏差",
            useful_tests=["锁扣位置测量"],
            human_confirmed=True,
        ),
        TreeProposalCaseLink(
            proposal_id="TP-2",
            case_id="CASE-2",
            matched_root_cause_family="锁扣位置偏差",
            useful_tests=["锁扣位置测量"],
            human_confirmed=True,
        ),
    ]

    report = build_tree_proposal_aggregate_report(
        proposals[0],
        proposals=proposals,
        case_links=links,
        eval_results=[],
        review_logs=[],
    )

    assert report.bucket_proposal_count == 2
    assert report.bucket_human_confirmation_rate == 1.0
    assert report.root_cause_families[0].support_case_count == 2
    assert report.repeated_tests[0].human_confirmed_count == 2
    assert any("累计 2 个 proposal" in item for item in report.satisfied)


def test_aggregate_blocks_when_root_family_has_high_refute_rate() -> None:
    proposal = _proposal("TP-1")
    links = [
        TreeProposalCaseLink(
            proposal_id="TP-1",
            case_id="CASE-1",
            link_type="SUPPORTS",
            matched_root_cause_family="锁扣位置偏差",
            human_confirmed=True,
        ),
        TreeProposalCaseLink(
            proposal_id="TP-1",
            case_id="CASE-2",
            link_type="REFUTES",
            matched_root_cause_family="锁扣位置偏差",
            human_confirmed=False,
            notes="人工复核发现该 case 由铰链干涉导致，反证锁扣位置偏差。",
        ),
    ]

    report = build_tree_proposal_aggregate_report(
        proposal,
        proposals=[proposal],
        case_links=links,
        eval_results=[],
        review_logs=[],
    )

    assert any("反证比例" in item for item in report.blockers)
    assert any(
        item.source_type == "CASE_LINK" and item.severity == "BLOCKER"
        for item in report.high_risk_counter_evidence
    )


def test_precheck_consumes_aggregate_report_as_blocking_evidence() -> None:
    proposal = _proposal("TP-1")
    eval_result = TreeProposalEvalResult(
        proposal_id=proposal.proposal_id,
        eval_suite="tree_proposal_v1",
        status_at_eval=TreeProposalStatus.DRAFT_TREE,
        metrics={"evidence_binding_rate": 1.0},
    )
    aggregate_report = build_tree_proposal_aggregate_report(
        proposal,
        proposals=[proposal],
        case_links=[
            TreeProposalCaseLink(
                proposal_id=proposal.proposal_id,
                case_id="CASE-1",
                link_type="REFUTES",
                matched_root_cause_family="锁扣位置偏差",
                human_confirmed=False,
            )
        ],
        eval_results=[
            TreeProposalEvalResult(
                proposal_id=proposal.proposal_id,
                eval_suite="tree_proposal_replay_shadow_v1",
                status_at_eval=TreeProposalStatus.DRAFT_TREE,
                unsafe_findings=["ROOT_CONTRADICTED"],
            )
        ],
        review_logs=[
            TreeProposalReviewLog(
                proposal_id=proposal.proposal_id,
                from_status=TreeProposalStatus.DRAFT_TREE,
                to_status=TreeProposalStatus.DRAFT_TREE,
                decision="REQUEST_CHANGES",
                rationale="需要解释反证案例。",
            )
        ],
    )

    precheck = assess_tree_proposal_promotion(
        proposal,
        case_links=[],
        eval_results=[eval_result],
        aggregate_report=aggregate_report,
    )

    assert precheck.verdict == "BLOCKED"
    assert any("高风险反证" in item for item in precheck.blockers)
    assert precheck.metrics["aggregate_high_risk_counter_evidence_count"] >= 2
