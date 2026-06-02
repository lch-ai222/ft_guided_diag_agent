from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    fault_tree_ttl_path: Path = Path(os.getenv("FAULT_TREE_TTL_PATH", "corrected_fault_tree_instances.ttl"))
    raw_docs_dir: Path = Path(os.getenv("RAW_DOCS_DIR", "data/raw_docs"))
    chroma_dir: Path = Path(os.getenv("CHROMA_DIR", "data/chroma"))
    runs_dir: Path = Path(os.getenv("RUNS_DIR", "runs"))
    datasets_dir: Path = Path(os.getenv("DATASETS_DIR", "datasets"))
    tree_generation_dir: Path = Path(os.getenv("TREE_GENERATION_DIR", "data/tree_generation"))
    tree_proposals_dir: Path = Path(os.getenv("TREE_PROPOSALS_DIR", "data/tree_proposals"))
    rag_collection_name: str = os.getenv("RAG_COLLECTION_NAME", "ft_diag_docs")
    rag_chunk_size: int = int(os.getenv("RAG_CHUNK_SIZE", "900"))
    rag_chunk_overlap: int = int(os.getenv("RAG_CHUNK_OVERLAP", "120"))
    llm_provider: str = os.getenv("LLM_PROVIDER", "deepseek")
    llm_enable: bool = _bool_env("LLM_ENABLE", _bool_env("OPENAI_ENABLE_LLM", False))
    output_language: str = os.getenv("OUTPUT_LANGUAGE", "zh-CN")
    diagnosis_mode: str = os.getenv("DIAGNOSIS_MODE", "PRODUCTION")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model_fast: str = os.getenv(
        "DEEPSEEK_MODEL_FAST",
        os.getenv("DEEPSEEK_FLASH_MODEL", "deepseek-v4-flash"),
    )
    deepseek_model_pro: str = os.getenv(
        "DEEPSEEK_MODEL_PRO",
        os.getenv("DEEPSEEK_PRO_MODEL", "deepseek-v4-pro"),
    )
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
    openai_enable_llm: bool = _bool_env("OPENAI_ENABLE_LLM", False)


def get_settings() -> Settings:
    return Settings()
