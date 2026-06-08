from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from ft_diag_agent.models import (
    TreeGenerationArtifact,
    TreeProposal,
    TreeProposalCaseLink,
    TreeProposalEvalResult,
    TreeProposalPromotionPrecheck,
    TreeProposalReviewLog,
    TreeProposalStatus,
)
from ft_diag_agent.tree_generation import generation_hitl_items
from ft_diag_agent.tree_proposal_eval import (
    TREE_PROPOSAL_EVAL_SUITE,
    TREE_PROPOSAL_REPLAY_SHADOW_EVAL_SUITE,
)

LifecycleStatus = Literal["DONE", "CURRENT", "WARNING", "BLOCKED", "PENDING"]


@dataclass(frozen=True)
class TreeProposalLifecycleStep:
    step_id: str
    label: str
    status: LifecycleStatus
    detail: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def tree_proposal_lifecycle_steps(
    proposal: TreeProposal,
    *,
    artifact: TreeGenerationArtifact | None = None,
    case_links: list[TreeProposalCaseLink] | None = None,
    eval_results: list[TreeProposalEvalResult] | None = None,
    review_logs: list[TreeProposalReviewLog] | None = None,
    precheck: TreeProposalPromotionPrecheck | None = None,
) -> list[TreeProposalLifecycleStep]:
    case_links = case_links or []
    eval_results = eval_results or []
    review_logs = review_logs or []
    latest_eval = _latest_eval(eval_results, TREE_PROPOSAL_EVAL_SUITE)
    latest_shadow_eval = _latest_eval(eval_results, TREE_PROPOSAL_REPLAY_SHADOW_EVAL_SUITE)
    case_count = len({*proposal.source_case_ids, *(link.case_id for link in case_links)})
    source_count = len({*proposal.source_refs, *proposal.evidence_ids})
    hitl_pending = len(generation_hitl_items(artifact)) if artifact else None

    return [
        TreeProposalLifecycleStep(
            "source",
            "1 来源输入",
            _source_status(proposal, case_count, source_count),
            _source_detail(proposal, case_count, source_count),
        ),
        TreeProposalLifecycleStep(
            "draft",
            "2 DRAFT 草案",
            "DONE" if artifact else "BLOCKED",
            "已生成 TreeGenerationArtifact 本体草案。"
            if artifact
            else "缺少 artifact，当前只是发现记录或 proposal skeleton。",
        ),
        TreeProposalLifecycleStep(
            "structure",
            "3 L1/L2/L3 结构",
            _structure_status(artifact),
            _structure_detail(artifact),
        ),
        TreeProposalLifecycleStep(
            "hitl",
            "4 HITL 补全",
            _hitl_status(artifact, hitl_pending),
            _hitl_detail(artifact, hitl_pending),
        ),
        TreeProposalLifecycleStep(
            "eval",
            "5 Proposal Eval",
            _eval_status(latest_eval),
            _eval_detail(latest_eval),
        ),
        TreeProposalLifecycleStep(
            "candidate_review",
            "6 人工审核",
            _review_status(proposal, precheck),
            _review_detail(proposal, precheck),
        ),
        TreeProposalLifecycleStep(
            "gray",
            "7 Replay / Shadow",
            _gray_status(proposal, latest_shadow_eval),
            _gray_detail(proposal, review_logs, latest_shadow_eval),
        ),
        TreeProposalLifecycleStep(
            "release",
            "8 生产 TTL 发布",
            _release_status(proposal),
            _release_detail(proposal),
        ),
    ]


def proposal_skeleton_mermaid(proposal: TreeProposal) -> str:
    start_label = _node_label("L1 start", proposal.candidate_start_symptom or "MISSING start", "DISCOVERY_ONLY")
    lines = [
        "flowchart TD",
        f'  S["{start_label}"]',
    ]
    roots = proposal.root_cause_families or ["MISSING root cause family"]
    tests = proposal.candidate_tests or ["MISSING candidate test"]
    for index, root in enumerate(roots[:12]):
        root_id = f"R{index + 1}"
        test = tests[index] if index < len(tests) else tests[0]
        root_label = _node_label("L3 root", root, "DISCOVERY_ONLY")
        lines.append(f'  {root_id}["{root_label}"]')
        lines.append(f'  S -- "{_escape_mermaid(test)}" --> {root_id}')
    return "\n".join(lines)


def artifact_node_rows(artifact: TreeGenerationArtifact) -> list[dict[str, object]]:
    return [
        {
            "id": item.entity_id,
            "level": item.level or "-",
            "type": item.entity_type.value,
            "name": item.name or item.description or item.entity_id,
            "name_status": item.name_status.value,
            "description_status": item.description_status.value,
            "source_refs": item.source_refs,
            "chunk_ids": item.chunk_ids,
            "needs_hitl": bool(item.properties.get("needs_generation_hitl")),
        }
        for item in artifact.symptoms
    ]


def artifact_transition_rows(artifact: TreeGenerationArtifact) -> list[dict[str, object]]:
    symptom_by_id = {item.entity_id: item for item in artifact.symptoms}
    test_by_id = {item.entity_id: item for item in artifact.tests}
    rows: list[dict[str, object]] = []
    for transition in artifact.transitions:
        rows.append(
            {
                "id": transition.transition_id,
                "source": _entity_name(symptom_by_id, transition.source_id),
                "target": _entity_name(symptom_by_id, transition.target_id),
                "tests": [
                    test_by_id[test_id].name or test_by_id[test_id].description or test_id
                    for test_id in transition.test_ids
                    if test_id in test_by_id
                ]
                or transition.test_ids,
                "condition": transition.condition,
                "condition_status": transition.condition_status.value,
                "description_status": transition.description_status.value,
                "source_refs": transition.source_refs,
                "chunk_ids": transition.chunk_ids,
            }
        )
    return rows


def proposal_skeleton_node_rows(proposal: TreeProposal) -> list[dict[str, object]]:
    rows = [
        {
            "level": "L1 start",
            "name": proposal.candidate_start_symptom or "MISSING start",
            "status": "DISCOVERY_ONLY",
            "source": proposal.source_type,
        }
    ]
    rows.extend(
        {
            "level": "L3 root",
            "name": root,
            "status": "DISCOVERY_ONLY",
            "source": proposal.source_type,
        }
        for root in (proposal.root_cause_families or ["MISSING root cause family"])
    )
    return rows


def proposal_skeleton_transition_rows(proposal: TreeProposal) -> list[dict[str, object]]:
    roots = proposal.root_cause_families or ["MISSING root cause family"]
    tests = proposal.candidate_tests or ["MISSING candidate test"]
    return [
        {
            "source": proposal.candidate_start_symptom or "MISSING start",
            "target": root,
            "test": tests[index] if index < len(tests) else tests[0],
            "status": "DISCOVERY_ONLY",
        }
        for index, root in enumerate(roots)
    ]


def _source_status(proposal: TreeProposal, case_count: int, source_count: int) -> LifecycleStatus:
    if case_count and source_count:
        return "DONE"
    if source_count or proposal.source_job_id or proposal.source_request_id or proposal.source_cluster_id:
        return "WARNING"
    return "BLOCKED"


def _source_detail(proposal: TreeProposal, case_count: int, source_count: int) -> str:
    if case_count and source_count:
        return f"已关联 {case_count} 个 case，{source_count} 条 evidence/source ref。"
    if source_count:
        return f"已有 {source_count} 条 evidence/source ref，但缺少 source case / case link。"
    if proposal.source_job_id or proposal.source_request_id or proposal.source_cluster_id:
        return "已有来源入口 ID，但缺少 case/evidence 绑定。"
    return "缺少来源资料、case link 和 evidence。"


def _structure_status(artifact: TreeGenerationArtifact | None) -> LifecycleStatus:
    if not artifact:
        return "BLOCKED"
    start_count = sum(1 for item in artifact.symptoms if item.level == "start")
    root_count = sum(1 for item in artifact.symptoms if item.level == "root")
    has_transition = bool(artifact.transitions)
    if start_count and root_count and has_transition:
        return "DONE"
    return "BLOCKED"


def _structure_detail(artifact: TreeGenerationArtifact | None) -> str:
    if not artifact:
        return "缺少 artifact，无法展示真实 L1/L2/L3 proposed tree。"
    start_count = sum(1 for item in artifact.symptoms if item.level == "start")
    inner_count = sum(1 for item in artifact.symptoms if item.level == "inner")
    root_count = sum(1 for item in artifact.symptoms if item.level == "root")
    return (
        f"L1/start={start_count}，L2/inner={inner_count}，"
        f"L3/root={root_count}，transition={len(artifact.transitions)}。"
    )


def _hitl_status(artifact: TreeGenerationArtifact | None, hitl_pending: int | None) -> LifecycleStatus:
    if not artifact:
        return "PENDING"
    if hitl_pending:
        return "CURRENT"
    return "DONE"


def _hitl_detail(artifact: TreeGenerationArtifact | None, hitl_pending: int | None) -> str:
    if not artifact:
        return "等待生成 artifact 后扫描 MISSING / EXTRACTED_INFERRED 字段。"
    if hitl_pending:
        return f"仍有 {hitl_pending} 个字段需要树生成 HITL 补全/确认。"
    return "无待确认树生成 HITL 字段。"


def _eval_status(eval_result: TreeProposalEvalResult | None) -> LifecycleStatus:
    if not eval_result:
        return "CURRENT"
    if eval_result.unsafe_findings:
        return "BLOCKED"
    return "DONE"


def _eval_detail(eval_result: TreeProposalEvalResult | None) -> str:
    if not eval_result:
        return "尚未运行 Tree Proposal Eval。"
    if eval_result.unsafe_findings:
        return "Eval 阻塞项：" + "；".join(eval_result.unsafe_findings)
    return "Tree Proposal Eval 无 unsafe findings。"


def _review_status(
    proposal: TreeProposal,
    precheck: TreeProposalPromotionPrecheck | None,
) -> LifecycleStatus:
    reviewed_statuses = {
        TreeProposalStatus.CANDIDATE_TREE,
        TreeProposalStatus.GRAY_TREE,
        TreeProposalStatus.RELEASED_TREE,
    }
    if proposal.status in reviewed_statuses:
        return "DONE"
    if precheck and precheck.verdict == "BLOCKED":
        return "BLOCKED"
    if precheck and precheck.verdict == "NEEDS_MORE_EVIDENCE":
        return "WARNING"
    return "CURRENT"


def _review_detail(
    proposal: TreeProposal,
    precheck: TreeProposalPromotionPrecheck | None,
) -> str:
    reviewed_statuses = {
        TreeProposalStatus.CANDIDATE_TREE,
        TreeProposalStatus.GRAY_TREE,
        TreeProposalStatus.RELEASED_TREE,
    }
    if proposal.status in reviewed_statuses:
        return f"当前已进入 {proposal.status.value}。"
    if precheck:
        return f"预审结论：{precheck.verdict}。"
    return "等待人工审核 DRAFT_TREE。"


def _gray_status(proposal: TreeProposal, shadow_eval: TreeProposalEvalResult | None) -> LifecycleStatus:
    if proposal.status in {TreeProposalStatus.GRAY_TREE, TreeProposalStatus.RELEASED_TREE}:
        return "DONE"
    if shadow_eval and shadow_eval.unsafe_findings:
        return "BLOCKED"
    if shadow_eval and shadow_eval.metrics.get("shadow_ready") is True:
        return "DONE"
    if proposal.status == TreeProposalStatus.CANDIDATE_TREE:
        return "CURRENT"
    return "PENDING"


def _gray_detail(
    proposal: TreeProposal,
    review_logs: list[TreeProposalReviewLog],
    shadow_eval: TreeProposalEvalResult | None,
) -> str:
    if proposal.status in {TreeProposalStatus.GRAY_TREE, TreeProposalStatus.RELEASED_TREE}:
        return "已进入或通过 GRAY_TREE 阶段。"
    if shadow_eval and shadow_eval.unsafe_findings:
        return "shadow eval 阻塞项：" + "；".join(shadow_eval.unsafe_findings)
    if shadow_eval and shadow_eval.metrics.get("shadow_ready") is True:
        count = shadow_eval.metrics.get("shadow_relevant_case_count", 0)
        rate = shadow_eval.metrics.get("shadow_support_rate")
        rate_text = f"{rate:.0%}" if isinstance(rate, float) else "N/A"
        return f"shadow eval 通过；相关 replay={count}，支持率={rate_text}。"
    if proposal.status == TreeProposalStatus.CANDIDATE_TREE:
        return "等待 replay-based eval / shadow diagnosis。"
    approval_count = sum(1 for log in review_logs if log.decision == "APPROVE")
    return f"等待候选审核完成；已有 {approval_count} 条批准类审核日志。"


def _release_status(proposal: TreeProposal) -> LifecycleStatus:
    if proposal.status == TreeProposalStatus.RELEASED_TREE:
        return "DONE"
    if proposal.status == TreeProposalStatus.GRAY_TREE:
        return "CURRENT"
    return "PENDING"


def _release_detail(proposal: TreeProposal) -> str:
    if proposal.status == TreeProposalStatus.RELEASED_TREE:
        return "已发布为生产可用 Released Tree。"
    if proposal.status == TreeProposalStatus.GRAY_TREE:
        return "等待 release manifest、rollback metadata、TTL diff 和正式签核。"
    return "尚未进入生产 TTL 发布阶段。"


def _node_label(level: str, name: str, status: str) -> str:
    return _escape_mermaid(f"{level}\\n{name}\\n{status}")


def _entity_name(entity_by_id: dict, entity_id: str) -> str:
    entity = entity_by_id.get(entity_id)
    if not entity:
        return entity_id
    return entity.name or entity.description or entity.entity_id


def _escape_mermaid(value: str) -> str:
    return str(value).replace('"', "'").replace("\n", "<br/>")


def _latest_eval(
    eval_results: list[TreeProposalEvalResult],
    eval_suite: str,
) -> TreeProposalEvalResult | None:
    matching = [item for item in eval_results if item.eval_suite == eval_suite]
    if not matching:
        return None
    return sorted(matching, key=lambda item: (item.created_at, item.eval_id))[-1]
