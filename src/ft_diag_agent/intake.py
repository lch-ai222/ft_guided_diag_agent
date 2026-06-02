from __future__ import annotations

import os

from ft_diag_agent.models import IntakeRequest, NormalizedPhenomenon
from ft_diag_agent.settings import Settings


def normalize_intake(request: IntakeRequest, settings: Settings | None = None) -> NormalizedPhenomenon:
    settings = settings or Settings()
    if settings.openai_enable_llm and os.getenv("OPENAI_API_KEY"):
        try:
            return _normalize_with_openai(request, settings)
        except Exception as exc:  # pragma: no cover - network/API dependent
            fallback = _normalize_rules(request)
            fallback.quality_notes.append(f"LLM normalization failed, used rules: {exc}")
            return fallback
    return _normalize_rules(request)


def _normalize_rules(request: IntakeRequest) -> NormalizedPhenomenon:
    raw = request.raw_input.strip()
    phenomenon = raw
    prefixes = ["故障现象:", "故障现象：", "现象:", "现象："]
    for prefix in prefixes:
        if phenomenon.startswith(prefix):
            phenomenon = phenomenon.removeprefix(prefix).strip()
    return NormalizedPhenomenon(
        phenomenon=phenomenon,
        aliases=[raw] if raw != phenomenon else [],
        vehicle_info={
            "project": request.vehicle_project,
            "VINs": request.vin_list,
            "factory": request.factory,
            "station": request.station,
        },
        context={
            "timestamp": request.timestamp,
            **request.extra_context,
        },
        quality_notes=[] if phenomenon else ["raw_input is empty after normalization"],
        llm_used=False,
    )


def _normalize_with_openai(request: IntakeRequest, settings: Settings) -> NormalizedPhenomenon:
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model=settings.openai_model, temperature=0)
    structured = llm.with_structured_output(NormalizedPhenomenon)
    result = structured.invoke(
        [
            (
                "system",
                "You normalize manufacturing quality diagnostic intake into a strict schema. "
                "Preserve safety uncertainty in quality_notes.",
            ),
            ("human", request.model_dump_json(ensure_ascii=False)),
        ]
    )
    result.llm_used = True
    return result
