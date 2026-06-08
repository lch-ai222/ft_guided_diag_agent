from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ft_diag_agent.models import (
    FieldStatus,
    ReplayRecord,
    TreeGenerationArtifact,
    TreeProposal,
    TreeProposalCaseLink,
    TreeProposalEvalResult,
)
from ft_diag_agent.tree_generation import generation_hitl_items, validate_tree_generation_artifact
from ft_diag_agent.tree_proposals import TreeProposalStore

TREE_PROPOSAL_EVAL_SUITE = "tree_proposal_v1"
TREE_PROPOSAL_REPLAY_SHADOW_EVAL_SUITE = "tree_proposal_replay_shadow_v1"
MIN_SHADOW_SUPPORT_RATE = 0.6
MIN_SHADOW_TEST_HIT_RATE = 0.6


def run_tree_proposal_eval(
    store: TreeProposalStore,
    proposal_id: str,
    *,
    persist: bool = True,
) -> TreeProposalEvalResult | None:
    proposal = store.get_proposal(proposal_id)
    if not proposal:
        return None
    artifact = store.load_artifact_snapshot(proposal_id)
    result = evaluate_tree_proposal(proposal, artifact)
    if persist:
        store.append_eval_result(result)
    return result


def run_tree_proposal_replay_shadow_eval(
    store: TreeProposalStore,
    proposal_id: str,
    *,
    records: list[ReplayRecord] | None = None,
    runs_dir: str | Path | None = None,
    persist: bool = True,
) -> TreeProposalEvalResult | None:
    proposal = store.get_proposal(proposal_id)
    if not proposal:
        return None
    artifact = store.load_artifact_snapshot(proposal_id)
    case_links = store.load_case_links(proposal_id)
    replay_records = records if records is not None else _load_replay_records(runs_dir) if runs_dir else []
    result = evaluate_tree_proposal_replay_shadow(
        proposal,
        artifact,
        replay_records,
        case_links=case_links,
    )
    if persist:
        store.append_eval_result(result)
    return result


def evaluate_tree_proposal(
    proposal: TreeProposal,
    artifact: TreeGenerationArtifact | None,
) -> TreeProposalEvalResult:
    if artifact is None:
        return TreeProposalEvalResult(
            proposal_id=proposal.proposal_id,
            eval_suite=TREE_PROPOSAL_EVAL_SUITE,
            status_at_eval=proposal.status,
            metrics={
                "schema_valid": False,
                "artifact_present": False,
                "candidate_ready": False,
            },
            unsafe_findings=["ARTIFACT_MISSING"],
            failure_cases=[{"rule_id": "ARTIFACT_MISSING", "message": "缺少 proposal artifact 快照。"}],
        )

    validation = artifact.validation_report or validate_tree_generation_artifact(artifact)
    hitl_items = generation_hitl_items(artifact)
    roots_with_tests = _roots_with_tests(artifact)
    evidence_binding_rate = _evidence_binding_rate(artifact)
    hitl_confirmed_rate = _hitl_confirmed_rate(artifact)
    missing_test_transition_count = sum(1 for transition in artifact.transitions if not transition.test_ids)
    unsafe_findings = _unsafe_findings(
        validation_error_count=len(validation.errors),
        root_count=validation.root_symptom_count,
        test_count=validation.test_count,
        transition_count=validation.transition_count,
        missing_test_transition_count=missing_test_transition_count,
        hitl_pending_count=len(hitl_items),
        evidence_binding_rate=evidence_binding_rate,
    )
    metrics = {
        "artifact_present": True,
        "schema_valid": validation.is_valid,
        "candidate_ready": not unsafe_findings,
        "validation_error_count": len(validation.errors),
        "validation_warning_count": len(validation.warnings),
        "start_count": validation.start_symptom_count,
        "root_count": validation.root_symptom_count,
        "test_count": validation.test_count,
        "transition_count": validation.transition_count,
        "roots_with_tests_rate": roots_with_tests,
        "missing_test_transition_count": missing_test_transition_count,
        "evidence_binding_rate": evidence_binding_rate,
        "hitl_confirmed_rate": hitl_confirmed_rate,
        "hitl_pending_count": len(hitl_items),
        "hitl_suggestion_count": len(artifact.hitl_suggestions),
        "hitl_decision_count": len(artifact.hitl_decisions),
    }
    failure_cases = [
        {"rule_id": finding, "message": _finding_message(finding)}
        for finding in unsafe_findings
    ]
    return TreeProposalEvalResult(
        proposal_id=proposal.proposal_id,
        eval_suite=TREE_PROPOSAL_EVAL_SUITE,
        status_at_eval=proposal.status,
        metrics=metrics,
        failure_cases=failure_cases,
        unsafe_findings=unsafe_findings,
    )


def evaluate_tree_proposal_replay_shadow(
    proposal: TreeProposal,
    artifact: TreeGenerationArtifact | None,
    records: list[ReplayRecord],
    *,
    case_links: list[TreeProposalCaseLink] | None = None,
) -> TreeProposalEvalResult:
    linked_case_ids = {
        *proposal.source_case_ids,
        *(link.case_id for link in (case_links or [])),
        *(link.work_order_id for link in (case_links or []) if link.work_order_id),
    }
    start_terms = _candidate_terms(
        [
            proposal.candidate_start_symptom,
            proposal.phenomenon_bucket,
            proposal.candidate_failure_domain,
            *[
                item.name or item.description
                for item in (artifact.symptoms if artifact else [])
                if item.level == "start"
            ],
        ]
    )
    root_terms = _candidate_terms(
        [
            *proposal.root_cause_families,
            *[
                item.name or item.description
                for item in (artifact.symptoms if artifact else [])
                if item.level == "root"
            ],
        ]
    )
    test_terms = _candidate_terms(
        [
            *proposal.candidate_tests,
            *[
                item.name or item.description or item.entity_id
                for item in (artifact.tests if artifact else [])
            ],
        ]
    )
    artifact_test_ids = {item.entity_id for item in (artifact.tests if artifact else [])}
    rows: list[dict[str, Any]] = []
    relevant_rows: list[dict[str, Any]] = []
    contradiction_rows: list[dict[str, Any]] = []
    for record in records:
        text = _record_text(record)
        case_id = _record_case_id(record)
        linked = bool(case_id and case_id in linked_case_ids)
        start_hit = _any_term(text, start_terms)
        root_hit = _any_term(text, root_terms)
        test_hit = _any_term(text, test_terms) or bool(artifact_test_ids & _record_test_ids(record))
        evidence_hit = (
            bool(set(proposal.evidence_ids) & _record_evidence_ids(record))
            if proposal.evidence_ids
            else None
        )
        relevant = linked or start_hit or root_hit
        supported = relevant and root_hit and test_hit
        contradiction = relevant and _looks_contradictory(text, root_terms, test_terms)
        row = {
            "case_id": case_id or "UNKNOWN",
            "linked_case": linked,
            "start_hit": start_hit,
            "root_hit": root_hit,
            "test_hit": test_hit,
            "evidence_hit": evidence_hit,
            "relevant": relevant,
            "simulated_shadow_result": (
                "SUPPORTS" if supported else "CONTRADICTS" if contradiction else "NEEDS_EVIDENCE"
            ),
            "gate_status": _record_gate_status(record),
            "reason": _shadow_reason(linked, start_hit, root_hit, test_hit, contradiction),
        }
        rows.append(row)
        if relevant:
            relevant_rows.append(row)
        if contradiction:
            contradiction_rows.append(row)

    relevant_count = len(relevant_rows)
    supported_count = sum(1 for row in relevant_rows if row["simulated_shadow_result"] == "SUPPORTS")
    root_hit_count = sum(1 for row in relevant_rows if row["root_hit"])
    test_hit_count = sum(1 for row in relevant_rows if row["test_hit"])
    evidence_hit_values = [row["evidence_hit"] for row in relevant_rows if row["evidence_hit"] is not None]
    support_rate = _ratio(supported_count, relevant_count)
    test_hit_rate = _ratio(test_hit_count, relevant_count)
    evidence_hit_rate = _ratio(sum(1 for value in evidence_hit_values if value), len(evidence_hit_values))
    unsafe_findings = _shadow_unsafe_findings(
        artifact_present=artifact is not None,
        record_count=len(records),
        relevant_count=relevant_count,
        support_rate=support_rate,
        test_hit_rate=test_hit_rate,
        contradiction_count=len(contradiction_rows),
    )
    metrics = {
        "artifact_present": artifact is not None,
        "replay_record_count": len(records),
        "linked_case_count": len(linked_case_ids),
        "shadow_relevant_case_count": relevant_count,
        "shadow_supported_case_count": supported_count,
        "shadow_support_rate": support_rate,
        "shadow_root_hit_rate": _ratio(root_hit_count, relevant_count),
        "shadow_test_hit_rate": test_hit_rate,
        "shadow_evidence_hit_rate": evidence_hit_rate,
        "shadow_contradiction_count": len(contradiction_rows),
        "shadow_ready": not unsafe_findings,
    }
    failure_cases = [
        row
        for row in relevant_rows
        if row["simulated_shadow_result"] != "SUPPORTS"
    ][:50]
    if not records:
        failure_cases.append(
            {"rule_id": "REPLAY_RECORDS_MISSING", "message": "没有可用于 shadow diagnosis 的 replay。"}
        )
    if artifact is None:
        failure_cases.append({"rule_id": "ARTIFACT_MISSING", "message": "缺少 artifact，无法评估候选树结构。"})
    return TreeProposalEvalResult(
        proposal_id=proposal.proposal_id,
        eval_suite=TREE_PROPOSAL_REPLAY_SHADOW_EVAL_SUITE,
        status_at_eval=proposal.status,
        metrics=metrics,
        failure_cases=failure_cases,
        unsafe_findings=unsafe_findings,
    )


def _roots_with_tests(artifact: TreeGenerationArtifact) -> float | None:
    root_ids = {symptom.entity_id for symptom in artifact.symptoms if symptom.level == "root"}
    if not root_ids:
        return None
    roots_with_tests = {
        transition.target_id
        for transition in artifact.transitions
        if transition.target_id in root_ids and transition.test_ids
    }
    return len(roots_with_tests) / len(root_ids)


def _evidence_binding_rate(artifact: TreeGenerationArtifact) -> float | None:
    entities = [*artifact.symptoms, *artifact.tests, *artifact.measures]
    transitions = artifact.transitions
    total = len(entities) + len(transitions)
    if total == 0:
        return None
    bound = sum(1 for item in entities if item.source_refs or item.chunk_ids)
    bound += sum(1 for item in transitions if item.source_refs or item.chunk_ids)
    return bound / total


def _hitl_confirmed_rate(artifact: TreeGenerationArtifact) -> float | None:
    statuses: list[FieldStatus] = []
    for entity in [*artifact.symptoms, *artifact.tests, *artifact.measures]:
        statuses.extend([entity.name_status, entity.description_status])
    for transition in artifact.transitions:
        statuses.extend([transition.condition_status, transition.description_status])
    review_statuses = [
        status
        for status in statuses
        if status in {FieldStatus.CONFIRMED, FieldStatus.VERIFIED, FieldStatus.EXTRACTED_INFERRED, FieldStatus.MISSING}
    ]
    if not review_statuses:
        return None
    confirmed = sum(1 for status in review_statuses if status in {FieldStatus.CONFIRMED, FieldStatus.VERIFIED})
    return confirmed / len(review_statuses)


def _unsafe_findings(
    *,
    validation_error_count: int,
    root_count: int,
    test_count: int,
    transition_count: int,
    missing_test_transition_count: int,
    hitl_pending_count: int,
    evidence_binding_rate: float | None,
) -> list[str]:
    findings: list[str] = []
    if validation_error_count:
        findings.append("VALIDATION_ERRORS")
    if root_count < 1:
        findings.append("ROOT_MISSING")
    if test_count < 1:
        findings.append("TEST_MISSING")
    if transition_count < 1:
        findings.append("TRANSITION_MISSING")
    if missing_test_transition_count:
        findings.append("TRANSITION_TEST_MISSING")
    if hitl_pending_count:
        findings.append("HITL_PENDING")
    if evidence_binding_rate is None or evidence_binding_rate < 0.5:
        findings.append("EVIDENCE_BINDING_LOW")
    return findings


def _finding_message(finding: str) -> str:
    messages = {
        "VALIDATION_ERRORS": "结构校验仍有 ERROR。",
        "ROOT_MISSING": "缺少 root FailureSymptom。",
        "TEST_MISSING": "缺少 OntologyTest。",
        "TRANSITION_MISSING": "缺少 SymptomTransition。",
        "TRANSITION_TEST_MISSING": "存在未绑定 test 的 transition。",
        "HITL_PENDING": "仍有 EXTRACTED_INFERRED 或 MISSING 字段未确认。",
        "EVIDENCE_BINDING_LOW": "实体/关系证据绑定率低于 50%。",
        "ARTIFACT_MISSING": "缺少 proposal artifact 快照。",
    }
    return messages.get(finding, finding)


def _shadow_unsafe_findings(
    *,
    artifact_present: bool,
    record_count: int,
    relevant_count: int,
    support_rate: float | None,
    test_hit_rate: float | None,
    contradiction_count: int,
) -> list[str]:
    findings: list[str] = []
    if not artifact_present:
        findings.append("ARTIFACT_MISSING")
    if record_count < 1:
        findings.append("REPLAY_RECORDS_MISSING")
    if relevant_count < 1:
        findings.append("SHADOW_RELEVANT_CASES_MISSING")
    if support_rate is None or support_rate < MIN_SHADOW_SUPPORT_RATE:
        findings.append("SHADOW_SUPPORT_RATE_LOW")
    if test_hit_rate is None or test_hit_rate < MIN_SHADOW_TEST_HIT_RATE:
        findings.append("SHADOW_TEST_HIT_RATE_LOW")
    if contradiction_count:
        findings.append("SHADOW_CONTRADICTION_FOUND")
    return findings


def _load_replay_records(runs_dir: str | Path | None) -> list[ReplayRecord]:
    if not runs_dir:
        return []
    records: list[ReplayRecord] = []
    for path in sorted(Path(runs_dir).glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    records.append(ReplayRecord.model_validate_json(line))
    return records


def _record_case_id(record: ReplayRecord) -> str | None:
    return (
        _nested(record.state_after, "case_id")
        or _nested(record.state_after, "work_order", "order_id")
        or _nested(record.state_before, "case_id")
        or _nested(record.state_before, "work_order", "order_id")
    )


def _record_gate_status(record: ReplayRecord) -> str | None:
    return record.gate_result.get("status") or _nested(record.state_after, "gate_result", "status")


def _record_text(record: ReplayRecord) -> str:
    payload = {
        "case_id": _record_case_id(record),
        "work_order": record.state_after.get("work_order") or record.state_before.get("work_order"),
        "classification": record.state_after.get("classification"),
        "coverage_decision": record.state_after.get("coverage_decision"),
        "active_node_id": record.state_after.get("active_node_id"),
        "candidate_causes": record.state_after.get("candidate_causes"),
        "planned_actions": record.planner_output or record.state_after.get("planned_actions"),
        "executed_tests": record.state_after.get("executed_tests"),
        "evidence_chain": record.state_after.get("evidence_chain"),
        "gate_result": record.gate_result or record.state_after.get("gate_result"),
        "human_decision": record.human_decision,
        "final_report": record.state_after.get("final_report"),
    }
    return json.dumps(payload, ensure_ascii=False).lower()


def _record_test_ids(record: ReplayRecord) -> set[str]:
    ids: set[str] = set()
    for item in record.state_after.get("executed_tests") or []:
        if isinstance(item, dict) and item.get("test_id"):
            ids.add(str(item["test_id"]))
    for item in record.planner_output or []:
        if isinstance(item, dict) and item.get("test_id"):
            ids.add(str(item["test_id"]))
    if record.human_decision.get("test_id"):
        ids.add(str(record.human_decision["test_id"]))
    return ids


def _record_evidence_ids(record: ReplayRecord) -> set[str]:
    ids: set[str] = set()
    for item in record.state_after.get("evidence_chain") or []:
        if isinstance(item, dict) and item.get("evidence_id"):
            ids.add(str(item["evidence_id"]))
    if record.human_decision.get("evidence_id"):
        ids.add(str(record.human_decision["evidence_id"]))
    return ids


def _candidate_terms(values: list[Any]) -> list[str]:
    terms: list[str] = []
    for value in values:
        if not value:
            continue
        text = str(value).strip().lower()
        if not text or text == "missing":
            continue
        terms.append(text)
        compact = "".join(text.split())
        if compact != text:
            terms.append(compact)
    return list(dict.fromkeys(terms))


def _any_term(text: str, terms: list[str]) -> bool:
    compact_text = "".join(text.split())
    return any(term in text or term in compact_text for term in terms if term)


def _looks_contradictory(text: str, root_terms: list[str], test_terms: list[str]) -> bool:
    if not (_any_term(text, root_terms) or _any_term(text, test_terms)):
        return False
    markers = [
        "反驳",
        "不支持",
        "误判",
        "无改善",
        "仍复现",
        "无效",
        "排除",
        "正常",
    ]
    return any(marker in text for marker in markers)


def _shadow_reason(
    linked: bool,
    start_hit: bool,
    root_hit: bool,
    test_hit: bool,
    contradiction: bool,
) -> str:
    if contradiction:
        return "replay 中存在反证或无效处置语义，需要专家复核。"
    if root_hit and test_hit:
        return "replay 同时命中候选 root 和候选 test，可作为 shadow 支持样本。"
    missing = []
    if not root_hit:
        missing.append("root")
    if not test_hit:
        missing.append("test")
    prefix = "已关联 source case，但" if linked else "语义相关 replay 中"
    return prefix + "缺少 " + "/".join(missing) + " 命中。"


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
