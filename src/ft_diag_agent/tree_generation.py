from __future__ import annotations

import csv
import json
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from shutil import copy2
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ft_diag_agent.llm import LlmProvider
from ft_diag_agent.models import (
    EvidenceItem,
    FieldStatus,
    OntologyEntityDraft,
    OntologyEntityType,
    OntologyExtractionPass,
    OntologyValidationIssue,
    SymptomTransitionDraft,
    TreeGenerationArtifact,
    TreeGenerationHitlDecision,
    TreeGenerationHitlSuggestion,
    TreeGenerationHitlSuggestionOption,
    TreeGenerationInputDocument,
    TreeGenerationJob,
    TreeGenerationJobStatus,
    TreeGenerationQuality,
    TreeGenerationValidationReport,
    TreeProposal,
    TreeProposalReviewLog,
    utc_now_iso,
)
from ft_diag_agent.settings import Settings
from ft_diag_agent.tree_proposals import TreeProposalStore

SUPPORTED_TREE_SOURCE_SUFFIXES = {".pdf", ".md", ".txt", ".csv"}

TREE_GENERATION_SYSTEM_PROMPT = """
你是故障树本体建模 Agent。你只负责从质量报告、8D、SOP、FMEA、维修资料中抽取和修复本体草案。
不要直接输出最终 FaultTree，不要维护 FaultTree.symptom_ids；只输出 FailureSymptom、OntologyTest、
OntologyMeasure、SymptomTransition。最终 FaultTree 由系统代码从 start 节点沿 transition 做确定性 BFS 重建。

核心本体定义：
- FailureSymptom 表达“异常状态是什么”，不能是检查、验证、复测、分析、处理、整改等动作。
- OntologyTest 表达“如何判断/检查”，例如测量、读取、观察、拆检、比对、复测、验证、确认。
- OntologyMeasure 表达“如何处置”，例如更换、维修、调整、返修、工艺改善、刷新、标定。
- SymptomTransition 表达“如何从上级异常定位到下级异常”，方向必须是 source -> target，并且必须绑定 test。

FailureSymptom 层级定义：
- start：L1，入口异常。必须是客户、产线、检测系统可以直接观察或报告的最大公约数现象。
- inner：L2，中间异常状态。必须是经过检查、观察、分析后得到的更具体异常状态，不能是检查动作本身。
- root：L3，终止根因。应是资料中已定位到的、不可再拆分、可直接挂接措施或验证闭环的最深层可处置异常。

质量规则：
- 同一失效域只保留一个语义最大公约数 start；同义入口必须合并。
- root 不能是“未知原因/待确认原因”，除非明确标记低置信占位且不得作为可发布根因。
- 每条 transition 必须至少绑定一个 OntologyTest；原文没有明确检查项时，创建 MISSING 占位 test。
- transition 方向必须从 start/inner 指向更具体的 inner/root，不允许 root 有出边，不允许 start 有入边。
- 如果原文说“通过读取日志发现 IIC 通信超时”，OntologyTest=读取日志，FailureSymptom=IIC 通信超时。
- 抽取阶段字段状态只能使用 EXTRACTED_EXPLICIT、EXTRACTED_INFERRED、MISSING。
- 不要在抽取阶段输出 SUGGESTED_GROUNDED 或 SUGGESTED_LOW_CONF；这两个状态只用于后续 HITL 补全建议。
- 必须保留 evidence_refs，引用输入资料片段中的 chunk_id。
- 任务标题和任务描述只作为 job metadata / 抽取目标背景，不是 evidence source；禁止把任务标题或任务描述
  抽取为 FailureSymptom、OntologyTest、OntologyMeasure 或 SymptomTransition。

输出必须是严格 JSON，不能返回空本体草案。如果资料足够，至少输出 1 个 start、1 个 root、1 个 test、1 条 transition。
""".strip()

HITL_SUGGESTION_SYSTEM_PROMPT = """
你是树生成阶段的领域/工艺/维修专家审核员，只为 EXTRACTED_INFERRED 或 MISSING 字段提供人工确认前的建议选项。
你不能直接发布或修改故障树，只能给用户可确认、可拒绝、可手动修订的候选值。

建议必须遵守：
- 第一优先级是当前输入资料原文，必须解释建议如何被原文片段支持。
- 第二优先级是 RAG 命中的 SOP/FMEA/维修手册/历史工单，只能作为当前语境的补强证据。
- 第三优先级才是行业知识，用于补足表达、检查口径或维修术语，不得脱离当前场景自由生成。
- 如果原文和 RAG 都不足，不要编造选项；返回空 options，并说明需要补充哪些资料。
- 输出选项状态只能是 SUGGESTED_GROUNDED 或 SUGGESTED_LOW_CONF。
- 输出必须是严格 JSON。
""".strip()

GRAPH_JSON_CONTRACT = """
严格输出 JSON 字段：
{
  "extraction_summary": "...",
  "candidate_failure_domain": "...",
  "symptoms": [
    {
      "name": "入口或异常状态名称",
      "level": "start|inner|root",
      "description": "证据支持的说明",
      "name_status": "EXTRACTED_EXPLICIT|EXTRACTED_INFERRED|MISSING",
      "description_status": "EXTRACTED_EXPLICIT|EXTRACTED_INFERRED|MISSING",
      "evidence_refs": ["chunk_id"]
    }
  ],
  "tests": [
    {
      "name": "检查动作名称，缺失时为 null",
      "target": "该检查用于判断什么",
      "rule": "判定规则或条件",
      "description": "检查说明",
      "name_status": "EXTRACTED_EXPLICIT|EXTRACTED_INFERRED|MISSING",
      "evidence_refs": ["chunk_id"]
    }
  ],
  "measures": [
    {
      "name": "处置措施",
      "description": "措施说明",
      "name_status": "EXTRACTED_EXPLICIT|EXTRACTED_INFERRED|MISSING",
      "evidence_refs": ["chunk_id"]
    }
  ],
  "transitions": [
    {
      "source_name": "上级异常状态名称",
      "target_name": "下级异常状态名称",
      "test_names": ["绑定的检查动作名称"],
      "condition": "什么检查结果支持流向 target",
      "description": "诊断转移说明",
      "condition_status": "EXTRACTED_EXPLICIT|EXTRACTED_INFERRED|MISSING",
      "evidence_refs": ["chunk_id"]
    }
  ],
  "risk_notes": ["不确定性或资料缺口"]
}
字段名必须优先使用上面的 name/level/source_name/target_name/test_names。
""".strip()

LEVELING_JSON_CONTRACT = """
严格输出 JSON 字段：
{
  "extraction_summary": "...",
  "candidate_failure_domain": "...",
  "symptoms": [
    {
      "name": "入口、中间异常或终止根因名称",
      "level": "start|inner|root",
      "description": "证据支持的说明",
      "name_status": "EXTRACTED_EXPLICIT|EXTRACTED_INFERRED|MISSING",
      "description_status": "EXTRACTED_EXPLICIT|EXTRACTED_INFERRED|MISSING",
      "evidence_refs": ["chunk_id"]
    }
  ],
  "tests": [
    {
      "name": "检查动作名称，缺失时为 null",
      "target": "该检查用于判断什么",
      "rule": "判定规则或条件",
      "description": "检查说明",
      "name_status": "EXTRACTED_EXPLICIT|EXTRACTED_INFERRED|MISSING",
      "evidence_refs": ["chunk_id"]
    }
  ],
  "measures": [
    {
      "name": "处置措施",
      "description": "措施说明",
      "name_status": "EXTRACTED_EXPLICIT|EXTRACTED_INFERRED|MISSING",
      "evidence_refs": ["chunk_id"]
    }
  ],
  "transitions": [],
  "risk_notes": ["不确定性或资料缺口"]
}
PASS_2 只做实体分类、去重、start 合并和 start/inner/root 分级，不生成 SymptomTransition。
""".strip()


class TreeGenerationDraftPayload(BaseModel):
    candidate_start_symptom: str
    candidate_failure_domain: str | None = None
    root_cause_families: list[str] = Field(default_factory=list)
    candidate_tests: list[str] = Field(default_factory=list)
    candidate_measures: list[str] = Field(default_factory=list)
    candidate_transitions: list[str] = Field(default_factory=list)
    evidence_summary: list[str] = Field(default_factory=list)


def _first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _list_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    return [value] if value != "" else []


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def _coerce_risk_notes(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        parts = re.split(r"^(?:\d+[.、])\s*|(?<=[。；;\n])\s*(?:\d+[.、])\s*|(?:^|\n)\s*[-*]\s*", text)
        notes = [part.strip() for part in parts if part and part.strip()]
        return notes or [text]
    if isinstance(value, list):
        notes: list[str] = []
        for item in value:
            if isinstance(item, str):
                notes.extend(_coerce_risk_notes(item))
            elif item not in (None, ""):
                notes.append(json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item))
        return notes
    if isinstance(value, dict):
        return [json.dumps(value, ensure_ascii=False)]
    return [str(value)]


def _adapt_status(value: Any, default: FieldStatus) -> FieldStatus:
    if value in (None, ""):
        return default
    try:
        status = FieldStatus(str(value).strip().upper())
    except ValueError:
        return default
    if status in {FieldStatus.SUGGESTED_GROUNDED, FieldStatus.SUGGESTED_LOW_CONF}:
        return FieldStatus.EXTRACTED_INFERRED
    return status


def _evidence_refs_from(data: dict[str, Any], *keys: str) -> list[str]:
    refs: list[str] = []
    for key in keys:
        refs.extend(_coerce_str_list(data.get(key)))
    properties = data.get("properties")
    if isinstance(properties, dict):
        refs.extend(_coerce_str_list(properties.get("evidence_refs")))
        refs.extend(_coerce_str_list(properties.get("chunk_ids")))
    return list(dict.fromkeys(refs))


def _generation_hitl_properties(*statuses: FieldStatus) -> dict[str, Any]:
    reasons = [
        f"field:{status}"
        for status in statuses
        if status in {FieldStatus.EXTRACTED_INFERRED, FieldStatus.MISSING}
    ]
    return {"needs_generation_hitl": bool(reasons), "hitl_reasons": reasons}


def _name_from_candidate(value: dict[str, Any]) -> Any:
    return _first_present(
        value,
        "name",
        "symptom_name",
        "test_name",
        "measure_name",
        "label",
        "title",
        "failure_mode",
        "abnormal_state",
        "root_cause",
        "phenomenon",
        "symptom",
        "state",
        "issue",
        "problem",
        "check",
        "check_name",
        "test_method",
        "inspection",
        "action",
        "measure",
    )


def _candidate_type_text(value: dict[str, Any]) -> str:
    text = " ".join(
        str(item)
        for item in [
            value.get("type"),
            value.get("entity_type"),
            value.get("category"),
            value.get("class"),
            value.get("kind"),
            value.get("role"),
        ]
        if item
    )
    return text.lower()


def _candidate_type_bucket(value: dict[str, Any]) -> str:
    text = _candidate_type_text(value)
    name = str(_name_from_candidate(value) or "").lower()
    combined = f"{text} {name}"
    if any(item in combined for item in ["test", "check", "inspection", "检查", "检测", "测量", "读取", "验证"]):
        return "test"
    if any(item in combined for item in ["measure", "action", "fix", "repair", "处置", "措施", "整改", "维修", "更换"]):
        return "measure"
    return "symptom"


def _candidate_with_level(value: Any, level: str | None = None) -> Any:
    if isinstance(value, str):
        data: dict[str, Any] = {"name": value}
    elif isinstance(value, dict):
        data = dict(value)
    else:
        return value
    if level and not _first_present(data, "suggested_level", "level", "symptom_level"):
        data["suggested_level"] = level
    return data


class LlmSymptomCandidate(BaseModel):
    name: str
    evidence_refs: list[str] = Field(default_factory=list)
    description: str | None = None
    suggested_level: str | None = None
    rationale: str | None = None

    @model_validator(mode="before")
    @classmethod
    def adapt_shape(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"name": value}
        if not isinstance(value, dict):
            return value
        name = _name_from_candidate(value)
        if not name:
            return value
        suggested_level = _first_present(value, "suggested_level", "level", "symptom_level")
        if not suggested_level and any(key in value for key in ["root_cause", "root_cause_family"]):
            suggested_level = "root"
        return {
            **value,
            "name": name,
            "suggested_level": suggested_level,
            "description": _first_present(value, "description", "desc", "symptom_desc"),
            "evidence_refs": _evidence_refs_from(
                value,
                "evidence_refs",
                "chunk_ids",
                "symptom_chunk_ids",
                "source_refs",
            ),
        }


class LlmSymptomDraft(BaseModel):
    draft_id: str | None = None
    name: str
    level: str
    description: str | None = None
    name_status: FieldStatus = FieldStatus.EXTRACTED_INFERRED
    description_status: FieldStatus = FieldStatus.EXTRACTED_INFERRED
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def adapt_shape(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"name": value}
        if not isinstance(value, dict):
            return value
        name = _name_from_candidate(value)
        level = _first_present(value, "level", "symptom_level", "suggested_level")
        if not level and str(value.get("type", "")).lower() in {"start", "inner", "root"}:
            level = value["type"]
        return {
            **value,
            "draft_id": _first_present(value, "draft_id", "id", "symptom_id"),
            "name": name,
            "level": level,
            "description": _first_present(value, "description", "desc", "symptom_desc"),
            "name_status": _adapt_status(
                _first_present(value, "name_status", "symptom_name_status"),
                FieldStatus.EXTRACTED_INFERRED,
            ),
            "description_status": _adapt_status(
                _first_present(value, "description_status", "symptom_desc_status"),
                FieldStatus.EXTRACTED_INFERRED
                if _first_present(value, "description", "desc", "symptom_desc")
                else FieldStatus.MISSING,
            ),
            "evidence_refs": _evidence_refs_from(
                value,
                "evidence_refs",
                "chunk_ids",
                "symptom_chunk_ids",
                "source_refs",
            ),
        }


class LlmTestDraft(BaseModel):
    draft_id: str | None = None
    name: str | None = None
    target: str | None = None
    rule: str | None = None
    description: str | None = None
    name_status: FieldStatus = FieldStatus.EXTRACTED_INFERRED
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def adapt_shape(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"name": value}
        if not isinstance(value, dict):
            return value
        name = _first_present(
            value,
            "name",
            "test_name",
            "check_name",
            "check",
            "test_method",
            "inspection",
            "action",
            "label",
            "title",
        )
        return {
            **value,
            "draft_id": _first_present(value, "draft_id", "id", "test_id"),
            "name": name,
            "target": _first_present(value, "target", "test_target", "judges", "purpose"),
            "rule": _first_present(value, "rule", "test_rule", "condition", "criterion"),
            "description": _first_present(value, "description", "desc", "test_desc"),
            "name_status": _adapt_status(
                _first_present(value, "name_status", "test_name_status"),
                FieldStatus.EXTRACTED_INFERRED if name else FieldStatus.MISSING,
            ),
            "evidence_refs": _evidence_refs_from(value, "evidence_refs", "chunk_ids", "test_chunk_ids", "source_refs"),
        }


class LlmMeasureDraft(BaseModel):
    draft_id: str | None = None
    name: str
    description: str | None = None
    name_status: FieldStatus = FieldStatus.EXTRACTED_INFERRED
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def adapt_shape(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"name": value}
        if not isinstance(value, dict):
            return value
        name = _first_present(value, "name", "measure_name", "action", "measure", "fix", "repair", "label", "title")
        return {
            **value,
            "draft_id": _first_present(value, "draft_id", "id", "measure_id"),
            "name": name,
            "description": _first_present(value, "description", "desc", "measure_desc"),
            "name_status": _adapt_status(
                _first_present(value, "name_status", "measure_name_status"),
                FieldStatus.EXTRACTED_INFERRED,
            ),
            "evidence_refs": _evidence_refs_from(
                value,
                "evidence_refs",
                "chunk_ids",
                "measure_chunk_ids",
                "source_refs",
            ),
        }


class LlmCandidateExtraction(BaseModel):
    extraction_summary: str = ""
    candidate_failure_domain: str | None = None
    symptom_candidates: list[LlmSymptomCandidate] = Field(default_factory=list)
    test_candidates: list[LlmTestDraft] = Field(default_factory=list)
    measure_candidates: list[LlmMeasureDraft] = Field(default_factory=list)
    transition_hints: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def adapt_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        adapted = dict(value)
        symptom_candidates = [
            *_list_items(value.get("symptom_candidates")),
            *_list_items(value.get("symptoms")),
            *_list_items(value.get("failure_symptoms")),
            *_list_items(value.get("FailureSymptom")),  # CamelCase: system prompt uses class names
            *[_candidate_with_level(item, "inner") for item in _list_items(value.get("failure_modes"))],
            *[_candidate_with_level(item, "inner") for item in _list_items(value.get("abnormal_states"))],
            *[_candidate_with_level(item, "root") for item in _list_items(value.get("root_causes"))],
            *[_candidate_with_level(item, "root") for item in _list_items(value.get("root_cause_families"))],
            *[_candidate_with_level(item, "start") for item in _list_items(value.get("phenomena"))],
            *[_candidate_with_level(item, "start") for item in _list_items(value.get("phenomenons"))],
            *[_candidate_with_level(item, "start") for item in _list_items(value.get("start_symptom"))],
            *[_candidate_with_level(item, "start") for item in _list_items(value.get("start_symptoms"))],
            *[_candidate_with_level(item, "start") for item in _list_items(value.get("entry_symptom"))],
            *[_candidate_with_level(item, "start") for item in _list_items(value.get("entry_phenomenon"))],
            *[_candidate_with_level(item, "start") for item in _list_items(value.get("observed_phenomenon"))],
        ]
        test_candidates = [
            *_list_items(value.get("test_candidates")),
            *_list_items(value.get("tests")),
            *_list_items(value.get("ontology_tests")),
            *_list_items(value.get("OntologyTest")),  # CamelCase variant
            *_list_items(value.get("checks")),
            *_list_items(value.get("check_items")),
            *_list_items(value.get("inspection_items")),
            *_list_items(value.get("test_methods")),
        ]
        measure_candidates = [
            *_list_items(value.get("measure_candidates")),
            *_list_items(value.get("measures")),
            *_list_items(value.get("ontology_measures")),
            *_list_items(value.get("OntologyMeasure")),  # CamelCase variant
            *_list_items(value.get("corrective_actions")),
            *_list_items(value.get("containment_actions")),
            *_list_items(value.get("permanent_actions")),
            *_list_items(value.get("repair_actions")),
        ]
        for entity in _list_items(value.get("entities")):
            if isinstance(entity, dict):
                bucket = _candidate_type_bucket(entity)
                if bucket == "test":
                    test_candidates.append(entity)
                elif bucket == "measure":
                    measure_candidates.append(entity)
                else:
                    symptom_candidates.append(entity)
        adapted["symptom_candidates"] = symptom_candidates
        adapted["test_candidates"] = test_candidates
        adapted["measure_candidates"] = measure_candidates
        adapted["transition_hints"] = [
            *_list_items(value.get("transition_hints")),
            *_list_items(value.get("transitions")),
            *_list_items(value.get("diagnosis_chain")),
            *_list_items(value.get("diagnostic_chains")),
            *_list_items(value.get("diagnostic_paths")),
            *_list_items(value.get("causal_chains")),
            *_list_items(value.get("causal_relations")),
            *_list_items(value.get("relations")),
        ]
        adapted["transition_hints"] = [
            item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            for item in _list_items(adapted.get("transition_hints"))
        ]
        adapted["risk_notes"] = _coerce_risk_notes(value.get("risk_notes"))
        return adapted


class LlmTransitionDraft(BaseModel):
    source_name: str
    target_name: str
    test_names: list[str] = Field(default_factory=list)
    condition: str | None = None
    description: str | None = None
    condition_status: FieldStatus = FieldStatus.EXTRACTED_INFERRED
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def adapt_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        source = _first_present(value, "source_name", "source", "source_id", "transition_source")
        target = _first_present(value, "target_name", "target", "target_id", "transition_target")
        test_names = (
            value.get("test_names")
            or value.get("test_ids")
            or value.get("test_id")
            or value.get("tests")
            or value.get("ontology_tests")
            or []
        )
        return {
            **value,
            "source_name": source,
            "target_name": target,
            "test_names": _coerce_str_list(test_names),
            "condition": _first_present(value, "condition", "rule"),
            "description": _first_present(value, "description", "transition_desc", "desc"),
            "condition_status": _adapt_status(
                _first_present(value, "condition_status"),
                FieldStatus.EXTRACTED_INFERRED if _first_present(value, "condition", "rule") else FieldStatus.MISSING,
            ),
            "evidence_refs": _evidence_refs_from(
                value,
                "evidence_refs",
                "chunk_ids",
                "transition_chunk_ids",
                "source_refs",
            ),
        }


class LlmOntologyDraftGraph(BaseModel):
    extraction_summary: str = ""
    candidate_failure_domain: str | None = None
    symptoms: list[LlmSymptomDraft] = Field(default_factory=list)
    tests: list[LlmTestDraft] = Field(default_factory=list)
    measures: list[LlmMeasureDraft] = Field(default_factory=list)
    transitions: list[LlmTransitionDraft] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def adapt_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        adapted = dict(value)
        adapted.setdefault("symptoms", value.get("failure_symptoms") or value.get("FailureSymptom") or [])
        adapted.setdefault("tests", value.get("ontology_tests") or value.get("OntologyTest") or [])
        adapted.setdefault("measures", value.get("ontology_measures") or value.get("OntologyMeasure") or [])
        adapted.setdefault(
            "transitions",
            value.get("symptom_transitions") or value.get("SymptomTransition") or value.get("relations") or [],
        )
        return adapted


class LlmHitlSuggestionOption(BaseModel):
    value: Any = None
    status: FieldStatus = FieldStatus.SUGGESTED_LOW_CONF
    rationale: str = ""
    confidence: float = Field(default=0.3, ge=0.0, le=1.0)
    source_refs: list[str] = Field(default_factory=list)
    rag_refs: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def adapt_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        try:
            status = FieldStatus(str(value.get("status") or FieldStatus.SUGGESTED_LOW_CONF).strip().upper())
        except ValueError:
            status = FieldStatus.SUGGESTED_LOW_CONF
        if status not in {FieldStatus.SUGGESTED_GROUNDED, FieldStatus.SUGGESTED_LOW_CONF}:
            status = FieldStatus.SUGGESTED_LOW_CONF
        return {
            **value,
            "status": status,
            "source_refs": _coerce_str_list(value.get("source_refs") or value.get("evidence_refs")),
            "rag_refs": _coerce_str_list(value.get("rag_refs") or value.get("rag_source_refs")),
            "risk_notes": _coerce_risk_notes(value.get("risk_notes")),
        }


class LlmHitlSuggestionPayload(BaseModel):
    summary: str = ""
    options: list[LlmHitlSuggestionOption] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def adapt_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        options = value.get("options") or value.get("suggestions") or value.get("candidate_options") or []
        return {**value, "options": _list_items(options)}


@dataclass(frozen=True)
class LlmGraphAttempt:
    graph: LlmOntologyDraftGraph | None
    error: str | None = None
    model: str | None = None
    raw_payload: dict[str, Any] | None = None
    raw_text: str | None = None


@dataclass(frozen=True)
class LlmCandidateAttempt:
    candidates: LlmCandidateExtraction | None
    error: str | None = None
    model: str | None = None
    raw_payload: dict[str, Any] | None = None
    raw_text: str | None = None


@dataclass(frozen=True)
class LlmHitlSuggestionAttempt:
    payload: LlmHitlSuggestionPayload | None
    error: str | None = None
    model: str | None = None
    raw_payload: dict[str, Any] | None = None
    raw_text: str | None = None


ProgressCallback = Callable[[dict[str, Any]], None]


class TreeGenerationStageRecorder:
    def __init__(self, callback: ProgressCallback | None = None):
        self.callback = callback
        self.timings: list[dict[str, Any]] = []
        self._active: dict[str, tuple[float, dict[str, Any]]] = {}

    def start(self, stage_id: str, label: str, notes: str | None = None) -> None:
        record = {
            "stage_id": stage_id,
            "label": label,
            "status": "RUNNING",
            "started_at": utc_now_iso(),
            "finished_at": None,
            "duration_ms": None,
            "notes": notes,
        }
        self._active[stage_id] = (time.perf_counter(), record)
        self._emit(record)

    def finish(self, stage_id: str, status: str = "COMPLETED", notes: str | None = None) -> None:
        started = self._active.pop(stage_id, None)
        if not started:
            return
        start_time, record = started
        record = dict(record)
        record["status"] = status
        record["finished_at"] = utc_now_iso()
        record["duration_ms"] = int((time.perf_counter() - start_time) * 1000)
        if notes:
            record["notes"] = notes
        self.timings.append(record)
        self._emit(record)

    def fail_active(self, notes: str) -> None:
        for stage_id in list(self._active):
            self.finish(stage_id, status="FAILED", notes=notes)

    def _emit(self, record: dict[str, Any]) -> None:
        if self.callback:
            self.callback(dict(record))


class BatchTreeGenerationService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_dir = settings.tree_generation_dir
        self.jobs_dir = self.base_dir / "jobs"
        self.uploads_dir = self.base_dir / "uploads"
        self.artifacts_dir = self.base_dir / "artifacts"
        self.proposals_dir = settings.tree_proposals_dir
        self.proposal_store = TreeProposalStore(self.proposals_dir)
        for path in [self.jobs_dir, self.uploads_dir, self.artifacts_dir, self.proposals_dir]:
            path.mkdir(parents=True, exist_ok=True)

    def copy_input_documents(self, source_paths: list[str | Path], job_id: str | None = None) -> list[Path]:
        target_dir = self.uploads_dir / (job_id or "manual")
        target_dir.mkdir(parents=True, exist_ok=True)
        copied: list[Path] = []
        for source in source_paths:
            path = Path(source)
            if not path.exists() or not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_TREE_SOURCE_SUFFIXES:
                continue
            target = _unique_path(target_dir / path.name)
            copy2(path, target)
            copied.append(target)
        return copied

    def run_batch_job(
        self,
        *,
        title: str,
        source_paths: list[str | Path],
        description: str | None = None,
        use_llm: bool = True,
        progress_callback: ProgressCallback | None = None,
    ) -> TreeGenerationJob:
        recorder = TreeGenerationStageRecorder(progress_callback)
        job = TreeGenerationJob(title=title, description=description)
        job_dir = self.artifacts_dir / job.job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        recorder.start("copy_inputs", "复制/过滤输入资料")
        copied_paths = self.copy_input_documents(source_paths, job.job_id)
        recorder.finish("copy_inputs", notes=f"可用资料 {len(copied_paths)} 个")
        job.status = TreeGenerationJobStatus.RUNNING
        job.input_documents = [_document_record(path) for path in copied_paths]
        self.save_job(job)
        if not copied_paths:
            job.status = TreeGenerationJobStatus.FAILED
            job.error = "没有可用输入资料；仅支持 PDF/MD/TXT/CSV。"
            job.updated_at = utc_now_iso()
            recorder.start("failed", "生成失败")
            recorder.finish("failed", status="FAILED", notes=job.error)
            self.save_job(job)
            return job
        try:
            recorder.start("read_chunks", "读取资料并切分 chunk")
            chunks = [
                chunk
                for path in copied_paths
                for chunk in _read_source_chunks(path)
                if chunk["text"]
            ]
            for document in job.input_documents:
                document.chunk_ids = [
                    chunk["chunk_id"] for chunk in chunks if chunk["source_path"] == document.source_path
                ]
            recorder.finish("read_chunks", notes=f"生成 chunk {len(chunks)} 个")
            artifact = self._extract_artifact(job.job_id, title, description, chunks, use_llm, recorder)
            recorder.start("proposal", "生成 TreeProposal")
            payload = _artifact_to_payload(artifact)
            proposal = _artifact_to_tree_proposal(job, artifact, payload)
            recorder.finish("proposal")
            job.artifact = artifact
            job.proposal = proposal
            job.status = TreeGenerationJobStatus.COMPLETED
            job.updated_at = utc_now_iso()
            recorder.start("persist", "写入 artifact/proposal/job 文件")
            self._write_artifact_files(job)
            self.append_proposal(proposal)
            self.proposal_store.save_artifact_snapshot(proposal, artifact=artifact)
            recorder.finish("persist")
            if job.artifact:
                job.artifact.stage_timings = recorder.timings
                self._write_artifact_files(job)
            self.save_job(job)
            return job
        except Exception as exc:
            recorder.fail_active(str(exc))
            job.status = TreeGenerationJobStatus.FAILED
            job.error = str(exc)
            job.updated_at = utc_now_iso()
            self.save_job(job)
            return job

    def _extract_artifact(
        self,
        job_id: str,
        title: str,
        description: str | None,
        chunks: list[dict[str, str]],
        use_llm: bool,
        recorder: TreeGenerationStageRecorder | None = None,
    ) -> TreeGenerationArtifact:
        recorder = recorder or TreeGenerationStageRecorder()
        llm_error: str | None = None
        llm_model: str | None = None
        llm_counts: dict[str, int] = {}
        llm_preview: dict[str, Any] = {}
        llm_raw_payload: dict[str, Any] = {}
        llm_raw_text: str | None = None
        if use_llm:
            recorder.start("pass1", "PASS_1 候选实体抽取")
            candidate_attempt = _llm_extract_candidates(self.settings, title, description, chunks)
            recorder.finish(
                "pass1",
                status="FAILED" if candidate_attempt.error else "COMPLETED",
                notes=candidate_attempt.error,
            )
            llm_error = candidate_attempt.error
            llm_model = candidate_attempt.model
            llm_raw_payload = candidate_attempt.raw_payload or {}
            llm_raw_text = candidate_attempt.raw_text
            if candidate_attempt.candidates:
                llm_counts = _candidate_counts(candidate_attempt.candidates)
                llm_preview = _candidate_preview(candidate_attempt.candidates)
            elif candidate_attempt.raw_payload:
                llm_preview = llm_raw_payload
            if candidate_attempt.candidates and not _candidate_extraction_is_empty(candidate_attempt.candidates):
                recorder.start("pass2", "PASS_2 实体分级 / start 合并")
                level_attempt = _llm_level_entities(
                    self.settings,
                    title,
                    description,
                    chunks,
                    candidate_attempt.candidates,
                )
                recorder.finish(
                    "pass2",
                    status="FAILED" if level_attempt.error else "COMPLETED",
                    notes=level_attempt.error,
                )
                if level_attempt.error:
                    llm_error = level_attempt.error
                if level_attempt.model:
                    llm_model = level_attempt.model
                if not level_attempt.graph or _leveled_graph_is_empty(level_attempt.graph):
                    candidate_artifact = _artifact_from_candidates(
                        job_id,
                        candidate_attempt.candidates,
                        chunks,
                    )
                    candidate_artifact.extraction_quality = TreeGenerationQuality.NEEDS_REPAIR_LLM_DRAFT
                    candidate_artifact.extraction_passes = [
                        OntologyExtractionPass(
                            pass_id="PASS_1",
                            pass_type="LLM_CANDIDATE_ENTITY_EXTRACTION",
                            llm_used=True,
                            summary=_llm_pass_summary(
                                candidate_attempt.candidates.extraction_summary
                                or "LLM 完成候选异常、检查项、处置措施抽取。",
                                candidate_attempt.model,
                            ),
                            output_counts=_candidate_counts(candidate_attempt.candidates),
                            output_preview=_candidate_preview(candidate_attempt.candidates),
                            raw_output=llm_raw_payload,
                            raw_text=llm_raw_text,
                        ),
                        OntologyExtractionPass(
                            pass_id="PASS_2_FAILED",
                            pass_type="LLM_LEVELING_FAILED",
                            llm_used=True,
                            summary=_llm_pass_summary(
                                f"LLM 分级未返回可用本体实体，系统根据候选实体确定性组装待修复草案："
                                f"{llm_error or '未知原因'}",
                                llm_model,
                            ),
                            output_preview=_safe_payload_preview(level_attempt.raw_payload),
                            raw_output=level_attempt.raw_payload or {},
                            raw_text=level_attempt.raw_text,
                        ),
                        OntologyExtractionPass(
                            pass_id="PASS_2_FALLBACK",
                            pass_type="DETERMINISTIC_CANDIDATE_ASSEMBLY",
                            llm_used=False,
                            summary="LLM 分级返回空实体，系统根据候选实体确定性组装待审核草案。",
                            output_counts=_artifact_counts(candidate_artifact),
                            output_preview=_artifact_preview(candidate_artifact),
                            raw_output=level_attempt.raw_payload or {},
                            raw_text=level_attempt.raw_text,
                        ),
                    ]
                    candidate_artifact.validation_report = validate_tree_generation_artifact(candidate_artifact)
                    candidate_artifact.extraction_passes[-1].issues_after = (
                        candidate_artifact.validation_report.issues
                    )
                    candidate_artifact.rebuilt_fault_tree = rebuild_fault_tree_preview(candidate_artifact)
                    candidate_artifact.stage_timings = recorder.timings
                    return candidate_artifact
                recorder.start("pass3", "PASS_3 transition 绑定")
                graph_attempt = _llm_bind_transitions(
                    self.settings,
                    title,
                    description,
                    chunks,
                    level_attempt.graph,
                )
                recorder.finish(
                    "pass3",
                    status="FAILED" if graph_attempt.error else "COMPLETED",
                    notes=graph_attempt.error,
                )
                if graph_attempt.error:
                    llm_error = graph_attempt.error
                if graph_attempt.model:
                    llm_model = graph_attempt.model
                if graph_attempt.graph:
                    artifact = _llm_graph_to_artifact(job_id, graph_attempt.graph, chunks)
                    artifact.extraction_quality = TreeGenerationQuality.HIGH_CONF_LLM_DRAFT
                    artifact.extraction_passes.extend(
                        [
                            OntologyExtractionPass(
                                pass_id="PASS_1",
                                pass_type="LLM_CANDIDATE_ENTITY_EXTRACTION",
                                llm_used=True,
                                summary=_llm_pass_summary(
                                    candidate_attempt.candidates.extraction_summary
                                    or "LLM 完成候选异常、检查项、处置措施抽取。",
                                    candidate_attempt.model,
                                ),
                                output_counts=_candidate_counts(candidate_attempt.candidates),
                                output_preview=_candidate_preview(candidate_attempt.candidates),
                                raw_output=llm_raw_payload,
                                raw_text=llm_raw_text,
                            ),
                            OntologyExtractionPass(
                                pass_id="PASS_2",
                                pass_type="LLM_ENTITY_LEVELING",
                                llm_used=True,
                                summary=_llm_pass_summary(
                                    level_attempt.graph.extraction_summary
                                    or "LLM 完成实体分类、start 合并和 start/inner/root 分级。",
                                    llm_model,
                                ),
                                output_counts=_graph_counts(level_attempt.graph),
                                output_preview=_graph_preview(level_attempt.graph),
                                raw_output=level_attempt.raw_payload or {},
                                raw_text=level_attempt.raw_text,
                            ),
                            OntologyExtractionPass(
                                pass_id="PASS_3",
                                pass_type="LLM_TRANSITION_BINDING",
                                llm_used=True,
                                summary=_llm_pass_summary(
                                    graph_attempt.graph.extraction_summary
                                    or "LLM 基于已分级实体生成 transition 并绑定 test。",
                                    llm_model,
                                ),
                                output_counts=_graph_counts(graph_attempt.graph),
                                output_preview=_graph_preview(graph_attempt.graph),
                                raw_output=graph_attempt.raw_payload or {},
                                raw_text=graph_attempt.raw_text,
                            ),
                        ]
                    )
                    if _graph_is_empty(graph_attempt.graph):
                        llm_error = "LLM 返回空本体草案：symptoms/tests/transitions 为空，不能视为有效抽取。"
                        artifact.extraction_quality = TreeGenerationQuality.NEEDS_REPAIR_LLM_DRAFT
                        artifact.extraction_passes[-1].pass_type = "LLM_EMPTY_GRAPH"
                        artifact.extraction_passes[-1].summary = _llm_pass_summary(llm_error, llm_model)
                    recorder.start("validate", "结构校验")
                    artifact.validation_report = validate_tree_generation_artifact(artifact)
                    recorder.finish("validate", notes=f"issues={len(artifact.validation_report.issues)}")
                    if artifact.validation_report.issues:
                        recorder.start("pass4", "PASS_4 校验修复")
                        repair_attempt = _llm_repair_graph(
                            self.settings,
                            title,
                            description,
                            chunks,
                            graph_attempt.graph,
                            artifact,
                        )
                        recorder.finish(
                            "pass4",
                            status="FAILED" if repair_attempt.error else "COMPLETED",
                            notes=repair_attempt.error,
                        )
                        if repair_attempt.error:
                            llm_error = repair_attempt.error
                        if repair_attempt.model:
                            llm_model = repair_attempt.model
                        if repair_attempt.graph and not _graph_is_empty(repair_attempt.graph):
                            repaired_artifact = _llm_graph_to_artifact(job_id, repair_attempt.graph, chunks)
                            restored = preserve_hitl_candidates_after_repair(artifact, repaired_artifact)
                            repaired_artifact.extraction_passes = [
                                *artifact.extraction_passes,
                                OntologyExtractionPass(
                                    pass_id="PASS_4",
                                    pass_type="LLM_VALIDATE_AND_REPAIR",
                                    llm_used=True,
                                    summary=_llm_pass_summary(
                                        repair_attempt.graph.extraction_summary or "LLM 根据校验问题修复本体草案。",
                                        llm_model,
                                    ),
                                    output_counts=_graph_counts(repair_attempt.graph),
                                    output_preview=_graph_preview(repair_attempt.graph),
                                    raw_output=repair_attempt.raw_payload or {},
                                    raw_text=repair_attempt.raw_text,
                                    issues_before=artifact.validation_report.issues,
                                ),
                            ]
                            if restored:
                                repaired_artifact.extraction_passes[-1].summary += (
                                    f" 系统已恢复 {restored} 个被修复轮误删的待确认项。"
                                )
                            repaired_artifact.validation_report = validate_tree_generation_artifact(repaired_artifact)
                            repaired_artifact.extraction_passes[-1].issues_after = (
                                repaired_artifact.validation_report.issues
                            )
                            repaired_artifact.extraction_quality = (
                                TreeGenerationQuality.HIGH_CONF_LLM_DRAFT
                                if repaired_artifact.validation_report.is_valid
                                else TreeGenerationQuality.NEEDS_REPAIR_LLM_DRAFT
                            )
                            recorder.start("rebuild", "确定性 BFS 重建预览")
                            repaired_artifact.rebuilt_fault_tree = rebuild_fault_tree_preview(repaired_artifact)
                            recorder.finish("rebuild")
                            repaired_artifact.stage_timings = recorder.timings
                            return repaired_artifact
                        artifact.extraction_quality = TreeGenerationQuality.NEEDS_REPAIR_LLM_DRAFT
                        artifact.extraction_passes.append(
                            OntologyExtractionPass(
                                pass_id="PASS_4_FAILED",
                                pass_type="LLM_VALIDATE_AND_REPAIR_FAILED",
                                llm_used=True,
                                summary=_llm_pass_summary(
                                    f"LLM 修复未返回可校验草案：{llm_error or '未知原因'}",
                                    llm_model,
                                ),
                                output_preview=_safe_payload_preview(repair_attempt.raw_payload),
                                raw_output=repair_attempt.raw_payload or {},
                                raw_text=repair_attempt.raw_text,
                                issues_before=artifact.validation_report.issues,
                            )
                        )
                    if _graph_is_empty(graph_attempt.graph):
                        candidate_artifact = _artifact_from_candidates(
                            job_id,
                            candidate_attempt.candidates,
                            chunks,
                        )
                        candidate_artifact.extraction_quality = TreeGenerationQuality.NEEDS_REPAIR_LLM_DRAFT
                        candidate_artifact.extraction_passes = [
                            *artifact.extraction_passes,
                            OntologyExtractionPass(
                                pass_id="PASS_2_FALLBACK",
                                pass_type="DETERMINISTIC_CANDIDATE_ASSEMBLY",
                                llm_used=False,
                                summary="LLM 建图/修复返回空图，系统根据候选实体确定性组装待审核草案。",
                                output_counts=_artifact_counts(candidate_artifact),
                                output_preview=_artifact_preview(candidate_artifact),
                                raw_output=graph_attempt.raw_payload or {},
                                raw_text=graph_attempt.raw_text,
                                issues_before=artifact.validation_report.issues,
                            ),
                        ]
                        candidate_artifact.validation_report = validate_tree_generation_artifact(candidate_artifact)
                        candidate_artifact.extraction_passes[-1].issues_after = (
                            candidate_artifact.validation_report.issues
                        )
                        candidate_artifact.rebuilt_fault_tree = rebuild_fault_tree_preview(candidate_artifact)
                        candidate_artifact.stage_timings = recorder.timings
                        return candidate_artifact
                    recorder.start("rebuild", "确定性 BFS 重建预览")
                    artifact.rebuilt_fault_tree = rebuild_fault_tree_preview(artifact)
                    recorder.finish("rebuild")
                    artifact.stage_timings = recorder.timings
                    return artifact
                candidate_artifact = _artifact_from_candidates(
                    job_id,
                    candidate_attempt.candidates,
                    chunks,
                )
                candidate_artifact.extraction_quality = TreeGenerationQuality.NEEDS_REPAIR_LLM_DRAFT
                candidate_artifact.extraction_passes.extend(
                    [
                        OntologyExtractionPass(
                            pass_id="PASS_1",
                            pass_type="LLM_CANDIDATE_ENTITY_EXTRACTION",
                            llm_used=True,
                            summary=_llm_pass_summary(
                                candidate_attempt.candidates.extraction_summary
                                or "LLM 完成候选异常、检查项、处置措施抽取。",
                                candidate_attempt.model,
                            ),
                            output_counts=_candidate_counts(candidate_attempt.candidates),
                            output_preview=_candidate_preview(candidate_attempt.candidates),
                            raw_output=llm_raw_payload,
                            raw_text=llm_raw_text,
                        ),
                        OntologyExtractionPass(
                            pass_id="PASS_2_FALLBACK",
                            pass_type="DETERMINISTIC_CANDIDATE_ASSEMBLY",
                            llm_used=False,
                            summary=_llm_pass_summary(
                                f"LLM 建图未返回可用本体图，系统根据候选实体确定性组装待修复草案："
                                f"{llm_error or '未知原因'}",
                                llm_model,
                            ),
                            output_counts=_artifact_counts(candidate_artifact),
                            output_preview=_artifact_preview(candidate_artifact),
                            raw_output=graph_attempt.raw_payload or {},
                            raw_text=graph_attempt.raw_text,
                        ),
                    ]
                )
                candidate_artifact.validation_report = validate_tree_generation_artifact(candidate_artifact)
                candidate_artifact.extraction_passes[-1].issues_after = candidate_artifact.validation_report.issues
                candidate_artifact.rebuilt_fault_tree = rebuild_fault_tree_preview(candidate_artifact)
                candidate_artifact.stage_timings = recorder.timings
                return candidate_artifact
            elif candidate_attempt.candidates:
                llm_error = candidate_attempt.error or "LLM 候选抽取为空：未抽出任何候选 FailureSymptom。"
                llm_model = candidate_attempt.model
        recorder.start("fallback", "规则低置信兜底抽取")
        rule_payload = _rule_extract(chunks)
        recorder.finish("fallback")
        artifact = _payload_to_artifact(job_id, rule_payload, chunks)
        artifact.extraction_quality = TreeGenerationQuality.LOW_CONF_DEBUG_DRAFT
        artifact.extraction_passes.append(
            OntologyExtractionPass(
                pass_id="FALLBACK_1",
                pass_type="RULE_LOW_CONF_DEBUG_FALLBACK",
                llm_used=False,
                summary=_fallback_summary(use_llm, llm_error, llm_model),
                output_counts=llm_counts,
                output_preview=llm_preview,
                raw_output=llm_raw_payload,
                raw_text=llm_raw_text,
            )
        )
        recorder.start("validate", "结构校验")
        artifact.validation_report = validate_tree_generation_artifact(artifact)
        recorder.finish("validate", notes=f"issues={len(artifact.validation_report.issues)}")
        recorder.start("rebuild", "确定性 BFS 重建预览")
        artifact.rebuilt_fault_tree = rebuild_fault_tree_preview(artifact)
        recorder.finish("rebuild")
        artifact.stage_timings = recorder.timings
        return artifact

    def save_job(self, job: TreeGenerationJob) -> Path:
        path = self.jobs_dir / f"{job.job_id}.json"
        path.write_text(job.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load_job(self, job_id: str) -> TreeGenerationJob | None:
        path = self.jobs_dir / f"{job_id}.json"
        if not path.exists():
            return None
        return TreeGenerationJob.model_validate_json(path.read_text(encoding="utf-8"))

    def load_jobs(self) -> list[TreeGenerationJob]:
        jobs: list[TreeGenerationJob] = []
        for path in self.jobs_dir.glob("TGJ-*.json"):
            jobs.append(TreeGenerationJob.model_validate_json(path.read_text(encoding="utf-8")))
        jobs.sort(key=lambda job: (job.updated_at or job.created_at or "", job.job_id), reverse=True)
        return jobs

    def generate_hitl_suggestions(
        self,
        job_id: str,
        *,
        rag: Any | None = None,
        use_llm: bool = True,
        max_items: int = 20,
    ) -> TreeGenerationJob | None:
        job = self.load_job(job_id)
        if not job or not job.artifact:
            return job
        chunks = _read_job_source_chunks(job)
        pending_items = generation_hitl_items(job.artifact)[:max_items]
        existing_by_key = {
            _hitl_item_key(
                {
                    "object_type": suggestion.object_type,
                    "object_id": suggestion.object_id,
                    "field": suggestion.field,
                }
            ): suggestion
            for suggestion in job.artifact.hitl_suggestions
        }
        suggestions: list[TreeGenerationHitlSuggestion] = []
        for item in pending_items:
            key = _hitl_item_key(item)
            if key in existing_by_key:
                suggestions.append(existing_by_key[key])
                continue
            source_context = _hitl_source_context(item, chunks)
            rag_evidence = _hitl_rag_evidence(rag, _hitl_query(job.artifact, item), top_k=4)
            attempt = (
                _llm_suggest_hitl_options(self.settings, job.artifact, item, source_context, rag_evidence)
                if use_llm
                else LlmHitlSuggestionAttempt(None, error="本次未启用 LLM HITL 建议。")
            )
            suggestions.append(_hitl_suggestion_from_attempt(item, attempt, rag_evidence))
        job.artifact.hitl_suggestions = suggestions
        job.updated_at = utc_now_iso()
        self._write_artifact_files(job)
        self.save_job(job)
        return job

    def apply_hitl_decision(
        self,
        job_id: str,
        decision: TreeGenerationHitlDecision,
    ) -> TreeGenerationJob | None:
        job = self.load_job(job_id)
        if not job or not job.artifact:
            return job
        _apply_hitl_decision_to_artifact(job.artifact, decision)
        job.artifact.hitl_decisions.append(decision)
        _refresh_artifact_after_hitl(job)
        if job.proposal:
            self.proposal_store.save_proposal(job.proposal)
            self.proposal_store.save_artifact_snapshot(job.proposal, artifact=job.artifact)
            self.proposal_store.append_review_log(_hitl_decision_review_log(job, decision))
        job.updated_at = utc_now_iso()
        self._write_artifact_files(job)
        self.save_job(job)
        return job

    def append_proposal(self, proposal: TreeProposal) -> None:
        self.proposal_store.append_proposal(proposal)

    def load_proposals(self) -> list[TreeProposal]:
        return self.proposal_store.load_proposals()

    def _write_artifact_files(self, job: TreeGenerationJob) -> None:
        if not job.artifact or not job.proposal:
            return
        job_dir = self.artifacts_dir / job.job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "artifact.json").write_text(
            job.artifact.model_dump_json(ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (job_dir / "proposal.json").write_text(
            job.proposal.model_dump_json(ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (job_dir / "rebuilt_tree_preview.json").write_text(
            json.dumps(job.artifact.rebuilt_fault_tree, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def validate_tree_generation_artifact(artifact: TreeGenerationArtifact) -> TreeGenerationValidationReport:
    symptoms = artifact.symptoms
    tests = artifact.tests
    transitions = artifact.transitions
    start_count = sum(1 for item in symptoms if item.level == "start")
    root_count = sum(1 for item in symptoms if item.level == "root")
    errors: list[str] = []
    warnings: list[str] = []
    issues: list[OntologyValidationIssue] = []
    symptom_ids = {item.entity_id for item in symptoms}
    test_ids = {item.entity_id for item in tests}
    if start_count != 1:
        _add_issue(
            issues,
            "ERROR",
            "START_COUNT",
            "必须有且仅有一个 start FailureSymptom。",
            [],
            "合并语义重叠 start，保留最大公约数入口异常；若没有 start，则从客户/现场可观察现象中创建。",
        )
    if root_count < 1:
        _add_issue(
            issues,
            "ERROR",
            "ROOT_MISSING",
            "至少需要一个 root FailureSymptom。",
            [],
            "从资料中的最深可处置异常创建 root；资料不足时创建低置信 root 并明确 MISSING/LOW_CONF 状态。",
        )
    incoming: dict[str, list[str]] = {}
    outgoing: dict[str, list[str]] = {}
    source_target_pairs: set[tuple[str, str]] = set()
    for transition in transitions:
        pair = (transition.source_id, transition.target_id)
        if pair in source_target_pairs:
            _add_issue(
                issues,
                "ERROR",
                "DUPLICATE_TRANSITION",
                f"重复 transition source,target：{transition.source_id}->{transition.target_id}",
                [transition.transition_id, transition.source_id, transition.target_id],
                "合并同一 source,target 的 transition，并合并 test_id 与证据引用。",
            )
        source_target_pairs.add(pair)
        if transition.source_id not in symptom_ids:
            _add_issue(
                issues,
                "ERROR",
                "TRANSITION_SOURCE_MISSING",
                f"Transition {transition.transition_id} source 不存在：{transition.source_id}",
                [transition.transition_id, transition.source_id],
                "补建 source FailureSymptom，或将 transition.source 改为已有上级异常状态。",
            )
        if transition.target_id not in symptom_ids:
            _add_issue(
                issues,
                "ERROR",
                "TRANSITION_TARGET_MISSING",
                f"Transition {transition.transition_id} target 不存在：{transition.target_id}",
                [transition.transition_id, transition.target_id],
                "补建 target FailureSymptom，或将 transition.target 改为已有下级异常状态。",
            )
        if not transition.test_ids:
            _add_issue(
                issues,
                "ERROR",
                "TRANSITION_TEST_MISSING",
                f"Transition {transition.transition_id} 缺少 test_id。",
                [transition.transition_id],
                "为该 transition 绑定至少一个 OntologyTest；若原文无检查项，创建 MISSING 占位 test。",
            )
        for test_id in transition.test_ids:
            if test_id not in test_ids:
                _add_issue(
                    issues,
                    "ERROR",
                    "TRANSITION_TEST_REF_MISSING",
                    f"Transition {transition.transition_id} 引用不存在的 test：{test_id}",
                    [transition.transition_id, test_id],
                    "补建对应 OntologyTest，或替换为已存在 test_id。",
                )
        outgoing.setdefault(transition.source_id, []).append(transition.target_id)
        incoming.setdefault(transition.target_id, []).append(transition.source_id)
    for test in tests:
        if not test.name and test.name_status != FieldStatus.MISSING:
            _add_issue(
                issues,
                "WARNING",
                "EMPTY_FIELD_STATUS_MISMATCH",
                f"Test {test.entity_id} 名称为空但 name_status 不是 MISSING。",
                [test.entity_id],
                "将 test_name_status 改为 MISSING，或补充证据支持的 test_name。",
            )
    for symptom in symptoms:
        if not symptom.name and symptom.name_status != FieldStatus.MISSING:
            _add_issue(
                issues,
                "WARNING",
                "EMPTY_FIELD_STATUS_MISMATCH",
                f"Symptom {symptom.entity_id} 名称为空但 name_status 不是 MISSING。",
                [symptom.entity_id],
                "将 symptom_name_status 改为 MISSING，或补充证据支持的 symptom_name。",
            )
        if symptom.level == "start" and incoming.get(symptom.entity_id):
            _add_issue(
                issues,
                "ERROR",
                "START_HAS_INCOMING",
                f"Start {symptom.entity_id} 不应有入边。",
                [symptom.entity_id],
                "重新分级该节点，或移除指向 start 的 transition。",
            )
        if symptom.level == "root" and outgoing.get(symptom.entity_id):
            _add_issue(
                issues,
                "ERROR",
                "ROOT_HAS_OUTGOING",
                f"Root {symptom.entity_id} 不应有出边。",
                [symptom.entity_id],
                "若原文存在更深原因，将该节点改为 inner，并把最深可处置异常作为 root。",
            )
    cycle = _find_cycle(outgoing)
    if cycle:
        _add_issue(
            issues,
            "ERROR",
            "GRAPH_HAS_CYCLE",
            f"SymptomTransition 图存在环：{' -> '.join(cycle)}",
            cycle,
            "诊断转移必须从上级异常指向更具体异常；删除反向边或重新分级节点。",
        )
    roots = {item.entity_id for item in symptoms if item.level == "root"}
    for symptom in symptoms:
        if symptom.level == "inner" and not _can_reach_any(symptom.entity_id, roots, outgoing):
            _add_issue(
                issues,
                "ERROR",
                "INNER_CANNOT_REACH_ROOT",
                f"Inner {symptom.entity_id} 没有路径到 root。",
                [symptom.entity_id],
                "补建下游 root 和 transition；若它实际是终止原因，将其改为 root。",
            )
    start_ids = {item.entity_id for item in symptoms if item.level == "start"}
    reverse = _reverse_graph(outgoing)
    for symptom in symptoms:
        if symptom.level in {"inner", "root"} and not _can_reach_any(symptom.entity_id, start_ids, reverse):
            _add_issue(
                issues,
                "ERROR",
                "NODE_NOT_REACHABLE_FROM_START",
                f"{symptom.level} {symptom.entity_id} 无法反向回溯到 start。",
                [symptom.entity_id],
                "补充从 start 到该节点的诊断 transition，或删除孤立节点。",
            )
    rebuilt = rebuild_fault_tree_preview(artifact).get("symptom_ids", [])
    if root_count and not any(item.entity_id in rebuilt for item in symptoms if item.level == "root"):
        _add_issue(
            issues,
            "WARNING",
            "ROOT_NOT_IN_REBUILD",
            "当前 root 节点没有出现在确定性重建预览中。",
            [item.entity_id for item in symptoms if item.level == "root"],
            "检查 start 到 root 的 transition 是否完整。",
        )
    errors = [issue.message for issue in issues if issue.severity == "ERROR"]
    warnings = [issue.message for issue in issues if issue.severity == "WARNING"]
    return TreeGenerationValidationReport(
        is_valid=not errors,
        errors=errors,
        warnings=warnings,
        issues=issues,
        start_symptom_count=start_count,
        root_symptom_count=root_count,
        test_count=len(tests),
        transition_count=len(transitions),
        rebuilt_symptom_ids=rebuilt,
        ontology_constraints=_ontology_constraints(),
    )


def generation_hitl_items(artifact: TreeGenerationArtifact) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entity in [*artifact.symptoms, *artifact.tests, *artifact.measures]:
        entity_kind = entity.entity_type.value
        _append_generation_hitl_item(
            items,
            object_type=entity_kind,
            object_id=entity.entity_id,
            field="name",
            status=entity.name_status,
            current_value=entity.name,
            source_refs=entity.source_refs,
        )
        _append_generation_hitl_item(
            items,
            object_type=entity_kind,
            object_id=entity.entity_id,
            field="description",
            status=entity.description_status,
            current_value=entity.description,
            source_refs=entity.source_refs,
        )
    for transition in artifact.transitions:
        _append_generation_hitl_item(
            items,
            object_type="SymptomTransition",
            object_id=transition.transition_id,
            field="condition",
            status=transition.condition_status,
            current_value=transition.condition,
            source_refs=transition.source_refs,
        )
        _append_generation_hitl_item(
            items,
            object_type="SymptomTransition",
            object_id=transition.transition_id,
            field="description",
            status=transition.description_status,
            current_value=transition.description,
            source_refs=transition.source_refs,
        )
        if not transition.test_ids:
            items.append(
                {
                    "object_type": "SymptomTransition",
                    "object_id": transition.transition_id,
                    "field": "test_ids",
                    "status": FieldStatus.MISSING.value,
                    "current_value": [],
                    "source_refs": transition.source_refs,
                    "reason": "transition 缺少 test 绑定，必须补充 OntologyTest。",
                }
            )
    return items


def _read_job_source_chunks(job: TreeGenerationJob) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    for document in job.input_documents:
        path = Path(document.source_path)
        if path.exists():
            chunks.extend(_read_source_chunks(path))
    return chunks


def _hitl_item_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (str(item.get("object_type")), str(item.get("object_id")), str(item.get("field")))


def _hitl_query(artifact: TreeGenerationArtifact, item: dict[str, Any]) -> str:
    start_names = [symptom.name or "" for symptom in artifact.symptoms if symptom.level == "start"]
    root_names = [symptom.name or "" for symptom in artifact.symptoms if symptom.level == "root"]
    current_value = item.get("current_value")
    parts = [
        " ".join(start_names[:2]),
        " ".join(root_names[:4]),
        str(current_value or ""),
        str(item.get("object_type") or ""),
        str(item.get("field") or ""),
    ]
    return " ".join(part for part in parts if part).strip()


def _hitl_source_context(item: dict[str, Any], chunks: list[dict[str, str]], limit: int = 1800) -> str:
    source_refs = set(_coerce_str_list(item.get("source_refs")))
    selected = [chunk for chunk in chunks if not source_refs or chunk.get("chunk_id") in source_refs]
    if not selected:
        selected = chunks[:3]
    parts = [
        f"[{chunk.get('chunk_id')}] {chunk.get('text', '')[:600]}"
        for chunk in selected[:4]
        if chunk.get("text")
    ]
    return _compact("\n".join(parts), limit)


def _hitl_rag_evidence(rag: Any | None, query: str, top_k: int = 4) -> list[EvidenceItem]:
    if rag is None or not query:
        return []
    try:
        return rag.search(query, top_k=top_k)
    except Exception:
        return []


def _llm_suggest_hitl_options(
    settings: Settings,
    artifact: TreeGenerationArtifact,
    item: dict[str, Any],
    source_context: str,
    rag_evidence: list[EvidenceItem],
) -> LlmHitlSuggestionAttempt:
    provider = LlmProvider(settings)
    payload = provider.json_completion(
        system_prompt=HITL_SUGGESTION_SYSTEM_PROMPT,
        user_prompt=_hitl_suggestion_prompt(artifact, item, source_context, rag_evidence),
        response_model=LlmHitlSuggestionPayload,
        complexity="pro",
        max_tokens=3000,
    )
    return LlmHitlSuggestionAttempt(
        payload=payload,
        error=provider.last_error,
        model=provider.last_model,
        raw_payload=provider.last_payload,
        raw_text=provider.last_raw_content,
    )


def _hitl_suggestion_prompt(
    artifact: TreeGenerationArtifact,
    item: dict[str, Any],
    source_context: str,
    rag_evidence: list[EvidenceItem],
) -> str:
    rag_context = "\n".join(
        f"[RAG:{evidence.source_id}] {evidence.claim} refs={','.join(evidence.source_refs)}"
        for evidence in rag_evidence[:4]
    )
    tree_context = {
        "symptoms": [
            {
                "id": symptom.entity_id,
                "name": symptom.name,
                "level": symptom.level,
                "name_status": symptom.name_status,
                "description_status": symptom.description_status,
            }
            for symptom in artifact.symptoms
        ],
        "tests": [
            {
                "id": test.entity_id,
                "name": test.name,
                "target": test.description,
                "name_status": test.name_status,
            }
            for test in artifact.tests
        ],
        "transitions": [
            {
                "id": transition.transition_id,
                "source_id": transition.source_id,
                "target_id": transition.target_id,
                "test_ids": transition.test_ids,
                "condition": transition.condition,
            }
            for transition in artifact.transitions
        ],
    }
    return (
        "请为树生成阶段 HITL 补全/确认一个字段生成专家建议选项。\n"
        "必须先锚定原文语境，再参考 RAG 证据，最后才使用领域/工艺/维修专家知识补足表达。\n"
        "如果原文和 RAG 都不足以支持具体值，options 必须为空，并在 summary 说明需要补充资料；"
        "不要为了完整而编造当前场景没有支持的知识。\n"
        "选项状态只能是 SUGGESTED_GROUNDED 或 SUGGESTED_LOW_CONF。\n"
        "输出 JSON：{summary, options:[{value,status,rationale,confidence,source_refs,rag_refs,risk_notes}]}。\n"
        f"HITL 字段：{json.dumps(item, ensure_ascii=False)}\n"
        f"当前草稿树：{json.dumps(tree_context, ensure_ascii=False)}\n"
        f"原文片段：\n{source_context or '无直接原文片段'}\n"
        f"RAG 证据：\n{rag_context or '无 RAG 命中'}"
    )


def _hitl_suggestion_from_attempt(
    item: dict[str, Any],
    attempt: LlmHitlSuggestionAttempt,
    rag_evidence: list[EvidenceItem],
) -> TreeGenerationHitlSuggestion:
    options: list[TreeGenerationHitlSuggestionOption] = []
    if attempt.payload:
        for option in attempt.payload.options[:4]:
            options.append(
                TreeGenerationHitlSuggestionOption(
                    value=option.value,
                    status=option.status,
                    rationale=option.rationale or "LLM 基于原文/RAG/专家知识生成的候选选项。",
                    confidence=option.confidence,
                    source_refs=option.source_refs or _coerce_str_list(item.get("source_refs")),
                    rag_refs=option.rag_refs,
                    risk_notes=option.risk_notes,
                )
            )
    if not options and item.get("current_value") not in (None, "", []):
        options.append(
            TreeGenerationHitlSuggestionOption(
                value=item.get("current_value"),
                status=FieldStatus.SUGGESTED_LOW_CONF,
                rationale="LLM 不可用或证据不足时保留当前弱推断值，需人工确认。",
                confidence=0.25,
                source_refs=_coerce_str_list(item.get("source_refs")),
                rag_refs=[],
                risk_notes=[attempt.error] if attempt.error else ["缺少足够证据支撑自动补全。"],
            )
        )
    recommended_option_id = options[0].option_id if options else None
    rag_refs = list(dict.fromkeys(ref for evidence in rag_evidence for ref in evidence.source_refs if ref))
    return TreeGenerationHitlSuggestion(
        object_type=str(item.get("object_type")),
        object_id=str(item.get("object_id")),
        field=str(item.get("field")),
        current_status=FieldStatus(str(item.get("status"))),
        current_value=item.get("current_value"),
        reason=str(item.get("reason") or ""),
        source_refs=_coerce_str_list(item.get("source_refs")),
        rag_refs=rag_refs,
        options=options,
        recommended_option_id=recommended_option_id,
        generation_summary=(
            attempt.payload.summary
            if attempt.payload and attempt.payload.summary
            else attempt.error or "未生成可靠专家建议，需要人工补充资料。"
        ),
        llm_model=attempt.model,
        raw_output=attempt.raw_payload or {},
        raw_text=attempt.raw_text,
    )


def _apply_hitl_decision_to_artifact(
    artifact: TreeGenerationArtifact,
    decision: TreeGenerationHitlDecision,
) -> None:
    suggestion = next(
        (item for item in artifact.hitl_suggestions if item.suggestion_id == decision.suggestion_id),
        None,
    )
    if decision.action in {"NEEDS_MORE_EVIDENCE", "REJECT"}:
        return
    value = decision.value
    if decision.action == "ACCEPT_OPTION" and suggestion:
        option = next(
            (item for item in suggestion.options if item.option_id == decision.selected_option_id),
            None,
        )
        if option is not None:
            value = option.value
    elif decision.action == "KEEP_CURRENT" and suggestion:
        value = suggestion.current_value
    if value in (None, "") and decision.field != "test_ids":
        return
    _set_hitl_field_value(artifact, decision.object_type, decision.object_id, decision.field, value)


def _set_hitl_field_value(
    artifact: TreeGenerationArtifact,
    object_type: str,
    object_id: str,
    field: str,
    value: Any,
) -> None:
    if object_type in {
        OntologyEntityType.FAILURE_SYMPTOM.value,
        OntologyEntityType.ONTOLOGY_TEST.value,
        OntologyEntityType.ONTOLOGY_MEASURE.value,
    }:
        entities = {
            OntologyEntityType.FAILURE_SYMPTOM.value: artifact.symptoms,
            OntologyEntityType.ONTOLOGY_TEST.value: artifact.tests,
            OntologyEntityType.ONTOLOGY_MEASURE.value: artifact.measures,
        }[object_type]
        entity = next((item for item in entities if item.entity_id == object_id), None)
        if not entity:
            return
        if field == "name":
            entity.name = str(value)
            entity.name_status = FieldStatus.CONFIRMED
        elif field == "description":
            entity.description = str(value)
            entity.description_status = FieldStatus.CONFIRMED
        entity.properties["hitl_confirmed"] = True
        return
    if object_type != "SymptomTransition":
        return
    transition = next((item for item in artifact.transitions if item.transition_id == object_id), None)
    if not transition:
        return
    if field == "condition":
        transition.condition = str(value)
        transition.condition_status = FieldStatus.CONFIRMED
    elif field == "description":
        transition.description = str(value)
        transition.description_status = FieldStatus.CONFIRMED
    elif field == "test_ids":
        transition.test_ids = _ensure_transition_tests(artifact, value)


def _ensure_transition_tests(artifact: TreeGenerationArtifact, value: Any) -> list[str]:
    test_values = _coerce_str_list(value)
    existing_by_name = {_name_key(test.name or ""): test.entity_id for test in artifact.tests if test.name}
    existing_ids = {test.entity_id for test in artifact.tests}
    test_ids: list[str] = []
    for test_name in test_values:
        key = _name_key(test_name)
        if key in existing_by_name:
            test_ids.append(existing_by_name[key])
            continue
        test_id = _unique_entity_id(f"DRAFT_T_HITL_{len(existing_ids) + 1:03d}", existing_ids)
        existing_ids.add(test_id)
        artifact.tests.append(
            OntologyEntityDraft(
                entity_id=test_id,
                entity_type=OntologyEntityType.ONTOLOGY_TEST,
                name=test_name,
                name_status=FieldStatus.CONFIRMED,
                description=f"人工确认补充的检查项：{test_name}",
                description_status=FieldStatus.CONFIRMED,
                properties={"hitl_confirmed": True},
            )
        )
        test_ids.append(test_id)
    return test_ids


def _refresh_artifact_after_hitl(job: TreeGenerationJob) -> None:
    if not job.artifact:
        return
    job.artifact.validation_report = validate_tree_generation_artifact(job.artifact)
    job.artifact.rebuilt_fault_tree = rebuild_fault_tree_preview(job.artifact)
    if job.proposal:
        payload = _artifact_to_payload(job.artifact)
        job.proposal.candidate_start_symptom = payload.candidate_start_symptom
        job.proposal.candidate_failure_domain = payload.candidate_failure_domain
        job.proposal.root_cause_families = payload.root_cause_families
        job.proposal.candidate_tests = payload.candidate_tests
        job.proposal.candidate_transitions = payload.candidate_transitions
        job.proposal.confidence_summary = _confidence_summary(
            payload,
            job.artifact.validation_report,
            job.artifact.extraction_quality,
        )
        job.proposal.updated_at = utc_now_iso()


def _hitl_decision_review_log(
    job: TreeGenerationJob,
    decision: TreeGenerationHitlDecision,
) -> TreeProposalReviewLog:
    assert job.proposal is not None
    if decision.action in {"ACCEPT_OPTION", "MANUAL_VALUE", "KEEP_CURRENT"}:
        review_decision = "APPROVE"
        rationale = (
            f"树生成 HITL 确认字段 {decision.object_type}.{decision.object_id}.{decision.field}："
            f"{decision.action}。{decision.rationale or ''}"
        ).strip()
        required_changes: list[str] = []
    elif decision.action == "NEEDS_MORE_EVIDENCE":
        review_decision = "REQUEST_CHANGES"
        rationale = (
            f"树生成 HITL 字段 {decision.object_type}.{decision.object_id}.{decision.field} 需要补充资料。"
            f"{decision.rationale or ''}"
        ).strip()
        required_changes = [f"补充 {decision.object_type}.{decision.object_id}.{decision.field} 的来源证据"]
    else:
        review_decision = "REQUEST_CHANGES"
        rationale = (
            f"树生成 HITL 拒绝字段建议 {decision.object_type}.{decision.object_id}.{decision.field}。"
            f"{decision.rationale or ''}"
        ).strip()
        required_changes = [f"重新修订 {decision.object_type}.{decision.object_id}.{decision.field}"]
    return TreeProposalReviewLog(
        proposal_id=job.proposal.proposal_id,
        from_status=job.proposal.status,
        to_status=job.proposal.status,
        reviewer=decision.reviewer,
        decision=review_decision,
        rationale=rationale,
        required_changes=required_changes,
    )


def render_tree_generation_mermaid(artifact: TreeGenerationArtifact) -> str:
    symptom_by_id = {item.entity_id: item for item in artifact.symptoms}
    test_by_id = {item.entity_id: item for item in artifact.tests}
    lines = ["```mermaid", "flowchart TD"]
    for symptom in artifact.symptoms:
        node_id = _mermaid_id(symptom.entity_id)
        label = _mermaid_label(
            [
                symptom.name or symptom.entity_id,
                f"level: {symptom.level or 'unknown'}",
                f"name: {symptom.name_status}",
                f"desc: {symptom.description_status}",
            ]
        )
        lines.append(f'  {node_id}["{label}"]')
    for transition in artifact.transitions:
        if transition.source_id not in symptom_by_id or transition.target_id not in symptom_by_id:
            continue
        source_id = _mermaid_id(transition.source_id)
        target_id = _mermaid_id(transition.target_id)
        test_names = [
            test_by_id[test_id].name or test_id
            for test_id in transition.test_ids
            if test_id in test_by_id
        ]
        edge_label = _mermaid_label(test_names or ["MISSING test"])
        lines.append(f'  {source_id} -->|"{edge_label}"| {target_id}')
    if artifact.validation_report and artifact.validation_report.issues:
        lines.append("  %% validation issues exist; see validation report for details")
    lines.append("```")
    return "\n".join(lines)


def _mermaid_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if safe and safe[0].isdigit():
        safe = f"N_{safe}"
    return safe or "NODE"


def _mermaid_label(parts: list[str]) -> str:
    text = "\\n".join(part for part in parts if part)
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("|", "&#124;")


def preserve_hitl_candidates_after_repair(
    before: TreeGenerationArtifact,
    repaired: TreeGenerationArtifact,
) -> int:
    restored = 0
    symptom_id_map: dict[str, str] = {}
    test_id_map: dict[str, str] = {}
    symptom_names = {_name_key(item.name or ""): item.entity_id for item in repaired.symptoms if item.name}
    test_names = {_name_key(item.name or ""): item.entity_id for item in repaired.tests if item.name}
    measure_names = {_name_key(item.name or ""): item.entity_id for item in repaired.measures if item.name}
    symptom_ids = {item.entity_id for item in repaired.symptoms}
    test_ids = {item.entity_id for item in repaired.tests}
    measure_ids = {item.entity_id for item in repaired.measures}

    for symptom in before.symptoms:
        if symptom.name and _name_key(symptom.name) in symptom_names:
            symptom_id_map[symptom.entity_id] = symptom_names[_name_key(symptom.name)]
            continue
        if not _needs_hitl_preservation(symptom.name_status, symptom.description_status, symptom.properties):
            continue
        item = symptom.model_copy(deep=True)
        item.entity_id = _unique_entity_id(item.entity_id, symptom_ids)
        symptom_ids.add(item.entity_id)
        symptom_id_map[symptom.entity_id] = item.entity_id
        repaired.symptoms.append(item)
        restored += 1

    for test in before.tests:
        if test.name and _name_key(test.name) in test_names:
            test_id_map[test.entity_id] = test_names[_name_key(test.name)]
            continue
        if not _needs_hitl_preservation(test.name_status, test.description_status, test.properties):
            continue
        item = test.model_copy(deep=True)
        item.entity_id = _unique_entity_id(item.entity_id, test_ids)
        test_ids.add(item.entity_id)
        test_id_map[test.entity_id] = item.entity_id
        repaired.tests.append(item)
        restored += 1

    for measure in before.measures:
        if measure.name and _name_key(measure.name) in measure_names:
            continue
        if not _needs_hitl_preservation(measure.name_status, measure.description_status, measure.properties):
            continue
        item = measure.model_copy(deep=True)
        item.entity_id = _unique_entity_id(item.entity_id, measure_ids)
        measure_ids.add(item.entity_id)
        repaired.measures.append(item)
        restored += 1

    transition_pairs = {(item.source_id, item.target_id) for item in repaired.transitions}
    transition_ids = {item.transition_id for item in repaired.transitions}
    for transition in before.transitions:
        source_id = symptom_id_map.get(transition.source_id, transition.source_id)
        target_id = symptom_id_map.get(transition.target_id, transition.target_id)
        test_ids_mapped = [test_id_map.get(test_id, test_id) for test_id in transition.test_ids]
        if (source_id, target_id) in transition_pairs:
            continue
        if source_id not in symptom_ids or target_id not in symptom_ids:
            continue
        if any(test_id not in test_ids for test_id in test_ids_mapped):
            continue
        if not _needs_hitl_preservation(transition.condition_status, transition.description_status, {}):
            continue
        item = transition.model_copy(deep=True)
        item.transition_id = _unique_entity_id(item.transition_id, transition_ids)
        item.source_id = source_id
        item.target_id = target_id
        item.test_ids = test_ids_mapped
        transition_ids.add(item.transition_id)
        transition_pairs.add((item.source_id, item.target_id))
        repaired.transitions.append(item)
        restored += 1
    return restored


def _needs_hitl_preservation(
    first_status: FieldStatus,
    second_status: FieldStatus,
    properties: dict[str, Any],
) -> bool:
    return (
        first_status in {FieldStatus.EXTRACTED_INFERRED, FieldStatus.MISSING}
        or second_status in {FieldStatus.EXTRACTED_INFERRED, FieldStatus.MISSING}
        or bool(properties.get("needs_generation_hitl"))
    )


def _unique_entity_id(entity_id: str, existing: set[str]) -> str:
    if entity_id not in existing:
        return entity_id
    index = 1
    while f"{entity_id}_RESTORED_{index:02d}" in existing:
        index += 1
    return f"{entity_id}_RESTORED_{index:02d}"


def _append_generation_hitl_item(
    items: list[dict[str, Any]],
    *,
    object_type: str,
    object_id: str,
    field: str,
    status: FieldStatus,
    current_value: Any,
    source_refs: list[str],
) -> None:
    if status not in {FieldStatus.EXTRACTED_INFERRED, FieldStatus.MISSING}:
        return
    reason = (
        "字段缺失，需要基于原文/RAG/领域知识补全。"
        if status == FieldStatus.MISSING
        else "字段来自弱推断，需要人工确认或修订。"
    )
    items.append(
        {
            "object_type": object_type,
            "object_id": object_id,
            "field": field,
            "status": status.value,
            "current_value": current_value,
            "source_refs": source_refs,
            "reason": reason,
        }
    )


def _add_issue(
    issues: list[OntologyValidationIssue],
    severity: Literal["ERROR", "WARNING"],
    rule_id: str,
    message: str,
    entity_refs: list[str],
    repair_hint: str,
) -> None:
    issues.append(
        OntologyValidationIssue(
            severity=severity,
            rule_id=rule_id,
            message=message,
            entity_refs=entity_refs,
            repair_hint=repair_hint,
        )
    )


def _reverse_graph(graph: dict[str, list[str]]) -> dict[str, list[str]]:
    reverse: dict[str, list[str]] = {}
    for source, targets in graph.items():
        for target in targets:
            reverse.setdefault(target, []).append(source)
    return reverse


def _can_reach_any(start: str, targets: set[str], graph: dict[str, list[str]]) -> bool:
    queue: deque[str] = deque([start])
    seen: set[str] = set()
    while queue:
        node = queue.popleft()
        if node in targets:
            return True
        if node in seen:
            continue
        seen.add(node)
        queue.extend(item for item in graph.get(node, []) if item not in seen)
    return False


def _find_cycle(graph: dict[str, list[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        if node in visiting:
            start = path.index(node) if node in path else 0
            return [*path[start:], node]
        if node in visited:
            return None
        visiting.add(node)
        path.append(node)
        for target in graph.get(node, []):
            cycle = visit(target)
            if cycle:
                return cycle
        path.pop()
        visiting.remove(node)
        visited.add(node)
        return None

    for node in graph:
        cycle = visit(node)
        if cycle:
            return cycle
    return []


def rebuild_fault_tree_preview(artifact: TreeGenerationArtifact) -> dict[str, Any]:
    start_nodes = [item.entity_id for item in artifact.symptoms if item.level == "start"]
    start_id = start_nodes[0] if start_nodes else None
    if not start_id:
        return {"tree_id": None, "start_id": None, "symptom_ids": [], "transition_ids": []}
    outgoing: dict[str, list[SymptomTransitionDraft]] = {}
    for transition in artifact.transitions:
        outgoing.setdefault(transition.source_id, []).append(transition)
    seen: set[str] = set()
    transition_ids: list[str] = []
    queue: deque[str] = deque([start_id])
    while queue:
        node_id = queue.popleft()
        if node_id in seen:
            continue
        seen.add(node_id)
        for transition in outgoing.get(node_id, []):
            transition_ids.append(transition.transition_id)
            if transition.target_id not in seen:
                queue.append(transition.target_id)
    return {
        "tree_id": f"DRAFT_{artifact.job_id}",
        "start_id": start_id,
        "symptom_ids": list(seen),
        "transition_ids": transition_ids,
        "build_method": "deterministic_bfs_preview",
    }


def _candidate_extraction_prompt(
    title: str,
    description: str | None,
    chunks: list[dict[str, str]],
    *,
    strict_retry: bool,
) -> str:
    source_text = _source_prompt(chunks)
    retry_rules = ""
    if strict_retry:
        retry_rules = (
            "这是 PASS_1 的强约束重试：上一次返回了空 JSON 或空候选。\n"
            "禁止返回 {}。禁止所有候选数组同时为空。\n"
            "请逐个阅读每个 [chunk_id]，至少从资料中抽取：\n"
            "- 1 个 start symptom：客户/售后/产线可直接观察到的入口现象。\n"
            "- 1 个 root 或 inner symptom：资料中的根本原因、当前判断、异常状态或待确认原因。\n"
            "- 1 个 test：资料中的检查、测量、读取、复现、验证或后续采集项。\n"
            "如果某类信息确实缺失，也要输出 MISSING 占位候选，并在 risk_notes 说明缺口。\n"
        )
    return (
        f"任务标题：{title}\n任务描述：{description or ''}\n"
        "注意：任务标题和任务描述不是输入资料，不能被抽取为任何本体实体或诊断转移；"
        "候选实体必须来自下方资料片段，并尽量绑定 evidence_refs。\n"
        f"{retry_rules}"
        "PASS_1：只做候选抽取，不做最终建图。本次不需要输出 transition/start/root 结构图。\n"
        "请先按 tree_gen_agent.md 的工作流阅读资料：识别异常状态、检查动作、处置措施和叙事过程；"
        "不要直接把 8D 标题或“根本原因”整句投影成 root。\n"
        "请从资料中抽取：\n"
        "1. symptom_candidates：所有候选异常状态，不要包含检查动作和处置动作；可给 suggested_level。\n"
        "   必须尽量覆盖三类异常状态：\n"
        "   - start：客户/产线/检测系统可直接观察或报告的最大公约数入口现象。\n"
        "   - inner：经过检查、观察、分析后得到的更具体异常状态；例如“供电异常”“锁止预紧不足”"
        "“密封接触异常”“线束敲击异常”，它们不是动作，也不是最终处置根因。\n"
        "   - root：资料中已定位到的、不可再拆分、可直接挂接措施或验证闭环的最深层可处置异常。\n"
        "   如果原文出现“X 导致 Y”或“Y 由 X 导致”，通常 Y 更可能是 inner，X 更可能是 root；"
        "请把组合短语拆成候选异常状态，而不是合并成一个长 root。\n"
        '   每项格式：{"name": "...", "suggested_level": "start|inner|root", '
        '"description": "...", "evidence_refs": ["chunk_id"]}\n'
        "2. test_candidates：所有检查/测量/读取/观察/拆检/验证动作。\n"
        '   每项格式：{"name": "...", "target": "...", "evidence_refs": ["chunk_id"]}\n'
        "3. measure_candidates：所有处置/维修/整改/标定/复测后确认措施。\n"
        "4. transition_hints：原文支持的诊断链提示，例如“通过读取日志发现 IIC 通信超时”。\n"
        "必须使用以下顶层 JSON 字段名，不要用其他字段名替代：\n"
        "extraction_summary, candidate_failure_domain, symptom_candidates, "
        "test_candidates, measure_candidates, transition_hints, risk_notes。\n"
        "每个候选都必须尽量带 evidence_refs，引用资料片段中的 [chunk_id]。\n"
        f"资料片段：\n{source_text}"
    )


def _llm_extract_candidates(
    settings: Settings,
    title: str,
    description: str | None,
    chunks: list[dict[str, str]],
) -> LlmCandidateAttempt:
    provider = LlmProvider(settings)
    system_prompt = TREE_GENERATION_SYSTEM_PROMPT
    user_prompt = _candidate_extraction_prompt(title, description, chunks, strict_retry=False)
    candidates = provider.json_completion(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_model=LlmCandidateExtraction,
        complexity="pro",
        max_tokens=6000,
    )
    first_payload = provider.last_payload
    first_raw_text = provider.last_raw_content
    first_model = provider.last_model
    first_error = provider.last_error
    if candidates and _candidate_extraction_is_empty(candidates) and not first_error:
        retry_provider = LlmProvider(settings)
        retry_candidates = retry_provider.json_completion(
            system_prompt=system_prompt,
            user_prompt=_candidate_extraction_prompt(title, description, chunks, strict_retry=True),
            response_model=LlmCandidateExtraction,
            complexity="pro",
            max_tokens=8000,
        )
        raw_payload = _combined_llm_attempt_payload(
            [
                ("PASS_1_PRIMARY", first_model, first_error, first_payload),
                (
                    "PASS_1_STRICT_RETRY",
                    retry_provider.last_model,
                    retry_provider.last_error,
                    retry_provider.last_payload,
                ),
            ]
        )
        raw_text = _combined_llm_raw_text(
            [
                ("PASS_1_PRIMARY", first_model, first_raw_text),
                ("PASS_1_STRICT_RETRY", retry_provider.last_model, retry_provider.last_raw_content),
            ]
        )
        retry_error = retry_provider.last_error
        if retry_candidates and _candidate_extraction_is_empty(retry_candidates) and not retry_error:
            retry_error = "PASS_1 初次返回空候选，强约束重试后仍为空：未抽出任何候选 FailureSymptom。"
        return LlmCandidateAttempt(
            candidates=retry_candidates,
            error=retry_error,
            model=retry_provider.last_model or first_model,
            raw_payload=raw_payload,
            raw_text=raw_text,
        )
    return LlmCandidateAttempt(
        candidates=candidates,
        error=provider.last_error,
        model=provider.last_model,
        raw_payload=provider.last_payload,
        raw_text=provider.last_raw_content,
    )


def _llm_level_entities(
    settings: Settings,
    title: str,
    description: str | None,
    chunks: list[dict[str, str]],
    candidates: LlmCandidateExtraction,
) -> LlmGraphAttempt:
    provider = LlmProvider(settings)
    source_text = _source_prompt(chunks)
    system_prompt = TREE_GENERATION_SYSTEM_PROMPT
    user_prompt = (
        f"任务标题：{title}\n任务描述：{description or ''}\n"
        "注意：任务标题和任务描述不是输入资料，不能被抽取为任何本体实体或诊断转移；"
        "本体草案中的症状、检查和措施必须来自资料片段或候选抽取 JSON 的证据。\n"
        "PASS_2_LEVELING：根据候选抽取结果做实体分类、去重、start 合并和层级修正。"
        "若候选抽取 JSON 中 symptom_candidates 为空，请忽略它并直接从下方资料片段重新抽取 FailureSymptom，"
        "不要因 PASS_1 缺少症状而输出空 symptoms 列表。\n"
        "必须执行 tree_gen_agent.md 的写入前工作流，但本轮不要生成 transitions：\n"
        "1. 提取所有候选失效现象。\n"
        "2. 判断每个候选是异常状态、检查动作、处置措施还是叙事过程。\n"
        "3. 给真正的 FailureSymptom 分级为 start、inner、root。\n"
        "4. 自查颗粒度：每个 FailureSymptom 是否对应一次可验证诊断判定。\n"
        "5. 自查 start：同一失效域只保留一个最大公约数入口异常。\n"
        "6. 自查 root：root 必须是资料中实际定位到的最深层可处置根因；若还能继续拆分，应建模为 inner。\n"
        "7. 若原文存在“X 导致 Y / Y 由 X 导致”，通常 Y 是检查后得到的中间异常状态，X 是更深根因；"
        "不要把二者合并成一个长 root。\n"
        "8. 只有资料确实直接从入口现象定位到不可再拆分根因时，才保留 start -> root 的单跳结构。\n"
        "不允许返回空 symptoms/tests。\n"
        f"{LEVELING_JSON_CONTRACT}\n"
        f"候选抽取 JSON：\n{candidates.model_dump_json(ensure_ascii=False)}\n"
        f"资料片段：\n{source_text}"
    )
    graph = provider.json_completion(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_model=LlmOntologyDraftGraph,
        complexity="pro",
        max_tokens=8000,
    )
    return LlmGraphAttempt(
        graph=graph,
        error=provider.last_error,
        model=provider.last_model,
        raw_payload=provider.last_payload,
        raw_text=provider.last_raw_content,
    )


def _llm_bind_transitions(
    settings: Settings,
    title: str,
    description: str | None,
    chunks: list[dict[str, str]],
    leveled_graph: LlmOntologyDraftGraph,
) -> LlmGraphAttempt:
    provider = LlmProvider(settings)
    source_text = _source_prompt(chunks)
    system_prompt = TREE_GENERATION_SYSTEM_PROMPT
    user_prompt = (
        f"任务标题：{title}\n任务描述：{description or ''}\n"
        "注意：任务标题和任务描述不是输入资料，不能被抽取为任何本体实体或诊断转移；"
        "PASS_3_TRANSITION_BINDING：基于已分级实体生成 SymptomTransition，并保留/补齐字段 status。\n"
        "必须遵守：\n"
        "1. transition 表达诊断分解，不表达文档叙事顺序。\n"
        "2. 方向只能是 start -> inner/root 或 inner -> inner/root。\n"
        "3. 每条 transition 必须绑定至少一个 test_names；没有明确检查项时创建 "
        "name=null、name_status=MISSING 的占位 test。\n"
        "4. 检查动作留在 tests，不能变成 FailureSymptom。\n"
        "5. 不要随意改变 PASS_2 已完成的 start/inner/root 分级；只有发现明显违反 root 终止性或动作/异常混淆时才修正。\n"
        "6. 若 inner 已表达检查后得到的中间异常状态，应优先让 start 指向 inner，再由 inner 指向更深 root。\n"
        "7. 若资料确实没有中间异常状态，允许 start 直接指向 root。\n"
        "8. 输出必须包含完整 symptoms/tests/measures/transitions，而不是只输出 transitions diff。\n"
        f"{GRAPH_JSON_CONTRACT}\n"
        f"已分级本体实体 JSON：\n{leveled_graph.model_dump_json(ensure_ascii=False)}\n"
        f"资料片段：\n{source_text}"
    )
    graph = provider.json_completion(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_model=LlmOntologyDraftGraph,
        complexity="pro",
        max_tokens=8000,
    )
    return LlmGraphAttempt(
        graph=graph,
        error=provider.last_error,
        model=provider.last_model,
        raw_payload=provider.last_payload,
        raw_text=provider.last_raw_content,
    )


def _llm_build_graph(
    settings: Settings,
    title: str,
    description: str | None,
    chunks: list[dict[str, str]],
    candidates: LlmCandidateExtraction,
) -> LlmGraphAttempt:
    leveled = _llm_level_entities(settings, title, description, chunks, candidates)
    if not leveled.graph or _leveled_graph_is_empty(leveled.graph):
        return leveled
    return _llm_bind_transitions(settings, title, description, chunks, leveled.graph)


def _llm_extract_graph(
    settings: Settings,
    title: str,
    description: str | None,
    chunks: list[dict[str, str]],
) -> LlmGraphAttempt:
    candidates = _llm_extract_candidates(settings, title, description, chunks)
    if not candidates.candidates:
        return LlmGraphAttempt(
            graph=None,
            error=candidates.error,
            model=candidates.model,
            raw_payload=candidates.raw_payload,
            raw_text=candidates.raw_text,
        )
    return _llm_build_graph(settings, title, description, chunks, candidates.candidates)


def _llm_repair_graph(
    settings: Settings,
    title: str,
    description: str | None,
    chunks: list[dict[str, str]],
    graph: LlmOntologyDraftGraph,
    artifact: TreeGenerationArtifact,
) -> LlmGraphAttempt:
    if not artifact.validation_report or not artifact.validation_report.issues:
        return LlmGraphAttempt(graph=None, error="没有结构校验问题，跳过修复。")
    provider = LlmProvider(settings)
    source_text = _source_prompt(chunks)
    issues = [
        {
            "severity": issue.severity,
            "rule_id": issue.rule_id,
            "message": issue.message,
            "entity_refs": issue.entity_refs,
            "repair_hint": issue.repair_hint,
        }
        for issue in artifact.validation_report.issues[:20]
    ]
    system_prompt = TREE_GENERATION_SYSTEM_PROMPT
    user_prompt = (
        f"任务标题：{title}\n任务描述：{description or ''}\n"
        "注意：任务标题和任务描述不是输入资料，不能被抽取为任何本体实体或诊断转移；"
        "修复只能使用当前抽取图、校验问题和资料片段中的证据。\n"
        "PASS_4_REPAIR：根据结构校验问题修复本体草案。必须返回完整修复后的本体诊断图。\n"
        "修复重点：start 有且仅有一个；至少一个 root；每条 transition 绑定 test；"
        "动作和异常状态分离；inner/root 可从 start 到达；root 必须保持终止性，"
        "若当前 root 实际包含可继续拆分的异常状态和更深原因，应按原文证据拆分为 inner/root。\n"
        "重要保留规则：EXTRACTED_INFERRED 和 MISSING 表示需要进入树生成 HITL 补全/确认，"
        "不是删除理由。除非节点或关系重复、方向非法、引用不存在且无法补齐，不能因为低置信、待确认、"
        "GRAY 状态、缺少最终验证而删除实体、transition、test 或 measure。"
        "低置信内容应保留 status，并把风险写入 risk_notes。\n"
        f"{GRAPH_JSON_CONTRACT}\n"
        f"当前抽取图 JSON：\n{graph.model_dump_json(ensure_ascii=False)}\n"
        f"校验问题 JSON：\n{json.dumps(issues, ensure_ascii=False)}\n"
        f"资料片段：\n{source_text}"
    )
    graph = provider.json_completion(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_model=LlmOntologyDraftGraph,
        complexity="pro",
        max_tokens=6000,
    )
    return LlmGraphAttempt(
        graph=graph,
        error=provider.last_error,
        model=provider.last_model,
        raw_payload=provider.last_payload,
        raw_text=provider.last_raw_content,
    )


def _llm_graph_to_artifact(
    job_id: str,
    graph: LlmOntologyDraftGraph,
    chunks: list[dict[str, str]],
) -> TreeGenerationArtifact:
    chunk_ids = {chunk["chunk_id"] for chunk in chunks}
    source_by_chunk = {chunk["chunk_id"]: chunk["source_path"] for chunk in chunks}
    symptoms: list[OntologyEntityDraft] = []
    name_to_symptom_id: dict[str, str] = {}
    draft_to_symptom_id: dict[str, str] = {}
    for index, symptom in enumerate(graph.symptoms, 1):
        level = _normalize_level(symptom.level)
        entity_id = f"DRAFT_S_{level.upper()}_{index:03d}"
        refs = _clean_refs(symptom.evidence_refs, chunk_ids)
        symptoms.append(
            OntologyEntityDraft(
                entity_id=entity_id,
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name=symptom.name,
                name_status=symptom.name_status,
                level=level,
                description=symptom.description,
                description_status=symptom.description_status,
                chunk_ids=refs,
                source_refs=_source_refs_for_chunks(refs, source_by_chunk),
                properties={
                    "candidate_failure_domain": graph.candidate_failure_domain,
                    **_generation_hitl_properties(symptom.name_status, symptom.description_status),
                },
            )
        )
        name_to_symptom_id[_name_key(symptom.name)] = entity_id
        if symptom.draft_id:
            draft_to_symptom_id[_name_key(symptom.draft_id)] = entity_id
    tests: list[OntologyEntityDraft] = []
    name_to_test_id: dict[str, str] = {}
    draft_to_test_id: dict[str, str] = {}
    for index, test in enumerate(graph.tests, 1):
        entity_id = f"DRAFT_T_{index:03d}"
        refs = _clean_refs(test.evidence_refs, chunk_ids)
        tests.append(
            OntologyEntityDraft(
                entity_id=entity_id,
                entity_type=OntologyEntityType.ONTOLOGY_TEST,
                name=test.name,
                name_status=test.name_status if test.name else FieldStatus.MISSING,
                description=test.description,
                description_status=FieldStatus.EXTRACTED_INFERRED if test.description else FieldStatus.MISSING,
                chunk_ids=refs,
                source_refs=_source_refs_for_chunks(refs, source_by_chunk),
                properties={
                    "target": test.target,
                    "rule": test.rule,
                    **_generation_hitl_properties(
                        test.name_status if test.name else FieldStatus.MISSING,
                        FieldStatus.EXTRACTED_INFERRED if test.description else FieldStatus.MISSING,
                    ),
                },
            )
        )
        if test.name:
            name_to_test_id[_name_key(test.name)] = entity_id
        if test.draft_id:
            draft_to_test_id[_name_key(test.draft_id)] = entity_id
    measures: list[OntologyEntityDraft] = []
    for index, measure in enumerate(graph.measures, 1):
        refs = _clean_refs(measure.evidence_refs, chunk_ids)
        measures.append(
            OntologyEntityDraft(
                entity_id=f"DRAFT_M_{index:03d}",
                entity_type=OntologyEntityType.ONTOLOGY_MEASURE,
                name=measure.name,
                name_status=measure.name_status,
                description=measure.description,
                description_status=FieldStatus.EXTRACTED_INFERRED if measure.description else FieldStatus.MISSING,
                chunk_ids=refs,
                source_refs=_source_refs_for_chunks(refs, source_by_chunk),
                properties=_generation_hitl_properties(
                    measure.name_status,
                    FieldStatus.EXTRACTED_INFERRED if measure.description else FieldStatus.MISSING,
                ),
            )
        )
    transitions: list[SymptomTransitionDraft] = []
    for index, transition in enumerate(graph.transitions, 1):
        source_key = _name_key(transition.source_name)
        target_key = _name_key(transition.target_name)
        source_id = name_to_symptom_id.get(source_key) or draft_to_symptom_id.get(source_key) or transition.source_name
        target_id = name_to_symptom_id.get(target_key) or draft_to_symptom_id.get(target_key) or transition.target_name
        test_ids = [
            name_to_test_id.get(test_key) or draft_to_test_id[test_key]
            for test_name in transition.test_names
            if (test_key := _name_key(test_name)) in name_to_test_id or test_key in draft_to_test_id
        ]
        if not test_ids:
            missing_test = OntologyEntityDraft(
                entity_id=f"DRAFT_T_MISSING_{index:03d}",
                entity_type=OntologyEntityType.ONTOLOGY_TEST,
                name=None,
                name_status=FieldStatus.MISSING,
                description=f"占位检查：需补充用于判定 {transition.target_name} 的检查项。",
                description_status=FieldStatus.MISSING,
                properties={"needs_generation_hitl": True, "hitl_reasons": ["test_name:MISSING"]},
            )
            tests.append(missing_test)
            test_ids = [missing_test.entity_id]
        refs = _clean_refs(transition.evidence_refs, chunk_ids)
        transitions.append(
            SymptomTransitionDraft(
                transition_id=f"DRAFT_TR_{index:03d}",
                source_id=source_id,
                target_id=target_id,
                test_ids=test_ids,
                condition=transition.condition,
                condition_status=transition.condition_status if transition.condition else FieldStatus.MISSING,
                description=transition.description or f"{transition.source_name} -> {transition.target_name}",
                description_status=FieldStatus.EXTRACTED_INFERRED,
                chunk_ids=refs,
                source_refs=_source_refs_for_chunks(refs, source_by_chunk),
            )
        )
    return TreeGenerationArtifact(
        job_id=job_id,
        symptoms=symptoms,
        tests=tests,
        measures=measures,
        transitions=transitions,
    )


def _artifact_from_candidates(
    job_id: str,
    candidates: LlmCandidateExtraction,
    chunks: list[dict[str, str]],
) -> TreeGenerationArtifact:
    graph = _graph_from_candidates(candidates)
    return _llm_graph_to_artifact(job_id, graph, chunks)


def _graph_from_candidates(candidates: LlmCandidateExtraction) -> LlmOntologyDraftGraph:
    symptom_candidates = _dedup_symptom_candidates(candidates.symptom_candidates)
    start = _choose_start_candidate(symptom_candidates)
    roots = _choose_root_candidates(start, symptom_candidates)
    tests = _dedup_test_candidates(candidates.test_candidates)
    if not tests:
        tests = [
            LlmTestDraft(
                name=None,
                name_status=FieldStatus.MISSING,
                description="占位检查：候选抽取未给出明确检查项，需人工补充。",
            )
        ]
    symptoms = [
        LlmSymptomDraft(
            name=start.name,
            level="start",
            description=start.description,
            name_status=FieldStatus.EXTRACTED_INFERRED,
            description_status=FieldStatus.EXTRACTED_INFERRED if start.description else FieldStatus.MISSING,
            evidence_refs=start.evidence_refs,
        )
    ]
    symptoms.extend(
        LlmSymptomDraft(
            name=root.name,
            level="root",
            description=root.description,
            name_status=FieldStatus.EXTRACTED_INFERRED,
            description_status=FieldStatus.EXTRACTED_INFERRED if root.description else FieldStatus.MISSING,
            evidence_refs=root.evidence_refs,
        )
        for root in roots
    )
    transitions = [
        LlmTransitionDraft(
            source_name=start.name,
            target_name=root.name,
            test_names=_best_test_names_for_root(root, tests),
            condition=f"检查结果支持：{root.name}",
            description=f"{start.name} -> {root.name}",
            condition_status=FieldStatus.EXTRACTED_INFERRED,
            evidence_refs=root.evidence_refs or start.evidence_refs,
        )
        for root in roots
    ]
    return LlmOntologyDraftGraph(
        extraction_summary="系统根据 LLM 候选实体确定性组装本体草案，仍需校验修复和人工审核。",
        candidate_failure_domain=candidates.candidate_failure_domain,
        symptoms=symptoms,
        tests=tests,
        measures=_dedup_measure_candidates(candidates.measure_candidates),
        transitions=transitions,
        risk_notes=[
            *candidates.risk_notes,
            "PASS_2 LLM 建图不可用，本草案由候选实体确定性组装，不能直接发布。",
        ],
    )


def _dedup_symptom_candidates(candidates: list[LlmSymptomCandidate]) -> list[LlmSymptomCandidate]:
    seen: set[str] = set()
    result: list[LlmSymptomCandidate] = []
    for item in candidates:
        if not item.name:
            continue
        key = _normalize_bucket(item.name)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _dedup_test_candidates(candidates: list[LlmTestDraft]) -> list[LlmTestDraft]:
    seen: set[str] = set()
    result: list[LlmTestDraft] = []
    for item in candidates:
        key_source = item.name or item.description or item.target
        if not key_source:
            continue
        key = _normalize_bucket(key_source)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[:12]


def _dedup_measure_candidates(candidates: list[LlmMeasureDraft]) -> list[LlmMeasureDraft]:
    seen: set[str] = set()
    result: list[LlmMeasureDraft] = []
    for item in candidates:
        if not item.name:
            continue
        key = _normalize_bucket(item.name)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[:10]


def _choose_start_candidate(candidates: list[LlmSymptomCandidate]) -> LlmSymptomCandidate:
    for item in candidates:
        if _normalize_level_hint(item.suggested_level) == "start":
            return item
    for item in candidates:
        if any(keyword in item.name for keyword in ["无法", "不能", "困难", "失效", "异常", "不工作", "打不开"]):
            return LlmSymptomCandidate(
                name=_compact(item.name, 48),
                description=item.description,
                suggested_level="start",
                evidence_refs=item.evidence_refs,
            )
    return LlmSymptomCandidate(
        name="MISSING 占位入口现象：资料中未抽出明确 start symptom",
        suggested_level="start",
        rationale="任务标题不能作为抽取证据；需补充资料或人工确认入口现象。",
    )


def _choose_root_candidates(
    start: LlmSymptomCandidate,
    candidates: list[LlmSymptomCandidate],
) -> list[LlmSymptomCandidate]:
    roots = [
        item
        for item in candidates
        if _normalize_level_hint(item.suggested_level) == "root"
        and _normalize_bucket(item.name) != _normalize_bucket(start.name)
    ]
    if not roots:
        roots = [
            item
            for item in candidates
            if _normalize_bucket(item.name) != _normalize_bucket(start.name)
            and not any(keyword in item.name for keyword in ["临时措施", "后续措施", "关闭状态", "流出原因"])
        ]
    return roots[:8] or [
        LlmSymptomCandidate(
            name="MISSING 占位根因：资料中未抽出明确 root cause",
            suggested_level="root",
            rationale="候选抽取缺少 root，需人工补充或重新抽取。",
        )
    ]


def _best_test_names_for_root(root: LlmSymptomCandidate, tests: list[LlmTestDraft]) -> list[str]:
    root_text = root.name
    keywords = [
        token
        for token in ["执行器", "推力", "间隙", "密封圈", "摩擦", "面差", "行程", "BCM", "信号", "电压", "低温"]
        if token in root_text
    ]
    matched = [
        test.name
        for test in tests
        if test.name
        and any(token in " ".join([test.name, test.target or "", test.description or ""]) for token in keywords)
    ]
    if matched:
        return matched[:2]
    first_named = next((test.name for test in tests if test.name), None)
    return [first_named] if first_named else []


def _normalize_level_hint(value: str | None) -> str | None:
    if not value:
        return None
    return _normalize_level(value)


def _artifact_to_payload(artifact: TreeGenerationArtifact) -> TreeGenerationDraftPayload:
    start = next((item for item in artifact.symptoms if item.level == "start"), None)
    roots = [item.name or item.entity_id for item in artifact.symptoms if item.level == "root"]
    tests = [item.name or item.description or item.entity_id for item in artifact.tests]
    measures = [item.name or item.description or item.entity_id for item in artifact.measures]
    symptom_by_id = {item.entity_id: item for item in artifact.symptoms}
    test_by_id = {item.entity_id: item for item in artifact.tests}
    transitions = [
        " -> ".join(
            [
                symptom_by_id.get(transition.source_id).name
                if symptom_by_id.get(transition.source_id) and symptom_by_id[transition.source_id].name
                else transition.source_id,
                symptom_by_id.get(transition.target_id).name
                if symptom_by_id.get(transition.target_id) and symptom_by_id[transition.target_id].name
                else transition.target_id,
                " / ".join(
                    test_by_id[test_id].name or test_id
                    for test_id in transition.test_ids
                    if test_id in test_by_id
                )
                or "MISSING test",
            ]
        )
        for transition in artifact.transitions
    ]
    evidence = list(dict.fromkeys(ref for item in artifact.symptoms for ref in item.source_refs))[:8]
    return TreeGenerationDraftPayload(
        candidate_start_symptom=(
            start.name if start and start.name else "MISSING 占位入口现象：artifact 中无 start symptom"
        ),
        candidate_failure_domain=(
            str(start.properties.get("candidate_failure_domain"))
            if start and start.properties.get("candidate_failure_domain")
            else None
        ),
        root_cause_families=roots,
        candidate_tests=tests,
        candidate_measures=measures,
        candidate_transitions=transitions,
        evidence_summary=evidence,
    )


def _source_prompt(chunks: list[dict[str, str]]) -> str:
    return "\n\n".join(f"[{chunk['chunk_id']}] {chunk['text'][:1800]}" for chunk in chunks[:12])


def _normalize_level(value: str) -> Literal["start", "inner", "root"]:
    lowered = value.strip().lower()
    if lowered in {"start", "入口", "入口现象", "l1"}:
        return "start"
    if lowered in {"root", "根因", "终止", "l3", "l4"}:
        return "root"
    return "inner"


def _clean_refs(refs: list[str], allowed: set[str]) -> list[str]:
    cleaned = [ref for ref in refs if ref in allowed]
    return list(dict.fromkeys(cleaned))


def _source_refs_for_chunks(chunk_ids: list[str], source_by_chunk: dict[str, str]) -> list[str]:
    return list(dict.fromkeys(source_by_chunk[item] for item in chunk_ids if item in source_by_chunk))


def _name_key(value: str) -> str:
    return "".join(value.lower().split())


def _rule_extract(chunks: list[dict[str, str]]) -> TreeGenerationDraftPayload:
    text = "\n".join(chunk["text"] for chunk in chunks)
    start = _first_match(
        text,
        [
            r"(?:故障现象|失效现象|问题描述|客户抱怨|现场描述)[:：]\s*([^\n。；;]{4,80})",
            r"(?:主题|标题)[:：]\s*([^\n。；;]{4,80})",
        ],
    ) or "MISSING 占位入口现象：资料中未抽出明确 start symptom"
    domain = _first_match(text, [r"(?:业务域|系统|领域|模块)[:：]\s*([^\n。；;]{2,40})"])
    roots = _extract_lines(
        text,
        ["根因", "故障原因", "原因分析", "失效模式", "直接原因"],
        fallback_keywords=["异常", "损坏", "短路", "开路", "干涉", "松脱", "进水", "标定", "软件"],
    )
    tests = _extract_lines(
        text,
        ["检查", "检测", "测试", "测量", "读取", "确认", "复测", "验证"],
    )
    measures = _extract_lines(
        text,
        ["更换", "维修", "调整", "修复", "整改", "返修", "刷新", "标定"],
    )
    evidence = [_compact(chunk["text"], 180) for chunk in chunks[:6]]
    return TreeGenerationDraftPayload(
        candidate_start_symptom=_compact(start, 64),
        candidate_failure_domain=_compact(domain, 40) if domain else None,
        root_cause_families=_unique_or_default(roots, ["MISSING 占位根因：资料中未抽出明确 root cause"])[:8],
        candidate_tests=_unique_or_default(tests, ["MISSING 占位检查：补充可验证诊断检查项"])[:10],
        candidate_measures=list(dict.fromkeys(measures))[:8],
        evidence_summary=evidence,
    )


def _payload_to_artifact(
    job_id: str,
    payload: TreeGenerationDraftPayload,
    chunks: list[dict[str, str]],
) -> TreeGenerationArtifact:
    source_refs = [chunk["source_path"] for chunk in chunks]
    chunk_ids = [chunk["chunk_id"] for chunk in chunks]
    start_missing = _is_missing_placeholder(payload.candidate_start_symptom)
    start = OntologyEntityDraft(
        entity_id="DRAFT_S_START",
        entity_type=OntologyEntityType.FAILURE_SYMPTOM,
        name=payload.candidate_start_symptom,
        name_status=FieldStatus.MISSING if start_missing else FieldStatus.EXTRACTED_INFERRED,
        level="start",
        description="由输入资料抽取的入口失效现象；MISSING 表示资料未提供可用入口现象。",
        description_status=FieldStatus.MISSING if start_missing else FieldStatus.EXTRACTED_INFERRED,
        chunk_ids=chunk_ids[:4],
        source_refs=source_refs[:4],
        properties=_generation_hitl_properties(
            FieldStatus.MISSING if start_missing else FieldStatus.EXTRACTED_INFERRED
        ),
    )
    roots = [
        OntologyEntityDraft(
            entity_id=f"DRAFT_S_ROOT_{index + 1:03d}",
            entity_type=OntologyEntityType.FAILURE_SYMPTOM,
            name=root,
            name_status=FieldStatus.MISSING if _is_missing_placeholder(root) else FieldStatus.EXTRACTED_INFERRED,
            level="root",
            description="候选 root cause family，需由专家审核和后续本体建模细化。",
            description_status=FieldStatus.MISSING
            if _is_missing_placeholder(root)
            else FieldStatus.EXTRACTED_INFERRED,
            chunk_ids=chunk_ids[:4],
            source_refs=source_refs[:4],
            properties=_generation_hitl_properties(
                FieldStatus.MISSING if _is_missing_placeholder(root) else FieldStatus.EXTRACTED_INFERRED
            ),
        )
        for index, root in enumerate(payload.root_cause_families[:8])
    ]
    tests = [
        OntologyEntityDraft(
            entity_id=f"DRAFT_T_{index + 1:03d}",
            entity_type=OntologyEntityType.ONTOLOGY_TEST,
            name=None if test.startswith("MISSING") else test,
            name_status=FieldStatus.MISSING if test.startswith("MISSING") else FieldStatus.EXTRACTED_INFERRED,
            description=test,
            description_status=FieldStatus.MISSING if test.startswith("MISSING") else FieldStatus.EXTRACTED_INFERRED,
            chunk_ids=chunk_ids[:4],
            source_refs=source_refs[:4],
            properties=_generation_hitl_properties(
                FieldStatus.MISSING if test.startswith("MISSING") else FieldStatus.EXTRACTED_INFERRED
            ),
        )
        for index, test in enumerate(payload.candidate_tests[:10])
    ]
    if not tests:
        tests = [
            OntologyEntityDraft(
                entity_id="DRAFT_T_001",
                entity_type=OntologyEntityType.ONTOLOGY_TEST,
                name=None,
                name_status=FieldStatus.MISSING,
                description="占位检查：原始资料未明确检查项。",
                description_status=FieldStatus.MISSING,
                properties=_generation_hitl_properties(FieldStatus.MISSING),
            )
        ]
    measures = [
        OntologyEntityDraft(
            entity_id=f"DRAFT_M_{index + 1:03d}",
            entity_type=OntologyEntityType.ONTOLOGY_MEASURE,
            name=measure,
            name_status=FieldStatus.EXTRACTED_INFERRED,
            description=measure,
            description_status=FieldStatus.EXTRACTED_INFERRED,
            chunk_ids=chunk_ids[:4],
            source_refs=source_refs[:4],
            properties=_generation_hitl_properties(FieldStatus.EXTRACTED_INFERRED),
        )
        for index, measure in enumerate(payload.candidate_measures[:8])
    ]
    transitions: list[SymptomTransitionDraft] = []
    for index, root in enumerate(roots):
        test = tests[min(index, len(tests) - 1)]
        transitions.append(
            SymptomTransitionDraft(
                transition_id=f"DRAFT_TR_{index + 1:03d}",
                source_id=start.entity_id,
                target_id=root.entity_id,
                test_ids=[test.entity_id],
                condition=f"若检查支持：{test.description or test.entity_id}",
                condition_status=FieldStatus.EXTRACTED_INFERRED,
                description=f"{start.name} -> {root.name}",
                description_status=FieldStatus.EXTRACTED_INFERRED,
                chunk_ids=chunk_ids[:4],
                source_refs=source_refs[:4],
            )
        )
    return TreeGenerationArtifact(
        job_id=job_id,
        symptoms=[start, *roots],
        tests=tests,
        measures=measures,
        transitions=transitions,
    )


def _artifact_to_tree_proposal(
    job: TreeGenerationJob,
    artifact: TreeGenerationArtifact,
    payload: TreeGenerationDraftPayload,
) -> TreeProposal:
    source_refs = list(
        dict.fromkeys(ref for document in job.input_documents for ref in [document.source_path])
    )
    validation = artifact.validation_report
    risk_notes = [
        "批量文档生成入口产物为 DRAFT_TREE，不能生产 PASS。",
        "候选树必须经 Tree Proposal Eval、专家审核和灰度验证后才能发布。",
        "后续生成正式 TTL 时必须遵守 docs/tree_gen_agent.md 的本体建模和确定性重建流程。",
    ]
    if artifact.extraction_quality == TreeGenerationQuality.LOW_CONF_DEBUG_DRAFT:
        risk_notes.append("当前产物为规则低置信 debug fallback，不应作为高质量候选树。")
    if artifact.extraction_quality == TreeGenerationQuality.NEEDS_REPAIR_LLM_DRAFT:
        risk_notes.append("当前 LLM 草案仍存在校验问题，需要修复后再进入 CANDIDATE_TREE 评估。")
    if validation and validation.errors:
        risk_notes.extend(validation.errors)
    return TreeProposal(
        source_job_id=job.job_id,
        phenomenon_bucket=_normalize_bucket(payload.candidate_start_symptom),
        candidate_start_symptom=payload.candidate_start_symptom,
        candidate_failure_domain=payload.candidate_failure_domain,
        root_cause_families=payload.root_cause_families,
        candidate_tests=payload.candidate_tests,
        candidate_transitions=[
            transition.description or transition.transition_id for transition in artifact.transitions
        ],
        source_refs=source_refs,
        confidence_summary=_confidence_summary(payload, validation, artifact.extraction_quality),
        risk_notes=risk_notes,
        allowed_next_statuses=[],
    )


def _merge_payload(
    rule_payload: TreeGenerationDraftPayload,
    llm_payload: TreeGenerationDraftPayload,
) -> TreeGenerationDraftPayload:
    return TreeGenerationDraftPayload(
        candidate_start_symptom=llm_payload.candidate_start_symptom or rule_payload.candidate_start_symptom,
        candidate_failure_domain=llm_payload.candidate_failure_domain or rule_payload.candidate_failure_domain,
        root_cause_families=_unique_or_default(
            [*llm_payload.root_cause_families, *rule_payload.root_cause_families],
            rule_payload.root_cause_families,
        )[:8],
        candidate_tests=_unique_or_default(
            [*llm_payload.candidate_tests, *rule_payload.candidate_tests],
            rule_payload.candidate_tests,
        )[:10],
        candidate_measures=list(
            dict.fromkeys([*llm_payload.candidate_measures, *rule_payload.candidate_measures])
        )[:8],
        evidence_summary=list(dict.fromkeys([*llm_payload.evidence_summary, *rule_payload.evidence_summary]))[:8],
    )


def _document_record(path: Path) -> TreeGenerationInputDocument:
    return TreeGenerationInputDocument(
        source_path=str(path),
        filename=path.name,
        doc_type=_doc_type(path),
        size_bytes=path.stat().st_size,
        chunk_ids=[f"{path.name}:0"],
    )


def _read_source_chunks(path: Path, chunk_size: int = 2200, overlap: int = 250) -> list[dict[str, str]]:
    text = _read_text(path)
    text = " ".join(text.split())
    if not text:
        return []
    chunks: list[dict[str, str]] = []
    step = max(1, chunk_size - overlap)
    start = 0
    index = 0
    while start < len(text):
        chunks.append(
            {
                "chunk_id": f"{path.name}:{index}",
                "source_path": str(path),
                "text": text[start : start + chunk_size],
            }
        )
        start += step
        index += 1
    return chunks


def _read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return ""
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix == ".csv":
        rows: list[str] = []
        with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as handle:
            sample = handle.read(2048)
            handle.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(handle, dialect=dialect)
            for row in reader:
                rows.append(" | ".join(f"{key}: {value}" for key, value in row.items() if key and value))
        return "\n".join(rows)
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_lines(
    text: str,
    anchors: list[str],
    fallback_keywords: list[str] | None = None,
) -> list[str]:
    candidates: list[str] = []
    for raw_line in re.split(r"[\n。；;]", text):
        line = raw_line.strip(" -\t\r:：")
        if not line or len(line) < 4:
            continue
        if any(anchor in line for anchor in anchors):
            candidates.append(_clean_candidate(line))
    if not candidates and fallback_keywords:
        for raw_line in re.split(r"[\n。；;]", text):
            line = raw_line.strip(" -\t\r:：")
            if any(keyword in line for keyword in fallback_keywords):
                candidates.append(_clean_candidate(line))
    return list(dict.fromkeys(_compact(item, 96) for item in candidates if item))[:12]


def _first_match(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _clean_candidate(match.group(1))
    return None


def _clean_candidate(value: str) -> str:
    value = re.sub(r"^(?:根因|故障原因|原因分析|检查|检测|测试|测量|整改|措施|处理)[:：]?", "", value)
    return " ".join(value.split()).strip(" -:：")


def _unique_or_default(values: list[str], fallback: list[str]) -> list[str]:
    cleaned = [item for item in (value.strip() for value in values) if item]
    return list(dict.fromkeys(cleaned)) or fallback


def _compact(value: str, limit: int) -> str:
    text = " ".join(value.strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _is_missing_placeholder(value: str | None) -> bool:
    return bool(value and value.strip().upper().startswith("MISSING"))


def _normalize_bucket(value: str) -> str:
    return "".join(value.lower().split())[:80]


def _doc_type(path: Path) -> str:
    name = path.name.lower()
    if "8d" in name:
        return "8D"
    if "fmea" in name:
        return "FMEA"
    if "sop" in name:
        return "SOP"
    if "quality" in name or "report" in name or "质量" in name:
        return "QUALITY_REPORT"
    return path.suffix.lower().removeprefix(".").upper() or "UNKNOWN"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _confidence_summary(
    payload: TreeGenerationDraftPayload,
    validation: TreeGenerationValidationReport | None,
    quality: TreeGenerationQuality = TreeGenerationQuality.LOW_CONF_DEBUG_DRAFT,
) -> str:
    if quality == TreeGenerationQuality.LOW_CONF_DEBUG_DRAFT:
        return "LLM 不可用或抽取失败，当前仅为规则低置信调试草案，不能作为可用树。"
    if quality == TreeGenerationQuality.NEEDS_REPAIR_LLM_DRAFT:
        return "LLM 已生成本体草案，但仍存在结构校验问题，需要继续修复。"
    root_count = len(payload.root_cause_families)
    test_count = len(payload.candidate_tests)
    if validation and validation.is_valid and root_count >= 2 and test_count >= 2:
        return "结构校验通过，已形成候选 start/root/test/transition，但仍需专家审核。"
    if validation and validation.errors:
        return "候选草案存在结构阻塞项，需要补充资料或人工修订后再评测。"
    return "候选草案已生成，证据和检查项仍需补充验证。"


def _candidate_counts(candidates: LlmCandidateExtraction) -> dict[str, int]:
    return {
        "symptom_candidates": len(candidates.symptom_candidates),
        "test_candidates": len(candidates.test_candidates),
        "measure_candidates": len(candidates.measure_candidates),
        "transition_hints": len(candidates.transition_hints),
    }


def _candidate_extraction_is_empty(candidates: LlmCandidateExtraction | None) -> bool:
    if candidates is None:
        return True
    counts = _candidate_counts(candidates)
    return all(value == 0 for value in counts.values())


def _combined_llm_attempt_payload(
    attempts: list[tuple[str, str | None, str | None, dict[str, Any] | None]],
) -> dict[str, Any]:
    return {
        "attempts": [
            {
                "label": label,
                "model": model,
                "error": error,
                "parsed_json": payload or {},
            }
            for label, model, error, payload in attempts
        ]
    }


def _combined_llm_raw_text(attempts: list[tuple[str, str | None, str | None]]) -> str | None:
    sections = [
        f"--- {label} / {model or 'unknown_model'} ---\n{raw_text or ''}".strip()
        for label, model, raw_text in attempts
        if raw_text is not None
    ]
    return "\n\n".join(sections) if sections else None


def _graph_counts(graph: LlmOntologyDraftGraph) -> dict[str, int]:
    return {
        "symptoms": len(graph.symptoms),
        "tests": len(graph.tests),
        "measures": len(graph.measures),
        "transitions": len(graph.transitions),
        "start": sum(1 for item in graph.symptoms if _normalize_level(item.level) == "start"),
        "inner": sum(1 for item in graph.symptoms if _normalize_level(item.level) == "inner"),
        "root": sum(1 for item in graph.symptoms if _normalize_level(item.level) == "root"),
    }


def _leveled_graph_is_empty(graph: LlmOntologyDraftGraph) -> bool:
    counts = _graph_counts(graph)
    return counts["symptoms"] == 0 or counts["tests"] == 0


def _graph_is_empty(graph: LlmOntologyDraftGraph) -> bool:
    counts = _graph_counts(graph)
    return counts["symptoms"] == 0 or counts["tests"] == 0 or counts["transitions"] == 0


def _artifact_counts(artifact: TreeGenerationArtifact) -> dict[str, int]:
    return {
        "symptoms": len(artifact.symptoms),
        "tests": len(artifact.tests),
        "measures": len(artifact.measures),
        "transitions": len(artifact.transitions),
        "start": sum(1 for item in artifact.symptoms if item.level == "start"),
        "inner": sum(1 for item in artifact.symptoms if item.level == "inner"),
        "root": sum(1 for item in artifact.symptoms if item.level == "root"),
    }


def _candidate_preview(candidates: LlmCandidateExtraction) -> dict[str, Any]:
    return {
        "candidate_failure_domain": candidates.candidate_failure_domain,
        "symptom_candidates": [
            {
                "name": item.name,
                "suggested_level": item.suggested_level,
                "description": item.description,
                "evidence_refs": item.evidence_refs[:3],
            }
            for item in candidates.symptom_candidates[:12]
        ],
        "test_candidates": [
            {
                "name": item.name,
                "target": item.target,
                "rule": item.rule,
                "name_status": item.name_status,
                "evidence_refs": item.evidence_refs[:3],
            }
            for item in candidates.test_candidates[:12]
        ],
        "measure_candidates": [
            {
                "name": item.name,
                "description": item.description,
                "name_status": item.name_status,
                "evidence_refs": item.evidence_refs[:3],
            }
            for item in candidates.measure_candidates[:8]
        ],
        "transition_hints": candidates.transition_hints[:8],
        "risk_notes": candidates.risk_notes[:8],
    }


def _artifact_preview(artifact: TreeGenerationArtifact) -> dict[str, Any]:
    return {
        "symptoms": [
            {
                "entity_id": item.entity_id,
                "name": item.name,
                "name_status": item.name_status,
                "level": item.level,
                "description": item.description,
                "description_status": item.description_status,
                "source_refs": item.source_refs[:3],
            }
            for item in artifact.symptoms[:16]
        ],
        "tests": [
            {
                "entity_id": item.entity_id,
                "name": item.name,
                "name_status": item.name_status,
                "description": item.description,
                "description_status": item.description_status,
                "properties": item.properties,
            }
            for item in artifact.tests[:16]
        ],
        "transitions": [
            {
                "transition_id": item.transition_id,
                "source_id": item.source_id,
                "target_id": item.target_id,
                "test_ids": item.test_ids,
                "condition": item.condition,
                "condition_status": item.condition_status,
                "description": item.description,
                "description_status": item.description_status,
            }
            for item in artifact.transitions[:16]
        ],
    }


def _graph_preview(graph: LlmOntologyDraftGraph) -> dict[str, Any]:
    return {
        "candidate_failure_domain": graph.candidate_failure_domain,
        "symptoms": [
            {
                "name": item.name,
                "level": _normalize_level(item.level),
                "name_status": item.name_status,
                "description": item.description,
                "description_status": item.description_status,
                "evidence_refs": item.evidence_refs[:3],
            }
            for item in graph.symptoms[:16]
        ],
        "tests": [
            {
                "name": item.name,
                "target": item.target,
                "rule": item.rule,
                "description": item.description,
                "name_status": item.name_status,
            }
            for item in graph.tests[:16]
        ],
        "transitions": [
            {
                "source_name": item.source_name,
                "target_name": item.target_name,
                "test_names": item.test_names[:4],
                "condition": item.condition,
                "condition_status": item.condition_status,
                "description": item.description,
            }
            for item in graph.transitions[:16]
        ],
        "risk_notes": graph.risk_notes[:8],
    }


def _safe_payload_preview(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    preview: dict[str, Any] = {}
    for key in [
        "extraction_summary",
        "candidate_failure_domain",
        "entities",
        "symptom_candidates",
        "symptoms",
        "failure_symptoms",
        "root_causes",
        "failure_modes",
        "abnormal_states",
        "phenomena",
        "checks",
        "check_items",
        "tests",
        "ontology_tests",
        "corrective_actions",
        "permanent_actions",
        "measures",
        "measure_candidates",
        "diagnostic_paths",
        "causal_chains",
        "causal_relations",
        "transitions",
        "symptom_transitions",
        "risk_notes",
    ]:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, list):
            preview[key] = value[:5]
        else:
            preview[key] = value
    return preview


def _fallback_summary(use_llm: bool, llm_error: str | None, llm_model: str | None) -> str:
    if not use_llm:
        return "本次未启用 LLM-first 抽取，使用规则低置信兜底；该结果不能作为高质量候选树。"
    detail = llm_error or "LLM 未返回可校验本体草案，原因未知。"
    model = f"模型：{llm_model}。" if llm_model else ""
    return f"LLM 抽取失败，使用规则低置信兜底；该结果不能作为高质量候选树。{model}失败原因：{detail}"


def _llm_pass_summary(summary: str, llm_model: str | None) -> str:
    if not llm_model:
        return summary
    return f"{summary}（模型：{llm_model}）"


def _ontology_constraints() -> list[str]:
    return [
        "不要让 LLM 直接输出最终 FaultTree 或修改 FaultTree.symptom_ids。",
        "LLM/Agent 只负责维护 FailureSymptom、OntologyTest、OntologyMeasure、SymptomTransition。",
        "SymptomTransition 必须引用非空 OntologyTest；缺失检查项时创建 MISSING 占位 test。",
        "最终 FaultTree 必须由 start 节点沿 SymptomTransition 确定性 BFS 重建。",
        "DRAFT_TREE 不能生产 PASS，必须经过 eval、人工审核、gray/released 生命周期。",
    ]
