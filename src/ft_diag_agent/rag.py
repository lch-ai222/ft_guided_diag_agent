from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from ft_diag_agent.models import EvidenceItem


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    text: str
    source_path: str
    source_type: str
    metadata: dict[str, str]


class DocumentRag:
    def __init__(
        self,
        raw_docs_dir: str | Path,
        chroma_dir: str | Path,
        collection_name: str = "ft_diag_docs",
        chunk_size: int = 900,
        chunk_overlap: int = 120,
    ):
        self.raw_docs_dir = Path(raw_docs_dir)
        self.chroma_dir = Path(chroma_dir)
        self.collection_name = collection_name
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._chunks: list[DocumentChunk] = []

    def scan(self) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        if not self.raw_docs_dir.exists():
            return chunks
        for path in sorted(self.raw_docs_dir.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in {".pdf", ".md", ".txt", ".csv"}:
                continue
            chunks.extend(self._read_file(path))
        self._chunks = chunks
        return chunks

    def build_index(self) -> int:
        chunks = self.scan()
        try:
            import chromadb
        except ImportError:
            return len(chunks)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(self.chroma_dir))
        collection = client.get_or_create_collection(self.collection_name)
        if chunks:
            collection.upsert(
                ids=[chunk.chunk_id for chunk in chunks],
                documents=[chunk.text for chunk in chunks],
                embeddings=[_embed_text(chunk.text) for chunk in chunks],
                metadatas=[
                    {"source_path": chunk.source_path, "source_type": chunk.source_type, **chunk.metadata}
                    for chunk in chunks
                ],
            )
        return len(chunks)

    def search(self, query: str, top_k: int = 5, filters: dict[str, str] | None = None) -> list[EvidenceItem]:
        if not query.strip():
            return []
        chroma_results = self._search_chroma(query, top_k, filters)
        if chroma_results:
            return chroma_results
        if not self._chunks:
            self.scan()
        return self._search_lexical(query, top_k, filters)

    def _search_chroma(self, query: str, top_k: int, filters: dict[str, str] | None = None) -> list[EvidenceItem]:
        try:
            import chromadb
        except ImportError:
            return []
        if not self.chroma_dir.exists():
            return []
        client = chromadb.PersistentClient(path=str(self.chroma_dir))
        collection = client.get_or_create_collection(self.collection_name)
        try:
            kwargs = {"where": filters} if filters else {}
            result = collection.query(query_embeddings=[_embed_text(query)], n_results=top_k, **kwargs)
        except Exception:
            return []
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        ids = result.get("ids", [[]])[0]
        evidence: list[EvidenceItem] = []
        for doc_id, text, metadata in zip(ids, documents, metadatas, strict=False):
            evidence.append(
                EvidenceItem(
                    source_type="RAG",
                    source_id=str(doc_id),
                    claim=text[:300],
                    strength=0.55,
                    source_refs=[metadata.get("source_path", "")],
                    raw_payload={"metadata": metadata, "query": query},
                )
            )
        return evidence

    def _search_lexical(self, query: str, top_k: int, filters: dict[str, str] | None = None) -> list[EvidenceItem]:
        query_terms = {term for term in query.lower().split() if term}
        if not query_terms:
            query_terms = {query.lower()}
        scored: list[tuple[int, DocumentChunk]] = []
        for chunk in self._chunks:
            if filters and any(chunk.metadata.get(key) != value for key, value in filters.items()):
                continue
            haystack = chunk.text.lower()
            score = sum(1 for term in query_terms if term in haystack)
            if query.lower() in haystack:
                score += 3
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            EvidenceItem(
                source_type="RAG",
                source_id=chunk.chunk_id,
                claim=chunk.text[:300],
                strength=min(0.4 + score * 0.1, 0.8),
                source_refs=[chunk.source_path],
                raw_payload={"metadata": chunk.metadata, "query": query, "lexical_score": score},
            )
            for score, chunk in scored[:top_k]
        ]

    def _read_file(self, path: Path) -> list[DocumentChunk]:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            text = self._read_pdf(path)
        elif suffix == ".csv":
            text = self._read_csv(path)
        else:
            text = path.read_text(encoding="utf-8", errors="ignore")
            text = _sanitize_eval_labeled_text(path, text)
        return self._chunk_text(text, path)

    def _read_pdf(self, path: Path) -> str:
        try:
            from pypdf import PdfReader
        except ImportError:
            return ""
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    def _read_csv(self, path: Path) -> str:
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
                row = _sanitize_eval_labeled_csv_row(path, row)
                rows.append(" | ".join(f"{key}: {value}" for key, value in row.items() if key))
        return "\n".join(rows)

    def _chunk_text(self, text: str, path: Path) -> list[DocumentChunk]:
        text = " ".join(text.split())
        if not text:
            return []
        chunks: list[DocumentChunk] = []
        start = 0
        index = 0
        step = max(1, self.chunk_size - self.chunk_overlap)
        while start < len(text):
            chunk_text = text[start : start + self.chunk_size]
            chunks.append(
                DocumentChunk(
                    chunk_id=f"{path.name}:{index}",
                    text=chunk_text,
                    source_path=str(path),
                    source_type=path.suffix.lower().removeprefix("."),
                    metadata={"chunk_index": str(index), **_metadata_for_path(path, chunk_text)},
                )
            )
            index += 1
            start += step
        return chunks


def _metadata_for_path(path: Path, text: str) -> dict[str, str]:
    name = path.name.lower()
    metadata: dict[str, str] = {}
    if "sop" in name:
        metadata["doc_type"] = "SOP"
    elif "fmea" in name:
        metadata["doc_type"] = "FMEA"
    elif "repair_manual" in name:
        metadata["doc_type"] = "REPAIR_MANUAL"
    elif "mock_work_orders" in name:
        metadata["doc_type"] = "WORK_ORDER"
    elif "diagnostic_eval" in name or "eval_cases" in name:
        metadata["doc_type"] = "EVAL_CASE"
    else:
        metadata["doc_type"] = "UNKNOWN"

    if "ft_001" in name or "black_screen" in name:
        metadata["tree_id"] = "FT_001"
        metadata["phenomenon"] = "车机黑屏"
    elif "ft_002" in name or "door_close" in name:
        metadata["tree_id"] = "FT_002"
        metadata["phenomenon"] = "车门无法关闭"

    order = re.search(r"(WO-[A-Z]{2}-\d{6}-\d{3})", text)
    if order:
        metadata["work_order_id"] = order.group(1)
    expected = re.search(r"expected_leaf_symptom_id\s*\|\s*(S\d+)\s*\|", text)
    if expected:
        metadata["expected_leaf_symptom_id"] = expected.group(1)
    return metadata


def _sanitize_eval_labeled_csv_row(path: Path, row: dict[str, str]) -> dict[str, str]:
    if not _is_labeled_eval_path(path):
        return row
    allowed = {
        "case_id",
        "eval_group",
        "vehicle_project",
        "source",
        "severity",
        "failure_type",
        "domain",
        "case_description",
        "observed_evidence",
    }
    return {key: value for key, value in row.items() if key in allowed and value}


def _sanitize_eval_labeled_text(path: Path, text: str) -> str:
    if not _is_labeled_eval_path(path):
        return text
    drop_markers = [
        "expected_",
        "actual_repair_action",
        "repair_validation_result",
        "is_rework",
        "is_prior_misdiagnosis",
        "human_review_conclusion",
        "expected route",
        "expected tree",
        "expected gate",
        "期望",
        "真实闭环",
        "人工复核",
        "处理措施",
    ]
    kept: list[str] = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in drop_markers):
            continue
        kept.append(line)
    return "\n".join(kept)


def _is_labeled_eval_path(path: Path) -> bool:
    path_text = str(path)
    return "diagnostic_eval_labeled_cases_v1" in path_text or path.name.startswith("diagnostic_eval_cases_v1")


def _embed_text(text: str, dims: int = 64) -> list[float]:
    vector = [0.0] * dims
    tokens = [token for token in re.split(r"\W+", text.lower()) if token]
    if not tokens:
        tokens = list(text.lower())
    for token in tokens:
        digest = sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % dims
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vector[index] += sign
    norm = sum(value * value for value in vector) ** 0.5 or 1.0
    return [value / norm for value in vector]
