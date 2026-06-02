from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ft_diag_agent.models import DiagnosticState, ReplayRecord


class ReplayStore:
    def __init__(self, runs_dir: str | Path):
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def append(self, case_id: str, record: ReplayRecord) -> Path:
        path = self.runs_dir / f"{case_id}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json(ensure_ascii=False) + "\n")
        return path

    def snapshot(
        self,
        state_before: DiagnosticState,
        state_after: DiagnosticState,
        accepted: bool | None = None,
        human_decision: dict[str, Any] | None = None,
        rejected_reason: str | None = None,
    ) -> ReplayRecord:
        latest_tool = state_after.tool_calls[-1].model_dump() if state_after.tool_calls else {}
        record = ReplayRecord(
            state_before=state_before.model_dump(mode="json"),
            planner_output=[a.model_dump(mode="json") for a in state_after.planned_actions],
            tool_call=latest_tool,
            tool_result=latest_tool,
            state_after=state_after.model_dump(mode="json"),
            gate_result=state_after.gate_result.model_dump(mode="json") if state_after.gate_result else {},
            human_decision=human_decision or {},
            accepted=accepted,
            rejected_reason=rejected_reason,
        )
        return record

    def iter_records(self) -> list[ReplayRecord]:
        records: list[ReplayRecord] = []
        for path in sorted(self.runs_dir.glob("*.jsonl")):
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        records.append(ReplayRecord.model_validate(json.loads(line)))
        return records
