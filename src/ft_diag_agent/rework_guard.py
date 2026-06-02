from __future__ import annotations

from ft_diag_agent.models import DiagnosticState, ReworkRiskAssessment, SimilarReworkCase
from ft_diag_agent.rag import DocumentRag


class ReworkGuard:
    def assess(self, state: DiagnosticState, rag: DocumentRag | None = None) -> ReworkRiskAssessment:
        text = _state_text(state)
        if not text:
            return ReworkRiskAssessment()

        snippets: list[str] = []
        prior_actions: list[str] = []
        ineffective: list[str] = []
        avoid: list[str] = []
        checks: list[str] = []
        notes: list[str] = []
        similar_cases = _similar_rework_cases(state, rag) if _history_search_enabled(text) else []

        is_rework = _has_any(text, ["返修", "维修后", "复修", "再次进店", "仍复现", "无改善"])
        prior_misdiagnosis = False

        if _has_any(text, ["更换屏幕无效", "替换显示模组无改善", "更换显示模组无改善"]):
            prior_misdiagnosis = True
            prior_actions.append("前次按显示屏/显示模组方向处置")
            ineffective.append("更换屏幕或替换显示模组后无改善")
            avoid.append("避免仅按黑屏现象重复更换显示屏/显示模组")
            checks.extend(["测量主板关键电源/阻抗", "复核主机主板短路或供电网络异常"])
            snippets.extend(_snippets(text, ["更换屏幕无效", "替换显示模组无改善", "主板"]))

        if _has_any(text, ["先调整锁扣后返修", "调整锁扣后仍复现", "调整锁扣无效"]):
            prior_misdiagnosis = True
            prior_actions.append("前次按锁扣偏移方向调整")
            ineffective.append("调整锁扣后仍复现或返修")
            avoid.append("避免在锁扣复测合格时重复调整锁扣")
            checks.extend(["检查密封条压缩量和局部干涉", "复核铰链/门框/密封条反证项"])
            snippets.extend(_snippets(text, ["调整锁扣", "仍复现", "返修", "密封条"]))

        if _has_any(text, ["前次误判", "误判为", "无效"]):
            prior_misdiagnosis = True
            snippets.extend(_snippets(text, ["前次误判", "误判为", "无效"]))

        if prior_misdiagnosis:
            notes.append("识别到前次处置无效或疑似误判，需要先做反证检查，避免重复更换/调整。")
        elif is_rework:
            notes.append("识别到返修/维修后复现线索，需要复核前次处置闭环和未覆盖分支。")
        elif similar_cases:
            notes.append("检索到历史相似返修/处置无效案例，建议优先执行反证检查后再发布根因。")

        for case in similar_cases:
            if _has_any(case.summary, ["更换屏幕", "替换显示模组", "显示模组无改善"]):
                avoid.append("避免仅按黑屏现象重复更换显示屏/显示模组")
                checks.extend(["测量主板关键电源/阻抗", "复核主机主板短路或供电网络异常"])
            if _has_any(case.summary, ["调整锁扣", "锁扣后仍复现"]):
                avoid.append("避免在锁扣复测合格时重复调整锁扣")
                checks.extend(["检查密封条压缩量和局部干涉", "复核铰链/门框/密封条反证项"])

        confidence = 0.0
        if prior_misdiagnosis:
            confidence = 0.86
        elif is_rework:
            confidence = 0.62
        elif similar_cases:
            confidence = 0.42

        return ReworkRiskAssessment(
            is_rework_suspected=is_rework or prior_misdiagnosis or bool(similar_cases),
            is_prior_misdiagnosis_suspected=prior_misdiagnosis,
            confidence=confidence,
            prior_actions=list(dict.fromkeys(prior_actions)),
            ineffective_actions=list(dict.fromkeys(ineffective)),
            avoided_repeat_actions=list(dict.fromkeys(avoid)),
            recommended_checks=list(dict.fromkeys(checks)),
            similar_cases=similar_cases,
            evidence_snippets=list(dict.fromkeys(snippets)),
            risk_notes=notes,
        )


def _state_text(state: DiagnosticState) -> str:
    if not state.work_order:
        return ""
    parts = [
        state.work_order.failure_phenomenon,
        state.work_order.title or "",
        state.work_order.business_domain or "",
        state.work_order.description or "",
        state.work_order.station_or_scene or "",
        *state.work_order.executed_checks,
    ]
    return "\n".join(part for part in parts if part)


def _has_any(text: str, markers: list[str]) -> bool:
    return any(marker in text for marker in markers)


def _snippets(text: str, markers: list[str]) -> list[str]:
    pieces = [piece.strip() for piece in text.replace("；", "\n").replace("。", "\n").splitlines()]
    return [piece for piece in pieces if any(marker in piece for marker in markers)][:5]


def _similar_rework_cases(state: DiagnosticState, rag: DocumentRag | None) -> list[SimilarReworkCase]:
    if not rag or not state.work_order:
        return []
    query = _history_query(state)
    evidence = rag.search(query, top_k=8, filters={"doc_type": "EVAL_CASE"})
    if not evidence:
        evidence = rag.search(query, top_k=8, filters={"doc_type": "WORK_ORDER"})
    cases: list[SimilarReworkCase] = []
    for item in evidence:
        if state.case_id in item.source_id or state.case_id in item.claim:
            continue
        signal = _similarity_signal(item.claim)
        if not signal:
            continue
        cases.append(
            SimilarReworkCase(
                source_id=item.source_id,
                summary=item.claim[:180],
                similarity_signal=signal,
                evidence_id=item.evidence_id,
                source_refs=item.source_refs,
            )
        )
        if len(cases) >= 3:
            break
    return cases


def _history_query(state: DiagnosticState) -> str:
    order = state.work_order
    if not order:
        return ""
    parts = [
        order.failure_phenomenon,
        order.business_domain or "",
        order.description or "",
        " ".join(order.executed_checks[:3]),
        "返修 无改善 仍复现 前次 误判",
    ]
    return " ".join(part for part in parts if part)


def _similarity_signal(text: str) -> str | None:
    if _has_any(text, ["无改善", "仍复现", "返修", "前次", "误判"]):
        return "历史案例含返修/前次处置无效信号"
    if _has_any(text, ["更换屏幕", "替换显示模组", "调整锁扣"]):
        return "历史案例含高风险重复处置动作"
    return None


def _history_search_enabled(text: str) -> bool:
    return _has_any(
        text,
        [
            "维修站",
            "维修后",
            "售后",
            "返修",
            "再次",
            "仍",
            "复现",
            "无效",
            "无改善",
            "更换",
            "替换",
            "调整",
            "准备",
        ],
    )
