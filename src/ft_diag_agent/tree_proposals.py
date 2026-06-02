from __future__ import annotations

from pathlib import Path
from typing import Literal

from ft_diag_agent.models import (
    TreeGenerationArtifact,
    TreeProposal,
    TreeProposalCaseLink,
    TreeProposalEvalResult,
    TreeProposalReviewLog,
    TreeProposalStatus,
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

    def load_case_links(self, proposal_id: str | None = None) -> list[TreeProposalCaseLink]:
        links = _read_jsonl(self.case_links_path, TreeProposalCaseLink)
        if proposal_id:
            links = [item for item in links if item.proposal_id == proposal_id]
        return links

    def append_eval_result(self, result: TreeProposalEvalResult) -> None:
        _append_jsonl(self.eval_results_path, result)

    def load_eval_results(self, proposal_id: str | None = None) -> list[TreeProposalEvalResult]:
        results = _read_jsonl(self.eval_results_path, TreeProposalEvalResult)
        if proposal_id:
            results = [item for item in results if item.proposal_id == proposal_id]
        return results

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
    return from_status


def _allowed_next_statuses(status: TreeProposalStatus) -> list[TreeProposalStatus]:
    if status == TreeProposalStatus.DRAFT_TREE:
        return [TreeProposalStatus.CANDIDATE_TREE, TreeProposalStatus.REJECTED]
    if status == TreeProposalStatus.CANDIDATE_TREE:
        return [TreeProposalStatus.REJECTED]
    return []


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
