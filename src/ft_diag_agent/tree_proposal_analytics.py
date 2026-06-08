from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ft_diag_agent.models import (
    TreeProposal,
    TreeProposalCaseLink,
    TreeProposalEvalResult,
    TreeProposalReviewLog,
)


class RootCauseFamilyAggregate(BaseModel):
    root_cause_family: str
    proposal_ids: list[str] = Field(default_factory=list)
    support_case_count: int = 0
    refute_case_count: int = 0
    ambiguous_case_count: int = 0
    human_confirmed_count: int = 0
    human_rejected_count: int = 0
    human_confirmation_rate: float | None = None
    high_risk_counter_evidence_count: int = 0
    statuses: dict[str, int] = Field(default_factory=dict)


class RepeatedTestAggregate(BaseModel):
    test_name: str
    proposal_ids: list[str] = Field(default_factory=list)
    support_case_count: int = 0
    refute_case_count: int = 0
    human_confirmed_count: int = 0
    human_rejected_count: int = 0
    human_confirmation_rate: float | None = None
    high_risk_counter_evidence_count: int = 0


class HighRiskCounterEvidence(BaseModel):
    proposal_id: str
    source_type: Literal["CASE_LINK", "EVAL", "REVIEW", "RISK_NOTE"]
    signal: str
    severity: Literal["BLOCKER", "WARNING"] = "WARNING"
    case_id: str | None = None


class TreeProposalAggregateReport(BaseModel):
    proposal_id: str
    phenomenon_bucket: str
    bucket_proposal_ids: list[str] = Field(default_factory=list)
    bucket_proposal_count: int = 0
    bucket_support_case_count: int = 0
    bucket_refute_case_count: int = 0
    bucket_human_confirmation_rate: float | None = None
    root_cause_families: list[RootCauseFamilyAggregate] = Field(default_factory=list)
    repeated_tests: list[RepeatedTestAggregate] = Field(default_factory=list)
    high_risk_counter_evidence: list[HighRiskCounterEvidence] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    satisfied: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


MIN_BUCKET_PROPOSALS_FOR_PATTERN = 2
MIN_HUMAN_CONFIRMATION_RATE = 0.5
ROOT_REFUTE_BLOCK_RATE = 0.4
ROOT_REFUTE_WARN_RATE = 0.25
TEST_LOW_CONFIRMATION_WARN_COUNT = 2
HIGH_RISK_TERMS = [
    "unsafe",
    "反证",
    "误诊",
    "高风险",
    "阻塞",
    "不可",
    "拒绝",
    "低置信",
    "REFUTED",
    "REJECT",
]


def build_tree_proposal_aggregate_report(
    proposal: TreeProposal,
    *,
    proposals: list[TreeProposal],
    case_links: list[TreeProposalCaseLink],
    eval_results: list[TreeProposalEvalResult],
    review_logs: list[TreeProposalReviewLog],
) -> TreeProposalAggregateReport:
    bucket = _bucket(proposal)
    bucket_proposals = [item for item in proposals if _bucket(item) == bucket]
    bucket_ids = [item.proposal_id for item in bucket_proposals]
    bucket_links = [item for item in case_links if item.proposal_id in bucket_ids]
    bucket_evals = [item for item in eval_results if item.proposal_id in bucket_ids]
    bucket_reviews = [item for item in review_logs if item.proposal_id in bucket_ids]

    root_rows = _root_family_rows(bucket_proposals, bucket_links, bucket_evals, bucket_reviews)
    test_rows = _test_rows(bucket_proposals, bucket_links, bucket_evals, bucket_reviews)
    counter_evidence = _counter_evidence(bucket_proposals, bucket_links, bucket_evals, bucket_reviews)
    support_count = len({item.case_id for item in bucket_links if item.link_type == "SUPPORTS"})
    refute_count = len({item.case_id for item in bucket_links if item.link_type == "REFUTES"})
    confirmation_rate = _confirmation_rate(bucket_links)

    blockers, warnings, satisfied, recommended_actions = _aggregate_judgement(
        proposal=proposal,
        bucket_proposals=bucket_proposals,
        root_rows=root_rows,
        test_rows=test_rows,
        counter_evidence=counter_evidence,
        confirmation_rate=confirmation_rate,
    )
    return TreeProposalAggregateReport(
        proposal_id=proposal.proposal_id,
        phenomenon_bucket=bucket,
        bucket_proposal_ids=bucket_ids,
        bucket_proposal_count=len(bucket_ids),
        bucket_support_case_count=support_count,
        bucket_refute_case_count=refute_count,
        bucket_human_confirmation_rate=confirmation_rate,
        root_cause_families=root_rows,
        repeated_tests=test_rows,
        high_risk_counter_evidence=counter_evidence,
        blockers=blockers,
        warnings=warnings,
        satisfied=satisfied,
        recommended_actions=recommended_actions,
        metrics={
            "aggregate_bucket_proposal_count": len(bucket_ids),
            "aggregate_bucket_support_case_count": support_count,
            "aggregate_bucket_refute_case_count": refute_count,
            "aggregate_bucket_human_confirmation_rate": confirmation_rate,
            "aggregate_high_risk_counter_evidence_count": len(counter_evidence),
        },
    )


def _root_family_rows(
    proposals: list[TreeProposal],
    links: list[TreeProposalCaseLink],
    eval_results: list[TreeProposalEvalResult],
    review_logs: list[TreeProposalReviewLog],
) -> list[RootCauseFamilyAggregate]:
    rows: dict[str, RootCauseFamilyAggregate] = {}
    proposal_by_id = {item.proposal_id: item for item in proposals}
    for proposal in proposals:
        for family in proposal.root_cause_families:
            key = _norm(family)
            row = rows.setdefault(key, RootCauseFamilyAggregate(root_cause_family=family))
            row.proposal_ids = _unique([*row.proposal_ids, proposal.proposal_id])
            status = str(proposal.status.value)
            row.statuses[status] = row.statuses.get(status, 0) + 1
    for link in links:
        families = [link.matched_root_cause_family] if link.matched_root_cause_family else []
        if not families and link.proposal_id in proposal_by_id:
            families = proposal_by_id[link.proposal_id].root_cause_families
        for family in families:
            if not family:
                continue
            row = rows.setdefault(_norm(family), RootCauseFamilyAggregate(root_cause_family=family))
            row.proposal_ids = _unique([*row.proposal_ids, link.proposal_id])
            if link.link_type == "SUPPORTS":
                row.support_case_count += 1
            elif link.link_type == "REFUTES":
                row.refute_case_count += 1
            else:
                row.ambiguous_case_count += 1
            if link.human_confirmed is True:
                row.human_confirmed_count += 1
            elif link.human_confirmed is False:
                row.human_rejected_count += 1
    for row in rows.values():
        row.human_confirmation_rate = _rate(row.human_confirmed_count, row.human_rejected_count)
        row.high_risk_counter_evidence_count = _family_risk_count(
            row.root_cause_family,
            eval_results,
            review_logs,
        )
    return sorted(rows.values(), key=lambda item: (-len(item.proposal_ids), item.root_cause_family))


def _test_rows(
    proposals: list[TreeProposal],
    links: list[TreeProposalCaseLink],
    eval_results: list[TreeProposalEvalResult],
    review_logs: list[TreeProposalReviewLog],
) -> list[RepeatedTestAggregate]:
    rows: dict[str, RepeatedTestAggregate] = {}
    for proposal in proposals:
        for test in proposal.candidate_tests:
            key = _norm(test)
            row = rows.setdefault(key, RepeatedTestAggregate(test_name=test))
            row.proposal_ids = _unique([*row.proposal_ids, proposal.proposal_id])
    for link in links:
        for test in link.useful_tests:
            key = _norm(test)
            row = rows.setdefault(key, RepeatedTestAggregate(test_name=test))
            row.proposal_ids = _unique([*row.proposal_ids, link.proposal_id])
            if link.link_type == "SUPPORTS":
                row.support_case_count += 1
            elif link.link_type == "REFUTES":
                row.refute_case_count += 1
            if link.human_confirmed is True:
                row.human_confirmed_count += 1
            elif link.human_confirmed is False:
                row.human_rejected_count += 1
    for row in rows.values():
        row.human_confirmation_rate = _rate(row.human_confirmed_count, row.human_rejected_count)
        row.high_risk_counter_evidence_count = _test_risk_count(row.test_name, eval_results, review_logs)
    return sorted(rows.values(), key=lambda item: (-len(item.proposal_ids), item.test_name))


def _counter_evidence(
    proposals: list[TreeProposal],
    links: list[TreeProposalCaseLink],
    eval_results: list[TreeProposalEvalResult],
    review_logs: list[TreeProposalReviewLog],
) -> list[HighRiskCounterEvidence]:
    rows: list[HighRiskCounterEvidence] = []
    for link in links:
        if link.link_type == "REFUTES" or link.human_confirmed is False:
            rows.append(
                HighRiskCounterEvidence(
                    proposal_id=link.proposal_id,
                    source_type="CASE_LINK",
                    signal=link.notes or f"{link.link_type} / human_confirmed={link.human_confirmed}",
                    severity="BLOCKER" if link.link_type == "REFUTES" else "WARNING",
                    case_id=link.case_id,
                )
            )
    for result in eval_results:
        for finding in result.unsafe_findings:
            rows.append(
                HighRiskCounterEvidence(
                    proposal_id=result.proposal_id,
                    source_type="EVAL",
                    signal=f"{result.eval_suite}: {finding}",
                    severity="BLOCKER",
                )
            )
    for log in review_logs:
        if log.decision in {"REJECT", "REQUEST_CHANGES"}:
            rows.append(
                HighRiskCounterEvidence(
                    proposal_id=log.proposal_id,
                    source_type="REVIEW",
                    signal=log.rationale,
                    severity="BLOCKER" if log.decision == "REJECT" else "WARNING",
                )
            )
    for proposal in proposals:
        for note in proposal.risk_notes:
            if _has_high_risk_term(note):
                rows.append(
                    HighRiskCounterEvidence(
                        proposal_id=proposal.proposal_id,
                        source_type="RISK_NOTE",
                        signal=note,
                        severity="WARNING",
                    )
                )
    return rows


def _aggregate_judgement(
    *,
    proposal: TreeProposal,
    bucket_proposals: list[TreeProposal],
    root_rows: list[RootCauseFamilyAggregate],
    test_rows: list[RepeatedTestAggregate],
    counter_evidence: list[HighRiskCounterEvidence],
    confirmation_rate: float | None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    satisfied: list[str] = []
    actions: list[str] = []
    if len(bucket_proposals) < MIN_BUCKET_PROPOSALS_FOR_PATTERN:
        warnings.append("同类 phenomenon bucket 的 proposal 数不足，尚不能证明稳定故障模式。")
        actions.append("继续收集同类 case-only 或批量文档 proposal 后再做发布级判断。")
    else:
        satisfied.append(f"同类 phenomenon bucket 已累计 {len(bucket_proposals)} 个 proposal。")
    for family in proposal.root_cause_families:
        row = next((item for item in root_rows if _norm(item.root_cause_family) == _norm(family)), None)
        if not row:
            continue
        total = row.support_case_count + row.refute_case_count
        refute_rate = row.refute_case_count / total if total else 0.0
        if refute_rate >= ROOT_REFUTE_BLOCK_RATE and row.refute_case_count:
            blockers.append(f"root cause family '{family}' 的跨 proposal 反证比例 {refute_rate:.0%}，高于阻塞阈值。")
            actions.append("先复核反证 case、拆分 root cause family 或补充反证检查。")
        elif refute_rate >= ROOT_REFUTE_WARN_RATE and row.refute_case_count:
            warnings.append(f"root cause family '{family}' 存在 {row.refute_case_count} 条反证 case。")
            actions.append("在人工审核中说明反证处理方式。")
    if confirmation_rate is not None and confirmation_rate < MIN_HUMAN_CONFIRMATION_RATE:
        warnings.append(f"同类 proposal 人工确认有效率 {confirmation_rate:.0%}，低于建议阈值。")
        actions.append("补充人工确认案例，或剔除低确认率 root/test。")
    low_confirm_tests = [
        item
        for item in test_rows
        if len(item.proposal_ids) >= TEST_LOW_CONFIRMATION_WARN_COUNT
        and item.human_confirmation_rate is not None
        and item.human_confirmation_rate < MIN_HUMAN_CONFIRMATION_RATE
    ]
    for item in low_confirm_tests:
        warnings.append(f"重复检查 '{item.test_name}' 出现 {len(item.proposal_ids)} 个 proposal，但人工确认率偏低。")
        actions.append("复核该 test 是否为有效区分检查，必要时替换为更强判别检查。")
    blocker_evidence = [item for item in counter_evidence if item.severity == "BLOCKER"]
    if blocker_evidence:
        blockers.append(f"同类 proposal 存在 {len(blocker_evidence)} 条高风险反证/拒绝/unsafe 记录。")
        actions.append("先关闭或解释高风险反证，再提交晋升审核。")
    elif counter_evidence:
        warnings.append(f"同类 proposal 存在 {len(counter_evidence)} 条风险提示，需要审核关注。")
    if not blockers and not warnings:
        satisfied.append("跨 proposal 聚合未发现高风险反证或低确认率重复检查。")
    return _unique(blockers), _unique(warnings), _unique(satisfied), _unique(actions)


def _family_risk_count(
    family: str,
    eval_results: list[TreeProposalEvalResult],
    review_logs: list[TreeProposalReviewLog],
) -> int:
    text = _norm(family)
    return sum(
        1
        for item in [*eval_results, *review_logs]
        if text and text in _norm(str(item.model_dump(mode="json")))
    )


def _test_risk_count(
    test: str,
    eval_results: list[TreeProposalEvalResult],
    review_logs: list[TreeProposalReviewLog],
) -> int:
    text = _norm(test)
    return sum(
        1
        for item in [*eval_results, *review_logs]
        if text and text in _norm(str(item.model_dump(mode="json")))
    )


def _bucket(proposal: TreeProposal) -> str:
    return _norm(proposal.phenomenon_bucket or proposal.candidate_start_symptom) or "unknown"


def _norm(value: str | None) -> str:
    return "".join(str(value or "").lower().split())


def _rate(confirmed: int, rejected: int) -> float | None:
    total = confirmed + rejected
    return round(confirmed / total, 4) if total else None


def _confirmation_rate(links: list[TreeProposalCaseLink]) -> float | None:
    confirmed = sum(1 for item in links if item.human_confirmed is True)
    rejected = sum(1 for item in links if item.human_confirmed is False)
    return _rate(confirmed, rejected)


def _has_high_risk_term(text: str) -> bool:
    normalized = _norm(text)
    return any(_norm(term) in normalized for term in HIGH_RISK_TERMS)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))
