from __future__ import annotations

import json

from pydantic import BaseModel, Field

from ft_diag_agent.fault_tree import RdfFaultTreeRepository
from ft_diag_agent.llm import LlmProvider
from ft_diag_agent.models import (
    CoverageDecision,
    CoverageStatus,
    DiagnosisMode,
    WorkOrder,
    WorkOrderClassification,
)
from ft_diag_agent.rag import DocumentRag
from ft_diag_agent.settings import Settings
from ft_diag_agent.work_orders import work_order_to_intake_text


class _LlmClassification(BaseModel):
    tree_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    coverage_status: CoverageStatus = CoverageStatus.UNSUPPORTED
    reasoning_summary: str = ""
    signals: list[str] = Field(default_factory=list)


class WorkOrderClassifier:
    def __init__(
        self,
        repository: RdfFaultTreeRepository,
        rag: DocumentRag,
        settings: Settings | None = None,
    ):
        self.repository = repository
        self.rag = rag
        self.settings = settings or Settings()
        self.llm = LlmProvider(self.settings)

    def classify(self, order: WorkOrder, diagnosis_mode: DiagnosisMode | None = None) -> WorkOrderClassification:
        mode = diagnosis_mode or _mode_from_settings(self.settings.diagnosis_mode)
        query = work_order_to_intake_text(order)
        rule = self._rule_classify(order, query, mode)
        if rule.coverage_status == CoverageStatus.COVERED and rule.confidence >= 0.78:
            return rule

        llm_result = self._llm_classify(order, rule)
        if llm_result and llm_result.confidence > rule.confidence:
            llm_result.diagnosis_mode = mode
            return llm_result
        return rule

    def coverage_decision(self, classification: WorkOrderClassification) -> CoverageDecision:
        return CoverageDecision(
            status=classification.coverage_status,
            diagnosis_mode=classification.diagnosis_mode,
            tree_id=classification.tree_id,
            confidence=classification.confidence,
            reason=classification.reasoning_summary,
        )

    def _rule_classify(
        self,
        order: WorkOrder,
        query: str,
        mode: DiagnosisMode,
    ) -> WorkOrderClassification:
        primary_query = order.failure_phenomenon or query
        matches = self.repository.search_trees(primary_query, top_k=3)
        if not matches and query != primary_query:
            matches = self.repository.search_trees(query, top_k=3)
        signals: list[str] = []
        best_tree_id = None
        best_score = 0.0
        if matches:
            tree, score, reasons = matches[0]
            best_tree_id = tree.tree_id
            best_score = score
            signals.extend(reasons)

        expected = order.expected_leaf_symptom_id
        if expected:
            for tree in self.repository.trees.values():
                if expected in tree.symptom_ids:
                    best_tree_id = tree.tree_id
                    best_score = max(best_score, 0.95)
                    signals.append(f"expected_leaf_in:{tree.tree_id}")

        text = query.lower()
        if _needs_evidence_guardrail(text):
            if mode == DiagnosisMode.DEVELOPMENT:
                mode = DiagnosisMode.CASE_ONLY_EXPLORATORY
            return WorkOrderClassification(
                tree_id=None,
                confidence=0.0,
                coverage_status=CoverageStatus.UNSUPPORTED,
                diagnosis_mode=mode,
                matched_phenomenon=order.failure_phenomenon,
                reasoning_summary="事故/多系统/信息不足场景，需要补证后再诊断",
                signals=["guardrail:need_more_evidence"],
            )
        keyword_map = {
            "FT_001": ["车机", "黑屏", "屏幕", "lvds", "背光", "pmic", "镜像"],
            "FT_002": ["车门", "门锁", "锁扣", "铰链", "密封条", "电动门", "吸合"],
        }
        for tree_id, keywords in keyword_map.items():
            if _tree_suppressed_by_negative_context(tree_id, text):
                signals.append(f"suppressed:{tree_id}:negative_or_adjacent_context")
                continue
            hits = _positive_keyword_hits(text, keywords)
            if hits and len(hits) >= 2:
                score = min(0.55 + len(hits) * 0.08, 0.9)
                if score > best_score:
                    best_tree_id = tree_id
                    best_score = score
                signals.append(f"keywords:{tree_id}:{','.join(hits)}")

        if best_tree_id and _tree_suppressed_by_negative_context(best_tree_id, text) and not expected:
            best_score = min(best_score, 0.45)
            signals.append(f"suppressed_best:{best_tree_id}")

        if best_tree_id and best_score >= 0.55:
            status = CoverageStatus.COVERED
            reason = f"规则/检索命中 {best_tree_id}，置信度 {best_score:.2f}"
        elif best_tree_id:
            status = CoverageStatus.AMBIGUOUS
            reason = f"弱匹配 {best_tree_id}，需要 LLM 或人工确认"
        else:
            status = CoverageStatus.UNSUPPORTED
            reason = "未匹配到现有故障树覆盖范围"

        if status != CoverageStatus.COVERED and mode == DiagnosisMode.DEVELOPMENT:
            best_tree_id = None
            best_score = 0.0
            status = CoverageStatus.UNSUPPORTED
            mode = DiagnosisMode.CASE_ONLY_EXPLORATORY

        return WorkOrderClassification(
            tree_id=best_tree_id,
            confidence=round(best_score, 4),
            coverage_status=status,
            diagnosis_mode=mode,
            matched_phenomenon=order.failure_phenomenon,
            reasoning_summary=reason,
            signals=list(dict.fromkeys(signals)),
        )

    def _llm_classify(
        self,
        order: WorkOrder,
        fallback: WorkOrderClassification,
    ) -> WorkOrderClassification | None:
        available = [
            {
                "tree_id": tree.tree_id,
                "tree_name": tree.name,
                "scope": tree.applicable_scope,
                "start_symptoms": [
                    self.repository.get_symptom(sid).name
                    for sid in tree.symptom_ids
                    if self.repository.get_symptom(sid) and self.repository.get_symptom(sid).level == "start"
                ],
            }
            for tree in self.repository.trees.values()
        ]
        user = {
            "work_order": order.model_dump(mode="json"),
            "available_fault_trees": available,
            "fallback": fallback.model_dump(mode="json"),
            "required_json_schema": {
                "tree_id": "FT_001|FT_002|null",
                "confidence": "0..1",
                "coverage_status": "COVERED|UNSUPPORTED|AMBIGUOUS",
                "reasoning_summary": "short Chinese summary",
                "signals": ["short evidence strings"],
            },
        }
        result = self.llm.json_completion(
            system_prompt=(
                "你是制造质量诊断工单分类器。判断工单是否被已有故障树覆盖。"
                "只能选择给定故障树；若不覆盖，tree_id 必须为 null。"
            ),
            user_prompt=json.dumps(user, ensure_ascii=False),
            response_model=_LlmClassification,
            complexity="pro" if fallback.confidence < 0.7 else "fast",
        )
        if not result:
            return None
        return WorkOrderClassification(
            tree_id=result.tree_id,
            confidence=result.confidence,
            coverage_status=result.coverage_status,
            diagnosis_mode=fallback.diagnosis_mode,
            matched_phenomenon=order.failure_phenomenon,
            reasoning_summary=f"LLM: {result.reasoning_summary}",
            signals=[*fallback.signals, *result.signals],
        )


def _mode_from_settings(value: str) -> DiagnosisMode:
    try:
        return DiagnosisMode(value.upper())
    except ValueError:
        return DiagnosisMode.PRODUCTION


def _positive_keyword_hits(text: str, keywords: list[str]) -> list[str]:
    hits: list[str] = []
    for keyword in keywords:
        start = 0
        while True:
            idx = text.find(keyword.lower(), start)
            if idx < 0:
                break
            window = text[max(0, idx - 6) : idx]
            if not any(marker in window for marker in ("无", "没有", "未见", "未发现", "不存在", "非")):
                hits.append(keyword)
                break
            start = idx + len(keyword)
    return hits


def _tree_suppressed_by_negative_context(tree_id: str, text: str) -> bool:
    if tree_id == "FT_001":
        return any(
            marker in text
            for marker in [
                "无黑屏",
                "非黑屏",
                "没有黑屏",
                "屏幕显示正常",
                "车机屏幕正常",
                "车机屏幕显示正常",
                "屏幕正常",
            ]
        )
    if tree_id == "FT_002":
        if "车门无法关闭" in text:
            return False
        return any(
            marker in text
            for marker in [
                "尾门",
                "后备箱",
                "车门关闭正常",
                "机械锁止正常",
                "不是无法关闭",
                "非侧门",
            ]
        )
    return False


def _needs_evidence_guardrail(text: str) -> bool:
    if "信息不足" in text or "功能异常" in text and "无现象" in text:
        return True
    return "事故" in text and any(marker in text for marker in ["多系统", "多处", "无法归因", "记录缺失"])
