from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ft_diag_agent.models import (
    FieldStatus,
    OntologyEntityDraft,
    SymptomTransitionDraft,
    TreeGenerationArtifact,
    TreeProposal,
    TreeProposalEvalResult,
)
from ft_diag_agent.tree_generation import validate_tree_generation_artifact
from ft_diag_agent.tree_proposals import TreeProposalStore

TREE_GENERATION_EXTRACTION_EVAL_SUITE = "tree_generation_extraction_v1"
TREE_GENERATION_EXTRACTION_RUBRIC_VERSION = "tree_generation_extraction_rubric_v1"

MIN_ONTOLOGY_STRUCTURE_SCORE = 0.85
MIN_PATH_COHERENCE_SCORE = 0.8
MAX_HALLUCINATION_RATE = 0.2
MIN_GROUNDING_PRECISION = 0.5

_DIAGNOSTIC_FACT_MARKERS = [
    "故障",
    "异常",
    "失效",
    "异响",
    "黑屏",
    "无法",
    "不能",
    "偏差",
    "超差",
    "短路",
    "断路",
    "松动",
    "卡滞",
    "磨损",
    "检测",
    "检查",
    "测量",
    "读取",
    "确认",
    "根因",
    "原因",
]
_CONTRADICTION_MARKERS = ["排除", "不支持", "正常", "无异常", "无效", "误判", "反驳", "不成立"]


def run_tree_generation_extraction_eval(
    store: TreeProposalStore,
    proposal_id: str,
    *,
    persist: bool = True,
) -> TreeProposalEvalResult | None:
    proposal = store.get_proposal(proposal_id)
    if not proposal:
        return None
    artifact = store.load_artifact_snapshot(proposal_id)
    result = evaluate_tree_generation_extraction(proposal, artifact)
    if persist:
        store.append_eval_result(result)
    return result


def evaluate_tree_generation_extraction(
    proposal: TreeProposal,
    artifact: TreeGenerationArtifact | None,
    *,
    source_texts: list[str] | None = None,
) -> TreeProposalEvalResult:
    if artifact is None:
        return TreeProposalEvalResult(
            proposal_id=proposal.proposal_id,
            eval_suite=TREE_GENERATION_EXTRACTION_EVAL_SUITE,
            status_at_eval=proposal.status,
            metrics={
                "rubric_version": TREE_GENERATION_EXTRACTION_RUBRIC_VERSION,
                "artifact_present": False,
                "candidate_ready": False,
                "source_fact_recall_status": "not_available",
            },
            failure_cases=[{"rule_id": "ARTIFACT_MISSING", "message": "缺少 TreeGenerationArtifact。"}],
            unsafe_findings=["ARTIFACT_MISSING"],
        )

    validation = artifact.validation_report or validate_tree_generation_artifact(artifact)
    source_text = "\n".join(source_texts if source_texts is not None else _load_source_texts(proposal, artifact))
    artifact_text = _artifact_text(proposal, artifact)

    structure_score = _ontology_structure_score(artifact, validation)
    field_completeness_rate = _field_completeness_rate(artifact)
    source_fact_rows = _source_fact_rows(source_text, artifact_text)
    source_fact_recall = _ratio(
        sum(1 for row in source_fact_rows if row["covered"]),
        len(source_fact_rows),
    )
    grounding_rows = _artifact_grounding_rows(artifact, source_text)
    grounding_precision = _ratio(
        sum(1 for row in grounding_rows if row["grounded"]),
        len(grounding_rows),
    )
    hallucination_candidates = [
        row for row in grounding_rows if not row["hitl_confirmed"] and row["value"]
    ]
    hallucination_rate = _ratio(
        sum(1 for row in hallucination_candidates if not row["grounded"]),
        len(hallucination_candidates),
    )
    path_rows = _path_coherence_rows(artifact)
    path_coherence_score = _ratio(
        sum(1 for row in path_rows if row["coherent"]),
        len(path_rows),
    )
    test_actionability_rate = _test_actionability_rate(artifact)
    contradiction_rows = _contradiction_rows(artifact, source_text)
    duplicate_semantic_rate = _duplicate_semantic_rate(artifact)

    unsafe_findings = _unsafe_findings(
        structure_score=structure_score,
        path_coherence_score=path_coherence_score,
        hallucination_rate=hallucination_rate,
        grounding_precision=grounding_precision,
    )
    metrics: dict[str, Any] = {
        "rubric_version": TREE_GENERATION_EXTRACTION_RUBRIC_VERSION,
        "artifact_present": True,
        "candidate_ready": not any(
            item in unsafe_findings
            for item in ["ONTOLOGY_STRUCTURE_BLOCKED", "PATH_COHERENCE_BLOCKED", "HALLUCINATION_HIGH"]
        ),
        "ontology_structure_score": structure_score,
        "field_completeness_rate": field_completeness_rate,
        "source_fact_recall": source_fact_recall,
        "source_fact_recall_status": "available" if source_fact_rows else "not_available",
        "grounding_precision": grounding_precision,
        "hallucination_rate": hallucination_rate,
        "path_coherence_score": path_coherence_score,
        "test_actionability_rate": test_actionability_rate,
        "contradiction_count": len(contradiction_rows),
        "duplicate_semantic_rate": duplicate_semantic_rate,
        "source_fact_rows": source_fact_rows,
        "artifact_grounding_rows": grounding_rows,
        "path_coherence_rows": path_rows,
    }
    failure_cases = [
        {"rule_id": finding, "message": _finding_message(finding)}
        for finding in unsafe_findings
    ]
    failure_cases.extend(contradiction_rows[:20])
    return TreeProposalEvalResult(
        proposal_id=proposal.proposal_id,
        eval_suite=TREE_GENERATION_EXTRACTION_EVAL_SUITE,
        status_at_eval=proposal.status,
        metrics=metrics,
        failure_cases=failure_cases,
        unsafe_findings=unsafe_findings,
    )


def _ontology_structure_score(artifact: TreeGenerationArtifact, validation) -> float:
    missing_test_transition_count = sum(1 for transition in artifact.transitions if not transition.test_ids)
    checks = [
        validation.is_valid,
        validation.start_symptom_count == 1,
        validation.root_symptom_count >= 1,
        validation.test_count >= 1,
        validation.transition_count >= 1,
        missing_test_transition_count == 0,
    ]
    return sum(1 for item in checks if item) / len(checks)


def _field_completeness_rate(artifact: TreeGenerationArtifact) -> float | None:
    rows: list[tuple[Any, FieldStatus | None]] = []
    for entity in [*artifact.symptoms, *artifact.tests, *artifact.measures]:
        rows.extend([(entity.name, entity.name_status), (entity.description, entity.description_status)])
    for transition in artifact.transitions:
        rows.extend(
            [
                (transition.condition, transition.condition_status),
                (transition.description, transition.description_status),
            ]
        )
    if not rows:
        return None
    complete = sum(1 for value, status in rows if _has_value(value) and status != FieldStatus.MISSING)
    return complete / len(rows)


def _source_fact_rows(source_text: str, artifact_text: str) -> list[dict[str, Any]]:
    facts = _diagnostic_facts(source_text)
    compact_artifact = _compact(artifact_text)
    rows = []
    for fact in facts:
        key = _compact(fact)
        rows.append(
            {
                "fact": fact,
                "covered": bool(key and key in compact_artifact),
                "match_method": "normalized_substring",
            }
        )
    return rows


def _artifact_grounding_rows(artifact: TreeGenerationArtifact, source_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    compact_source = _compact(source_text)
    for entity in [*artifact.symptoms, *artifact.tests, *artifact.measures]:
        rows.append(_entity_grounding_row(entity, compact_source))
    for transition in artifact.transitions:
        rows.append(_transition_grounding_row(transition, compact_source))
    return rows


def _entity_grounding_row(entity: OntologyEntityDraft, compact_source: str) -> dict[str, Any]:
    value = entity.name or entity.description or entity.entity_id
    confirmed_statuses = {FieldStatus.CONFIRMED, FieldStatus.VERIFIED}
    hitl_confirmed = entity.name_status in confirmed_statuses or entity.description_status in confirmed_statuses
    grounded = bool(entity.source_refs or entity.chunk_ids or (_compact(value) and _compact(value) in compact_source))
    return {
        "object_type": str(entity.entity_type),
        "object_id": entity.entity_id,
        "value": value,
        "grounded": grounded,
        "hitl_confirmed": hitl_confirmed,
        "source_refs": entity.source_refs,
        "chunk_ids": entity.chunk_ids,
    }


def _transition_grounding_row(transition: SymptomTransitionDraft, compact_source: str) -> dict[str, Any]:
    value = transition.condition or transition.description or f"{transition.source_id}->{transition.target_id}"
    hitl_confirmed = transition.condition_status in {
        FieldStatus.CONFIRMED,
        FieldStatus.VERIFIED,
    } or transition.description_status in {FieldStatus.CONFIRMED, FieldStatus.VERIFIED}
    value_key = _compact(value)
    grounded = bool(transition.source_refs or transition.chunk_ids or (value_key and value_key in compact_source))
    return {
        "object_type": "SymptomTransition",
        "object_id": transition.transition_id,
        "value": value,
        "grounded": grounded,
        "hitl_confirmed": hitl_confirmed,
        "source_refs": transition.source_refs,
        "chunk_ids": transition.chunk_ids,
    }


def _path_coherence_rows(artifact: TreeGenerationArtifact) -> list[dict[str, Any]]:
    symptoms = {item.entity_id: item for item in artifact.symptoms}
    outgoing = {transition.source_id for transition in artifact.transitions}
    rows: list[dict[str, Any]] = []
    for transition in artifact.transitions:
        source = symptoms.get(transition.source_id)
        target = symptoms.get(transition.target_id)
        reasons: list[str] = []
        if not source:
            reasons.append("source_missing")
        if not target:
            reasons.append("target_missing")
        if source and source.level == "root":
            reasons.append("root_has_outgoing")
        if target and target.level == "start":
            reasons.append("target_is_start")
        if target and target.level == "root" and target.entity_id in outgoing:
            reasons.append("root_not_terminal")
        if not transition.test_ids:
            reasons.append("transition_test_missing")
        rows.append(
            {
                "transition_id": transition.transition_id,
                "source_id": transition.source_id,
                "target_id": transition.target_id,
                "test_ids": transition.test_ids,
                "coherent": not reasons,
                "reasons": reasons,
            }
        )
    return rows


def _test_actionability_rate(artifact: TreeGenerationArtifact) -> float | None:
    if not artifact.tests:
        return None
    actionable = 0
    for test in artifact.tests:
        has_name = _has_value(test.name) and test.name_status != FieldStatus.MISSING
        has_method = _has_value(test.description) or any(
            _has_value(test.properties.get(key)) for key in ["rule", "target", "executor_type", "tool_name"]
        )
        if has_name and has_method:
            actionable += 1
    return actionable / len(artifact.tests)


def _contradiction_rows(artifact: TreeGenerationArtifact, source_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not source_text:
        return rows
    values = [
        (entity.entity_id, entity.name or entity.description)
        for entity in [*artifact.symptoms, *artifact.tests, *artifact.measures]
        if entity.name or entity.description
    ]
    values.extend(
        (transition.transition_id, transition.condition or transition.description)
        for transition in artifact.transitions
        if transition.condition or transition.description
    )
    for sentence in _sentences(source_text):
        if not any(marker in sentence for marker in _CONTRADICTION_MARKERS):
            continue
        compact_sentence = _compact(sentence)
        for object_id, value in values:
            if value and _compact(value) in compact_sentence:
                rows.append(
                    {
                        "rule_id": "SOURCE_CONTRADICTION",
                        "object_id": object_id,
                        "source_sentence": sentence[:240],
                        "message": "原文语句包含反证/排除语义，需要专家复核。",
                    }
                )
    return rows


def _duplicate_semantic_rate(artifact: TreeGenerationArtifact) -> float | None:
    keys = [
        _name_key(entity.name)
        for entity in [*artifact.symptoms, *artifact.tests, *artifact.measures]
        if _name_key(entity.name)
    ]
    keys.extend(
        _name_key(f"{transition.source_id}->{transition.target_id}")
        for transition in artifact.transitions
        if transition.source_id and transition.target_id
    )
    if not keys:
        return None
    duplicate_count = len(keys) - len(set(keys))
    return duplicate_count / len(keys)


def _unsafe_findings(
    *,
    structure_score: float,
    path_coherence_score: float | None,
    hallucination_rate: float | None,
    grounding_precision: float | None,
) -> list[str]:
    findings: list[str] = []
    if structure_score < MIN_ONTOLOGY_STRUCTURE_SCORE:
        findings.append("ONTOLOGY_STRUCTURE_BLOCKED")
    if path_coherence_score is None or path_coherence_score < MIN_PATH_COHERENCE_SCORE:
        findings.append("PATH_COHERENCE_BLOCKED")
    if hallucination_rate is not None and hallucination_rate > MAX_HALLUCINATION_RATE:
        findings.append("HALLUCINATION_HIGH")
    if grounding_precision is None or grounding_precision < MIN_GROUNDING_PRECISION:
        findings.append("GROUNDING_LOW")
    return findings


def _finding_message(finding: str) -> str:
    messages = {
        "ARTIFACT_MISSING": "缺少 TreeGenerationArtifact。",
        "ONTOLOGY_STRUCTURE_BLOCKED": "本体结构评分低于晋升门槛。",
        "PATH_COHERENCE_BLOCKED": "L1/L2/L3 诊断链路逻辑低于晋升门槛。",
        "HALLUCINATION_HIGH": "未确认且缺少来源支撑的抽取项比例过高。",
        "GROUNDING_LOW": "artifact 来源绑定或原文 grounding 覆盖不足。",
    }
    return messages.get(finding, finding)


def _load_source_texts(proposal: TreeProposal, artifact: TreeGenerationArtifact) -> list[str]:
    texts: list[str] = []
    refs = list(
        dict.fromkeys(
            [
                *proposal.source_refs,
                *(
                    ref
                    for item in [*artifact.symptoms, *artifact.tests, *artifact.measures]
                    for ref in item.source_refs
                ),
                *(ref for transition in artifact.transitions for ref in transition.source_refs),
            ]
        )
    )
    for ref in refs:
        path = _path_from_ref(ref)
        if path and path.exists() and path.is_file():
            try:
                texts.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    return texts


def _path_from_ref(ref: str) -> Path | None:
    raw = ref.split(":", 1)[0]
    path = Path(raw)
    if path.exists():
        return path
    candidate = Path.cwd() / raw
    return candidate if candidate.exists() else None


def _artifact_text(proposal: TreeProposal, artifact: TreeGenerationArtifact) -> str:
    values = [
        proposal.candidate_start_symptom,
        *proposal.root_cause_families,
        *proposal.candidate_tests,
        *proposal.candidate_transitions,
    ]
    values.extend(
        str(value)
        for entity in [*artifact.symptoms, *artifact.tests, *artifact.measures]
        for value in [entity.name, entity.description]
        if value
    )
    values.extend(
        str(value)
        for transition in artifact.transitions
        for value in [transition.condition, transition.description]
        if value
    )
    return "\n".join(values)


def _diagnostic_facts(text: str) -> list[str]:
    facts: list[str] = []
    for sentence in _sentences(text):
        if any(marker in sentence for marker in _DIAGNOSTIC_FACT_MARKERS):
            cleaned = sentence.strip()
            if 3 <= len(cleaned) <= 120:
                facts.append(cleaned)
    return list(dict.fromkeys(facts))[:200]


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[。；;！!\n\r]+", text) if item.strip()]


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _compact(value: Any) -> str:
    return "".join(str(value or "").lower().split())


def _name_key(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[\W_]+", "", value.lower())


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator
