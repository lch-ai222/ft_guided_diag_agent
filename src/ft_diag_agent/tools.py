from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ft_diag_agent.fault_tree import RdfFaultTreeRepository
from ft_diag_agent.models import EvidenceItem, ExecutedTest, ToolCallRecord, ToolStatus
from ft_diag_agent.rag import DocumentRag


class ToolInput(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolOutput(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)
    evidence_items: list[EvidenceItem] = Field(default_factory=list)


class DiagnosticTool(ABC):
    name: str
    description: str
    requires_human_confirmation: bool = False

    @abstractmethod
    def run(self, payload: dict[str, Any]) -> ToolOutput:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, DiagnosticTool] = {}

    def register(self, tool: DiagnosticTool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def call(self, tool_name: str, payload: dict[str, Any]) -> ToolCallRecord:
        start = time.perf_counter()
        tool = self._tools.get(tool_name)
        if not tool:
            return ToolCallRecord(
                tool_name=tool_name,
                input_payload=payload,
                status=ToolStatus.ERROR,
                error=f"Tool not registered: {tool_name}",
            )
        try:
            ToolInput(payload=payload)
            output = tool.run(payload)
            status = ToolStatus.SUCCESS
            error = None
        except (ValidationError, Exception) as exc:
            output = ToolOutput()
            status = ToolStatus.ERROR
            error = str(exc)
        return ToolCallRecord(
            tool_name=tool_name,
            input_payload=payload,
            output_payload=output.data,
            status=status,
            error=error,
            latency_ms=int((time.perf_counter() - start) * 1000),
            evidence_items=output.evidence_items,
        )


class FaultTreeSearchTool(DiagnosticTool):
    name = "fault_tree_search"
    description = "Search parsed fault trees by normalized phenomenon."

    def __init__(self, repository: RdfFaultTreeRepository):
        self.repository = repository

    def run(self, payload: dict[str, Any]) -> ToolOutput:
        phenomenon = str(payload.get("phenomenon", ""))
        matches = self.repository.search_trees(phenomenon)
        return ToolOutput(
            data={
                "matches": [
                    {"tree_id": tree.tree_id, "score": score, "reasons": reasons}
                    for tree, score, reasons in matches
                ]
            }
        )


class RagSearchTool(DiagnosticTool):
    name = "rag_search"
    description = "Search local real documents/cases and return evidence candidates."

    def __init__(self, rag: DocumentRag):
        self.rag = rag

    def run(self, payload: dict[str, Any]) -> ToolOutput:
        query = str(payload.get("query", ""))
        top_k = int(payload.get("top_k", 5))
        evidence = self.rag.search(query, top_k=top_k)
        return ToolOutput(data={"count": len(evidence)}, evidence_items=evidence)


class HumanInputTool(DiagnosticTool):
    name = "human_input"
    description = "Record human inspection/test result."
    requires_human_confirmation = True

    def run(self, payload: dict[str, Any]) -> ToolOutput:
        test = ExecutedTest(**payload)
        claim = f"人工检测 {test.test_id}: {test.result}"
        if test.notes:
            claim += f"；{test.notes}"
        evidence = EvidenceItem(
            source_type="HITL",
            source_id=test.test_id,
            claim=claim,
            supports_cause_id=payload.get("supports_cause_id"),
            supports_node_id=payload.get("supports_node_id"),
            strength=float(payload.get("strength", 0.7)),
            raw_payload=test.model_dump(),
        )
        return ToolOutput(data={"executed_test": test.model_dump()}, evidence_items=[evidence])


class StubProductionTool(DiagnosticTool):
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    def run(self, payload: dict[str, Any]) -> ToolOutput:
        test_id = str(payload.get("test_id") or payload.get("id") or "stub")
        evidence = EvidenceItem(
            source_type=self.name,
            source_id=test_id,
            claim=f"{self.name} production integration is not configured; payload recorded for trace.",
            strength=0.1,
            supports_node_id=payload.get("supports_node_id"),
            supports_cause_id=payload.get("supports_cause_id"),
            raw_payload=payload,
        )
        return ToolOutput(
            data={
                "configured": False,
                "payload": payload,
                "executed_test": {
                    "test_id": test_id,
                    "result": f"{self.name} stub executed",
                    "passed": None,
                    "notes": "Production tool is not configured.",
                },
            },
            evidence_items=[evidence],
        )


def build_default_registry(repository: RdfFaultTreeRepository, rag: DocumentRag) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(FaultTreeSearchTool(repository))
    registry.register(RagSearchTool(rag))
    registry.register(HumanInputTool())
    registry.register(StubProductionTool("spc_query", "Query SPC metrics and anomaly evidence."))
    registry.register(StubProductionTool("bom_lookup", "Lookup BOM/component metadata."))
    registry.register(StubProductionTool("fp_growth_rules", "Query mined association rules."))
    registry.register(StubProductionTool("quality_case_search", "Search structured quality cases."))
    return registry
