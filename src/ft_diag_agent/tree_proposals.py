from __future__ import annotations

from hashlib import sha1
from pathlib import Path
from typing import Literal

from ft_diag_agent.models import (
    FaultTreeGenerationRequest,
    FaultTreeRequestCluster,
    TreeChangeType,
    TreeGenerationArtifact,
    TreeProposal,
    TreeProposalCaseLink,
    TreeProposalEvalResult,
    TreeProposalKind,
    TreeProposalReviewLog,
    TreeProposalStatus,
    TreeReleaseArtifact,
    utc_now_iso,
)


class TreeProposalStore:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.artifacts_dir = self.base_dir / "artifacts"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    @property
    def proposals_path(self) -> Path:
        return self.base_dir / "proposals.jsonl"

    @property
    def case_links_path(self) -> Path:
        return self.base_dir / "case_links.jsonl"

    @property
    def eval_results_path(self) -> Path:
        return self.base_dir / "eval_results.jsonl"

    @property
    def review_logs_path(self) -> Path:
        return self.base_dir / "review_logs.jsonl"

    def load_proposals(self) -> list[TreeProposal]:
        proposals = _read_jsonl(self.proposals_path, TreeProposal)
        proposals.sort(key=lambda item: (item.updated_at or item.created_at or "", item.proposal_id), reverse=True)
        return proposals

    def get_proposal(self, proposal_id: str) -> TreeProposal | None:
        return next((item for item in self.load_proposals() if item.proposal_id == proposal_id), None)

    def save_proposal(self, proposal: TreeProposal) -> None:
        proposal.updated_at = utc_now_iso()
        proposals = {item.proposal_id: item for item in self.load_proposals()}
        proposals[proposal.proposal_id] = proposal
        ordered = sorted(proposals.values(), key=lambda item: (item.updated_at, item.proposal_id), reverse=True)
        _write_jsonl(self.proposals_path, ordered)

    def append_proposal(self, proposal: TreeProposal) -> None:
        self.save_proposal(proposal)

    def append_case_link(self, link: TreeProposalCaseLink) -> None:
        _append_jsonl(self.case_links_path, link)

    def save_case_link(self, link: TreeProposalCaseLink) -> None:
        links = self.load_case_links()
        deduped = [
            item
            for item in links
            if not (
                item.proposal_id == link.proposal_id
                and item.case_id == link.case_id
                and item.work_order_id == link.work_order_id
            )
        ]
        _write_jsonl(self.case_links_path, [*deduped, link])

    def load_case_links(self, proposal_id: str | None = None) -> list[TreeProposalCaseLink]:
        links = _read_jsonl(self.case_links_path, TreeProposalCaseLink)
        if proposal_id:
            links = [item for item in links if item.proposal_id == proposal_id]
        return links

    def append_eval_result(self, result: TreeProposalEvalResult) -> None:
        _append_jsonl(self.eval_results_path, result)

    def load_eval_results(
        self,
        proposal_id: str | None = None,
        eval_suite: str | None = None,
    ) -> list[TreeProposalEvalResult]:
        results = _read_jsonl(self.eval_results_path, TreeProposalEvalResult)
        if proposal_id:
            results = [item for item in results if item.proposal_id == proposal_id]
        if eval_suite:
            results = [item for item in results if item.eval_suite == eval_suite]
        return results

    def latest_eval_result(
        self,
        proposal_id: str,
        eval_suite: str,
    ) -> TreeProposalEvalResult | None:
        results = self.load_eval_results(proposal_id, eval_suite=eval_suite)
        if not results:
            return None
        return sorted(results, key=lambda item: (item.created_at, item.eval_id))[-1]

    def append_review_log(self, log: TreeProposalReviewLog) -> None:
        _append_jsonl(self.review_logs_path, log)

    def load_review_logs(self, proposal_id: str | None = None) -> list[TreeProposalReviewLog]:
        logs = _read_jsonl(self.review_logs_path, TreeProposalReviewLog)
        if proposal_id:
            logs = [item for item in logs if item.proposal_id == proposal_id]
        logs.sort(key=lambda item: (item.created_at, item.review_id), reverse=True)
        return logs

    def review_proposal(
        self,
        proposal_id: str,
        *,
        decision: Literal["APPROVE", "REJECT", "REQUEST_CHANGES"],
        reviewer: str | None,
        rationale: str,
        required_changes: list[str] | None = None,
        precheck_result: dict | None = None,
    ) -> TreeProposalReviewLog | None:
        proposal = self.get_proposal(proposal_id)
        if not proposal:
            return None
        from_status = proposal.status
        to_status = _review_target_status(from_status, decision)
        log = TreeProposalReviewLog(
            proposal_id=proposal.proposal_id,
            from_status=from_status,
            to_status=to_status,
            reviewer=reviewer,
            decision=decision,
            rationale=rationale,
            required_changes=required_changes or [],
            precheck_result=precheck_result or {},
        )
        proposal.status = to_status
        proposal.allowed_next_statuses = _allowed_next_statuses(to_status)
        self.save_proposal(proposal)
        self.append_review_log(log)
        return log

    def save_artifact_snapshot(
        self,
        proposal: TreeProposal,
        *,
        artifact: TreeGenerationArtifact | None = None,
    ) -> Path:
        target_dir = self.artifacts_dir / proposal.proposal_id
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "proposal.json").write_text(
            proposal.model_dump_json(ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if artifact:
            (target_dir / "artifact.json").write_text(
                artifact.model_dump_json(ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return target_dir

    def load_artifact_snapshot(self, proposal_id: str) -> TreeGenerationArtifact | None:
        path = self.artifacts_dir / proposal_id / "artifact.json"
        if not path.exists():
            return None
        return TreeGenerationArtifact.model_validate_json(path.read_text(encoding="utf-8"))

    def save_release_artifact(self, release_artifact: TreeReleaseArtifact) -> Path:
        target_dir = self.artifacts_dir / release_artifact.proposal_id / "release"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "release_artifact.json").write_text(
            release_artifact.model_dump_json(ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (target_dir / "manifest.json").write_text(
            release_artifact.manifest.model_dump_json(ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (target_dir / "rollback_metadata.json").write_text(
            release_artifact.rollback.model_dump_json(ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (target_dir / "generated_ttl_preview.ttl").write_text(
            release_artifact.generated_ttl_preview,
            encoding="utf-8",
        )
        (target_dir / "ttl_diff.md").write_text(release_artifact.ttl_diff_md, encoding="utf-8")
        return target_dir

    def load_release_artifact(self, proposal_id: str) -> TreeReleaseArtifact | None:
        path = self.artifacts_dir / proposal_id / "release" / "release_artifact.json"
        if not path.exists():
            return None
        return TreeReleaseArtifact.model_validate_json(path.read_text(encoding="utf-8"))

    def upsert_from_generation_request(
        self,
        request: FaultTreeGenerationRequest,
    ) -> TreeProposal:
        proposal_id = _proposal_id("REQ", request.request_id)
        existing = self.get_proposal(proposal_id)
        proposal = TreeProposal(
            proposal_id=proposal_id,
            status=existing.status if existing else TreeProposalStatus.DRAFT_TREE,
            source_type="WORK_ORDER_TRIGGER",
            source_request_id=request.request_id,
            phenomenon_bucket=_normalize_bucket(request.candidate_start_symptom),
            candidate_start_symptom=request.candidate_start_symptom,
            candidate_failure_domain=request.candidate_failure_domain,
            root_cause_families=request.candidate_root_hypotheses,
            candidate_tests=request.candidate_tests,
            candidate_transitions=request.candidate_transitions or _transition_notes(
                request.candidate_start_symptom,
                request.candidate_root_hypotheses,
                request.candidate_tests,
            ),
            source_case_ids=_unique([request.source_case_id, request.work_order_id]),
            evidence_ids=request.evidence_ids,
            source_refs=request.source_refs,
            confidence_summary=(
                "由 unsupported development case-only 诊断沉淀的 DRAFT_TREE；"
                f"包含 {len(request.candidate_root_hypotheses)} 个候选根因族、"
                f"{len(request.candidate_tests)} 个候选检查项。"
            ),
            risk_notes=_request_risk_notes(request),
            allowed_next_statuses=existing.allowed_next_statuses
            if existing and existing.status != TreeProposalStatus.DRAFT_TREE
            else _allowed_next_statuses(TreeProposalStatus.DRAFT_TREE),
            created_at=existing.created_at if existing else request.created_at,
        )
        self.save_proposal(proposal)
        self.save_case_link(
            TreeProposalCaseLink(
                proposal_id=proposal.proposal_id,
                case_id=request.source_case_id,
                work_order_id=request.work_order_id,
                link_type="SUPPORTS",
                matched_root_cause_family=request.candidate_root_hypotheses[0]
                if request.candidate_root_hypotheses
                else None,
                useful_tests=request.candidate_tests,
                human_confirmed=None,
                notes=request.trigger_reason,
            )
        )
        self.save_artifact_snapshot(proposal)
        return proposal

    def upsert_from_request_cluster(
        self,
        cluster: FaultTreeRequestCluster,
    ) -> TreeProposal:
        proposal_id = _proposal_id("CLUSTER", cluster.cluster_id)
        existing = self.get_proposal(proposal_id)
        proposal = TreeProposal(
            proposal_id=proposal_id,
            status=existing.status if existing else TreeProposalStatus.DRAFT_TREE,
            source_type="DYNAMIC_CLUSTER",
            source_cluster_id=cluster.cluster_id,
            phenomenon_bucket=_normalize_bucket(cluster.representative_start_symptom),
            candidate_start_symptom=cluster.representative_start_symptom,
            candidate_failure_domain=cluster.candidate_failure_domain,
            root_cause_families=cluster.merged_root_hypotheses,
            candidate_tests=cluster.merged_tests,
            candidate_transitions=_transition_notes(
                cluster.representative_start_symptom,
                cluster.merged_root_hypotheses,
                cluster.merged_tests,
            ),
            source_case_ids=_unique([*cluster.source_case_ids, *cluster.supporting_case_ids]),
            evidence_ids=cluster.evidence_ids,
            source_refs=cluster.source_refs,
            confidence_summary=(
                "由跨 runs case-only 动态树聚类沉淀的 DRAFT_TREE；"
                f"支持案例 {cluster.support_count}/{cluster.min_support_for_review}，"
                f"包含 {len(cluster.merged_root_hypotheses)} 个候选根因族、"
                f"{len(cluster.merged_tests)} 个候选检查项。"
            ),
            risk_notes=_cluster_risk_notes(cluster),
            allowed_next_statuses=existing.allowed_next_statuses
            if existing and existing.status != TreeProposalStatus.DRAFT_TREE
            else _allowed_next_statuses(TreeProposalStatus.DRAFT_TREE),
            created_at=existing.created_at if existing else cluster.created_at,
        )
        self.save_proposal(proposal)
        for case_id in proposal.source_case_ids:
            self.save_case_link(
                TreeProposalCaseLink(
                    proposal_id=proposal.proposal_id,
                    case_id=case_id,
                    link_type="SUPPORTS",
                    matched_root_cause_family=cluster.merged_root_hypotheses[0]
                    if cluster.merged_root_hypotheses
                    else None,
                    useful_tests=cluster.merged_tests,
                    human_confirmed=None,
                    notes=cluster.recommended_next_step,
                )
            )
        self.save_artifact_snapshot(proposal)
        return proposal

    def upsert_tree_change_proposal(self, proposal: TreeProposal) -> TreeProposal:
        if proposal.proposal_kind != TreeProposalKind.TREE_CHANGE:
            proposal.proposal_kind = TreeProposalKind.TREE_CHANGE
        if not proposal.proposal_id:
            proposal.proposal_id = _tree_change_proposal_id(
                proposal.target_tree_id or "UNKNOWN_TREE",
                proposal.source_case_ids,
                proposal.change_types,
            )
        existing = self.get_proposal(proposal.proposal_id)
        if existing:
            proposal.status = existing.status
            proposal.allowed_next_statuses = existing.allowed_next_statuses
            proposal.created_at = existing.created_at
        elif not proposal.allowed_next_statuses:
            proposal.allowed_next_statuses = _allowed_next_statuses(TreeProposalStatus.DRAFT_TREE)
        self.save_proposal(proposal)
        for case_id in proposal.source_case_ids:
            self.save_case_link(
                TreeProposalCaseLink(
                    proposal_id=proposal.proposal_id,
                    case_id=case_id,
                    link_type="AMBIGUOUS",
                    matched_root_cause_family=proposal.root_cause_families[0]
                    if proposal.root_cause_families
                    else None,
                    useful_tests=proposal.candidate_tests,
                    human_confirmed=None,
                    notes=proposal.change_summary,
                )
            )
        self.save_artifact_snapshot(proposal)
        return proposal


def _review_target_status(
    from_status: TreeProposalStatus,
    decision: Literal["APPROVE", "REJECT", "REQUEST_CHANGES"],
) -> TreeProposalStatus:
    if decision == "REJECT":
        return TreeProposalStatus.REJECTED
    if decision == "REQUEST_CHANGES":
        return from_status
    if from_status == TreeProposalStatus.DRAFT_TREE:
        return TreeProposalStatus.CANDIDATE_TREE
    if from_status == TreeProposalStatus.CANDIDATE_TREE:
        return TreeProposalStatus.GRAY_TREE
    if from_status == TreeProposalStatus.GRAY_TREE:
        return TreeProposalStatus.RELEASED_TREE
    return from_status


def _allowed_next_statuses(status: TreeProposalStatus) -> list[TreeProposalStatus]:
    if status == TreeProposalStatus.DRAFT_TREE:
        return [TreeProposalStatus.CANDIDATE_TREE, TreeProposalStatus.REJECTED]
    if status == TreeProposalStatus.CANDIDATE_TREE:
        return [TreeProposalStatus.GRAY_TREE, TreeProposalStatus.REJECTED]
    if status == TreeProposalStatus.GRAY_TREE:
        return [TreeProposalStatus.RELEASED_TREE, TreeProposalStatus.REJECTED]
    return []


def _proposal_id(kind: str, value: str) -> str:
    digest = sha1(f"{kind}:{value}".encode()).hexdigest()[:10]
    return f"TP-{kind[:3]}-{digest}"


def _tree_change_proposal_id(
    target_tree_id: str,
    source_case_ids: list[str],
    change_types: list[TreeChangeType],
) -> str:
    basis = "|".join(
        [
            target_tree_id,
            ",".join(sorted(source_case_ids)),
            ",".join(sorted(str(item) for item in change_types)),
        ]
    )
    return _proposal_id("CHANGE", basis)


def _normalize_bucket(value: str) -> str:
    text = "".join(value.lower().split())
    return text[:48] or "unknown"


def _unique(values: list[str | None]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _transition_notes(start: str, roots: list[str], tests: list[str]) -> list[str]:
    if not roots:
        return []
    fallback_test = tests[0] if tests else "MISSING 待补充检查项"
    return [
        f"{start} -> {root} / {tests[index] if index < len(tests) else fallback_test}"
        for index, root in enumerate(roots)
    ]


def _request_risk_notes(request: FaultTreeGenerationRequest) -> list[str]:
    notes = [
        "WORK_ORDER_TRIGGER 入口产物为 DRAFT_TREE，不能生产 PASS。",
        "该 proposal 来自开发态 case-only 诊断，需要 Tree Proposal Eval、专家审核和后续树生成 HITL。",
        "正式发布前必须转换为 TTL/release manifest，并具备 replay/eval、证据绑定和回滚信息。",
    ]
    if not request.candidate_root_hypotheses:
        notes.append("缺少候选 root cause family，需要补充或确认。")
    if not request.candidate_tests:
        notes.append("缺少候选检查项，需要补充或确认。")
    return notes


def _cluster_risk_notes(cluster: FaultTreeRequestCluster) -> list[str]:
    notes = [
        "DYNAMIC_CLUSTER 入口产物为 DRAFT_TREE，不能生产 PASS。",
        "该 proposal 来自跨 runs 聚类，仍需人工确认同类案例是否真的支持同一故障树。",
        "正式发布前必须转换为 TTL/release manifest，并具备 replay/eval、证据绑定和回滚信息。",
    ]
    if cluster.support_count < cluster.min_support_for_review:
        notes.append(
            f"支持案例数 {cluster.support_count}/{cluster.min_support_for_review}，尚未达到人工审核建议门槛。"
        )
    if not cluster.merged_root_hypotheses:
        notes.append("聚类缺少候选 root cause family，需要补充或确认。")
    if not cluster.merged_tests:
        notes.append("聚类缺少候选检查项，需要补充或确认。")
    return notes


def _append_jsonl(path: Path, item) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(item.model_dump_json(ensure_ascii=False) + "\n")


def _write_jsonl(path: Path, items: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(item.model_dump_json(ensure_ascii=False) + "\n")


def _read_jsonl(path: Path, model_type) -> list:
    if not path.exists():
        return []
    items = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                items.append(model_type.model_validate_json(line))
    return items
