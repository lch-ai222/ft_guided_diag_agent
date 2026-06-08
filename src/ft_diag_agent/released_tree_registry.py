from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ft_diag_agent.models import (
    ProductionTtlAuditResult,
    ProductionTtlRollbackResult,
    ProductionTtlWritePlan,
    ProductionTtlWriteResult,
    ReleasedTreeRegistryEntry,
    TreeProposal,
    TreeProposalStatus,
    TreeReleaseArtifact,
    utc_now_iso,
)
from ft_diag_agent.tree_generation_eval import TREE_GENERATION_EXTRACTION_EVAL_SUITE

QTL = "http://lianshan.ai/ontology/qlt_fta#"

T = TypeVar("T", bound=BaseModel)


class ReleasedTreeRegistry:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def registry_path(self) -> Path:
        return self.base_dir / "registry.jsonl"

    @property
    def audit_results_path(self) -> Path:
        return self.base_dir / "ttl_audit_results.jsonl"

    @property
    def write_results_path(self) -> Path:
        return self.base_dir / "ttl_write_results.jsonl"

    @property
    def rollback_results_path(self) -> Path:
        return self.base_dir / "ttl_rollback_results.jsonl"

    @property
    def backups_dir(self) -> Path:
        return self.base_dir / "backups"

    def load_entries(self) -> list[ReleasedTreeRegistryEntry]:
        entries = _read_jsonl(self.registry_path, ReleasedTreeRegistryEntry)
        entries.sort(key=lambda item: (item.created_at, item.registry_entry_id), reverse=True)
        return entries

    def load_audit_results(self, proposal_id: str | None = None) -> list[ProductionTtlAuditResult]:
        results = _read_jsonl(self.audit_results_path, ProductionTtlAuditResult)
        if proposal_id:
            results = [item for item in results if item.proposal_id == proposal_id]
        results.sort(key=lambda item: (item.created_at, item.audit_id), reverse=True)
        return results

    def latest_audit_result(self, proposal_id: str) -> ProductionTtlAuditResult | None:
        results = self.load_audit_results(proposal_id)
        return results[0] if results else None

    def load_write_results(self, proposal_id: str | None = None) -> list[ProductionTtlWriteResult]:
        results = _read_jsonl(self.write_results_path, ProductionTtlWriteResult)
        if proposal_id:
            results = [item for item in results if item.proposal_id == proposal_id]
        results.sort(key=lambda item: (item.created_at, item.write_id), reverse=True)
        return results

    def latest_write_result(self, proposal_id: str) -> ProductionTtlWriteResult | None:
        results = self.load_write_results(proposal_id)
        return results[0] if results else None

    def load_rollback_results(self, proposal_id: str | None = None) -> list[ProductionTtlRollbackResult]:
        results = _read_jsonl(self.rollback_results_path, ProductionTtlRollbackResult)
        if proposal_id:
            results = [item for item in results if item.proposal_id == proposal_id]
        results.sort(key=lambda item: (item.created_at, item.rollback_run_id), reverse=True)
        return results

    def latest_rollback_result(self, proposal_id: str) -> ProductionTtlRollbackResult | None:
        results = self.load_rollback_results(proposal_id)
        return results[0] if results else None

    def audit_and_register_ready_entry(
        self,
        proposal: TreeProposal,
        release_artifact: TreeReleaseArtifact | None,
        *,
        production_ttl_path: str | Path | None = None,
    ) -> ProductionTtlAuditResult:
        audit = audit_released_tree_registration(
            proposal,
            release_artifact,
            registry_entries=self.load_entries(),
            production_ttl_path=production_ttl_path,
        )
        self.save_audit_result(audit)
        if audit.registry_entry:
            self.save_entry(audit.registry_entry)
        return audit

    def save_audit_result(self, result: ProductionTtlAuditResult) -> None:
        _append_jsonl(self.audit_results_path, result)

    def save_write_result(self, result: ProductionTtlWriteResult) -> None:
        _append_jsonl(self.write_results_path, result)

    def save_rollback_result(self, result: ProductionTtlRollbackResult) -> None:
        _append_jsonl(self.rollback_results_path, result)

    def save_entry(self, entry: ReleasedTreeRegistryEntry) -> None:
        entries = {
            (item.candidate_tree_id, item.release_version): item
            for item in self.load_entries()
        }
        entries[(entry.candidate_tree_id, entry.release_version)] = entry
        ordered = sorted(entries.values(), key=lambda item: (item.created_at, item.registry_entry_id), reverse=True)
        _write_jsonl(self.registry_path, ordered)

    def execute_production_ttl_write(
        self,
        proposal: TreeProposal,
        release_artifact: TreeReleaseArtifact | None,
        *,
        production_ttl_path: str | Path,
    ) -> ProductionTtlWriteResult:
        blockers: list[str] = []
        warnings: list[str] = []
        metrics: dict[str, object] = {}
        audit = audit_released_tree_registration(
            proposal,
            release_artifact,
            registry_entries=self.load_entries(),
            production_ttl_path=production_ttl_path,
        )
        self.save_audit_result(audit)
        metrics["source_audit_id"] = audit.audit_id
        if audit.blockers:
            blockers.extend(audit.blockers)
        if audit.warnings:
            warnings.extend(audit.warnings)
        if not release_artifact:
            result = _write_result(proposal.proposal_id, blockers, warnings, metrics)
            self.save_write_result(result)
            return result
        entry = self._ready_entry_for_release(proposal.proposal_id, release_artifact)
        if not entry:
            blockers.append("缺少 READY_FOR_TTL_WRITE registry entry；请先运行生产 TTL 写入审计并登记 READY 记录。")
        elif entry.registry_status != "READY_FOR_TTL_WRITE":
            blockers.append(f"registry entry 状态为 {entry.registry_status}，不能执行生产 TTL 写入。")
        generated_ttl = release_artifact.generated_ttl_preview
        generated_hash = sha256(generated_ttl.encode("utf-8")).hexdigest()
        if entry and entry.ttl_sha256 != generated_hash:
            blockers.append("registry entry ttl_sha256 与 release artifact generated TTL 不一致。")
        production_path = Path(production_ttl_path)
        if not production_path.exists():
            blockers.append(f"生产 TTL 文件不存在：{production_path}")
            current_ttl = ""
        else:
            current_ttl = production_path.read_text(encoding="utf-8")
        current_tree_ids, parse_blockers = _ttl_tree_ids_from_text(current_ttl, "生产 TTL")
        blockers.extend(parse_blockers)
        metrics["production_tree_count_before"] = len(current_tree_ids)
        if release_artifact.manifest.candidate_tree_id in current_tree_ids:
            blockers.append(f"生产 TTL 已存在 tree_id={release_artifact.manifest.candidate_tree_id}，不能重复写入。")
        next_ttl = _append_generated_ttl(current_ttl, generated_ttl, release_artifact)
        next_tree_ids, next_parse_blockers = _ttl_tree_ids_from_text(next_ttl, "写入后的生产 TTL")
        blockers.extend(next_parse_blockers)
        metrics["production_tree_count_after"] = len(next_tree_ids)
        if release_artifact.manifest.candidate_tree_id not in next_tree_ids:
            blockers.append("写入后的生产 TTL 未包含 candidate tree_id。")
        if blockers:
            result = _write_result(
                proposal.proposal_id,
                blockers,
                warnings,
                metrics,
                entry=entry,
                release_artifact=release_artifact,
            )
            self.save_write_result(result)
            return result
        assert entry is not None
        backup_path = self._backup_path(entry)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(current_ttl, encoding="utf-8")
        production_path.write_text(next_ttl, encoding="utf-8")
        entry.registry_status = "REGISTERED"
        entry.production_ttl_path = str(production_path)
        self.save_entry(entry)
        plan = ProductionTtlWritePlan(
            proposal_id=proposal.proposal_id,
            registry_entry_id=entry.registry_entry_id,
            release_artifact_id=release_artifact.release_artifact_id,
            release_version=release_artifact.manifest.release_version,
            candidate_tree_id=release_artifact.manifest.candidate_tree_id,
            production_ttl_path=str(production_path),
            backup_path=str(backup_path),
            generated_ttl_sha256=generated_hash,
            current_ttl_sha256=sha256(current_ttl.encode("utf-8")).hexdigest(),
            operation="WRITE",
        )
        metrics["backup_path"] = str(backup_path)
        result = _write_result(
            proposal.proposal_id,
            [],
            warnings,
            metrics,
            entry=entry,
            release_artifact=release_artifact,
            plan=plan,
        )
        self.save_write_result(result)
        return result

    def rollback_production_ttl_write(
        self,
        proposal_id: str,
        *,
        registry_entry_id: str | None = None,
        production_ttl_path: str | Path | None = None,
        dry_run: bool = True,
    ) -> ProductionTtlRollbackResult:
        blockers: list[str] = []
        warnings: list[str] = []
        metrics: dict[str, object] = {}
        entry = self._registered_entry_for_rollback(proposal_id, registry_entry_id=registry_entry_id)
        if not entry:
            blockers.append("缺少可回滚的 REGISTERED registry entry。")
            result = _rollback_result(proposal_id, dry_run, blockers, warnings, metrics)
            self.save_rollback_result(result)
            return result
        write_result = self._latest_registered_write_result(proposal_id, entry.registry_entry_id)
        if not write_result or not write_result.write_plan:
            blockers.append("缺少带 backup_path 的生产 TTL 写入记录，不能执行回滚。")
            result = _rollback_result(proposal_id, dry_run, blockers, warnings, metrics, entry=entry)
            self.save_rollback_result(result)
            return result
        target_path = Path(
            production_ttl_path
            or entry.production_ttl_path
            or write_result.write_plan.production_ttl_path
        )
        backup_path = Path(write_result.write_plan.backup_path)
        if not target_path.exists():
            blockers.append(f"生产 TTL 文件不存在：{target_path}")
            current_ttl = ""
        else:
            current_ttl = target_path.read_text(encoding="utf-8")
        if not backup_path.exists():
            blockers.append(f"回滚备份不存在：{backup_path}")
            backup_ttl = ""
        else:
            backup_ttl = backup_path.read_text(encoding="utf-8")
        current_tree_ids, current_blockers = _ttl_tree_ids_from_text(current_ttl, "当前生产 TTL")
        backup_tree_ids, backup_blockers = _ttl_tree_ids_from_text(backup_ttl, "回滚备份 TTL")
        blockers.extend(current_blockers)
        blockers.extend(backup_blockers)
        metrics.update(
            {
                "production_ttl_path": str(target_path),
                "backup_path": str(backup_path),
                "current_tree_count": len(current_tree_ids),
                "backup_tree_count": len(backup_tree_ids),
                "current_ttl_sha256": sha256(current_ttl.encode("utf-8")).hexdigest() if current_ttl else "",
                "backup_ttl_sha256": sha256(backup_ttl.encode("utf-8")).hexdigest() if backup_ttl else "",
                "candidate_tree_present_before_rollback": entry.candidate_tree_id in current_tree_ids,
                "candidate_tree_present_in_backup": entry.candidate_tree_id in backup_tree_ids,
            }
        )
        if entry.candidate_tree_id not in current_tree_ids:
            warnings.append("当前生产 TTL 未包含 candidate tree_id；仍可用备份恢复 registry 状态。")
        if entry.candidate_tree_id in backup_tree_ids:
            blockers.append("回滚备份已包含 candidate tree_id，不能证明可恢复到写入前状态。")
        if blockers:
            result = _rollback_result(proposal_id, dry_run, blockers, warnings, metrics, entry=entry)
            self.save_rollback_result(result)
            return result
        if dry_run:
            result = _rollback_result(
                proposal_id,
                dry_run,
                [],
                warnings,
                metrics,
                entry=entry,
                restored_from_backup_path=str(backup_path),
            )
            self.save_rollback_result(result)
            return result
        target_path.write_text(backup_ttl, encoding="utf-8")
        entry.registry_status = "ROLLED_BACK"
        self.save_entry(entry)
        result = _rollback_result(
            proposal_id,
            dry_run,
            [],
            warnings,
            metrics,
            entry=entry,
            restored_from_backup_path=str(backup_path),
        )
        self.save_rollback_result(result)
        return result

    def _ready_entry_for_release(
        self,
        proposal_id: str,
        release_artifact: TreeReleaseArtifact,
    ) -> ReleasedTreeRegistryEntry | None:
        for entry in self.load_entries():
            if (
                entry.proposal_id == proposal_id
                and entry.release_artifact_id == release_artifact.release_artifact_id
                and entry.release_version == release_artifact.manifest.release_version
                and entry.candidate_tree_id == release_artifact.manifest.candidate_tree_id
                and entry.registry_status == "READY_FOR_TTL_WRITE"
            ):
                return entry
        return None

    def _registered_entry_for_rollback(
        self,
        proposal_id: str,
        *,
        registry_entry_id: str | None,
    ) -> ReleasedTreeRegistryEntry | None:
        entries = [
            entry
            for entry in self.load_entries()
            if entry.proposal_id == proposal_id and entry.registry_status == "REGISTERED"
        ]
        if registry_entry_id:
            entries = [entry for entry in entries if entry.registry_entry_id == registry_entry_id]
        return entries[0] if entries else None

    def _latest_registered_write_result(
        self,
        proposal_id: str,
        registry_entry_id: str,
    ) -> ProductionTtlWriteResult | None:
        for result in self.load_write_results(proposal_id):
            if result.verdict == "REGISTERED" and result.registry_entry_id == registry_entry_id:
                return result
        return None

    def _backup_path(self, entry: ReleasedTreeRegistryEntry) -> Path:
        timestamp = _safe_filename(utc_now_iso())
        filename = f"{_safe_filename(entry.candidate_tree_id)}_{_safe_filename(entry.release_version)}_{timestamp}.ttl"
        return self.backups_dir / filename


def audit_released_tree_registration(
    proposal: TreeProposal,
    release_artifact: TreeReleaseArtifact | None,
    *,
    registry_entries: list[ReleasedTreeRegistryEntry] | None = None,
    production_ttl_path: str | Path | None = None,
) -> ProductionTtlAuditResult:
    registry_entries = registry_entries or []
    blockers: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, object] = {}

    if proposal.status != TreeProposalStatus.RELEASED_TREE:
        blockers.append("proposal 尚未处于 RELEASED_TREE，不能进入生产 TTL 写入队列。")
    if release_artifact is None:
        blockers.append("缺少 release artifact。")
        return _audit_result(proposal, None, blockers, warnings, metrics)
    if release_artifact.proposal_id != proposal.proposal_id:
        blockers.append("release artifact 与 proposal_id 不匹配。")
    if not release_artifact.release_materials_ready:
        blockers.append("release artifact 仍存在材料阻塞项。")
    if release_artifact.blockers:
        blockers.extend(f"发布材料阻塞：{item}" for item in release_artifact.blockers)
    if not release_artifact.manifest.formal_signoff_reviewer:
        blockers.append("缺少专家正式发布签核。")
    if not release_artifact.rollback.rollback_steps:
        blockers.append("缺少 rollback steps。")
    if TREE_GENERATION_EXTRACTION_EVAL_SUITE not in release_artifact.source_eval_suites:
        blockers.append("release artifact 未消费 tree_generation_extraction_v1。")
    warnings.extend(release_artifact.warnings)

    candidate_tree_id = release_artifact.manifest.candidate_tree_id
    generated_ttl = release_artifact.generated_ttl_preview
    ttl_hash = sha256(generated_ttl.encode("utf-8")).hexdigest() if generated_ttl else ""
    metrics["ttl_sha256"] = ttl_hash
    ttl_blockers, ttl_warnings, ttl_metrics = _audit_generated_ttl(generated_ttl, candidate_tree_id)
    blockers.extend(ttl_blockers)
    warnings.extend(ttl_warnings)
    metrics.update(ttl_metrics)

    duplicate_registry = [
        item
        for item in registry_entries
        if item.candidate_tree_id == candidate_tree_id
        and (
            item.proposal_id != proposal.proposal_id
            or item.release_version != release_artifact.manifest.release_version
        )
        and item.registry_status != "ROLLED_BACK"
    ]
    if duplicate_registry:
        blockers.append(f"Released Tree registry 已存在 candidate_tree_id={candidate_tree_id} 的其他有效记录。")

    production_tree_ids = (
        _production_tree_ids(production_ttl_path, blockers, warnings)
        if production_ttl_path
        else set()
    )
    if candidate_tree_id in production_tree_ids:
        blockers.append(f"生产 TTL 已存在 tree_id={candidate_tree_id}，不能重复写入。")
    metrics["production_tree_count"] = len(production_tree_ids)

    entry = None
    if not blockers:
        entry = ReleasedTreeRegistryEntry(
            proposal_id=proposal.proposal_id,
            release_artifact_id=release_artifact.release_artifact_id,
            release_version=release_artifact.manifest.release_version,
            candidate_tree_id=candidate_tree_id,
            candidate_start_symptom=proposal.candidate_start_symptom,
            applicable_scope=release_artifact.manifest.applicable_scope,
            ttl_sha256=ttl_hash,
            ttl_preview_path=f"artifacts/{proposal.proposal_id}/release/generated_ttl_preview.ttl",
            production_ttl_path=str(production_ttl_path) if production_ttl_path else None,
            source_eval_ids=release_artifact.source_eval_ids,
            source_review_ids=release_artifact.source_review_ids,
            manifest_id=release_artifact.manifest.manifest_id,
            rollback_id=release_artifact.rollback.rollback_id,
        )
    return _audit_result(proposal, release_artifact, blockers, warnings, metrics, entry)


def _audit_result(
    proposal: TreeProposal,
    release_artifact: TreeReleaseArtifact | None,
    blockers: list[str],
    warnings: list[str],
    metrics: dict[str, object],
    entry: ReleasedTreeRegistryEntry | None = None,
) -> ProductionTtlAuditResult:
    return ProductionTtlAuditResult(
        proposal_id=proposal.proposal_id,
        release_version=release_artifact.manifest.release_version if release_artifact else None,
        candidate_tree_id=release_artifact.manifest.candidate_tree_id if release_artifact else None,
        verdict="BLOCKED" if blockers else "READY_FOR_TTL_WRITE",
        blockers=list(dict.fromkeys(blockers)),
        warnings=list(dict.fromkeys(warnings)),
        metrics=metrics,
        registry_entry=entry,
    )


def _write_result(
    proposal_id: str,
    blockers: list[str],
    warnings: list[str],
    metrics: dict[str, object],
    *,
    entry: ReleasedTreeRegistryEntry | None = None,
    release_artifact: TreeReleaseArtifact | None = None,
    plan: ProductionTtlWritePlan | None = None,
) -> ProductionTtlWriteResult:
    return ProductionTtlWriteResult(
        proposal_id=proposal_id,
        registry_entry_id=entry.registry_entry_id if entry else None,
        release_version=release_artifact.manifest.release_version if release_artifact else None,
        candidate_tree_id=release_artifact.manifest.candidate_tree_id if release_artifact else None,
        verdict="BLOCKED" if blockers else "REGISTERED",
        blockers=list(dict.fromkeys(blockers)),
        warnings=list(dict.fromkeys(warnings)),
        metrics=metrics,
        write_plan=plan,
    )


def _rollback_result(
    proposal_id: str,
    dry_run: bool,
    blockers: list[str],
    warnings: list[str],
    metrics: dict[str, object],
    *,
    entry: ReleasedTreeRegistryEntry | None = None,
    restored_from_backup_path: str | None = None,
) -> ProductionTtlRollbackResult:
    if blockers:
        verdict = "BLOCKED"
    else:
        verdict = "ROLLBACK_READY" if dry_run else "ROLLED_BACK"
    return ProductionTtlRollbackResult(
        proposal_id=proposal_id,
        registry_entry_id=entry.registry_entry_id if entry else None,
        release_version=entry.release_version if entry else None,
        candidate_tree_id=entry.candidate_tree_id if entry else None,
        verdict=verdict,
        dry_run=dry_run,
        blockers=list(dict.fromkeys(blockers)),
        warnings=list(dict.fromkeys(warnings)),
        metrics=metrics,
        restored_from_backup_path=restored_from_backup_path,
    )


def _audit_generated_ttl(
    generated_ttl: str,
    candidate_tree_id: str,
) -> tuple[list[str], list[str], dict[str, object]]:
    blockers: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, object] = {}
    if not generated_ttl.strip():
        return ["缺少 generated TTL preview。"], warnings, metrics
    try:
        from rdflib import Graph, Namespace
        from rdflib.namespace import RDF
    except ImportError:
        return ["缺少 rdflib，无法执行 TTL parse 审计。"], warnings, metrics

    graph = Graph()
    try:
        graph.parse(data=generated_ttl, format="turtle")
    except Exception as exc:
        return [f"generated TTL preview 不是合法 Turtle：{exc}"], warnings, metrics

    qtl = Namespace(QTL)
    fault_tree_subjects = list(graph.subjects(RDF.type, qtl.FaultTree))
    tree_ids = [str(value) for subject in fault_tree_subjects for value in graph.objects(subject, qtl.treeId)]
    metrics["ttl_fault_tree_count"] = len(fault_tree_subjects)
    metrics["ttl_tree_ids"] = tree_ids
    if candidate_tree_id not in tree_ids:
        blockers.append(f"generated TTL preview 缺少 treeId={candidate_tree_id}。")

    target_subjects = [
        subject
        for subject in fault_tree_subjects
        if any(str(value) == candidate_tree_id for value in graph.objects(subject, qtl.treeId))
    ]
    if not target_subjects:
        return blockers, warnings, metrics

    symptom_refs = list(graph.objects(target_subjects[0], qtl.hasSymptom))
    transitions = list(graph.subjects(RDF.type, qtl.SymptomTransition))
    tests = list(graph.subjects(RDF.type, qtl.OntologyTest))
    symptoms = list(graph.subjects(RDF.type, qtl.FailureSymptom))
    metrics.update(
        {
            "ttl_symptom_count": len(symptoms),
            "ttl_test_count": len(tests),
            "ttl_transition_count": len(transitions),
            "ttl_tree_symptom_ref_count": len(symptom_refs),
        }
    )
    if not symptom_refs:
        blockers.append("generated TTL preview 中 FaultTree 缺少 qtl:hasSymptom。")
    start_count = sum(1 for subject in symptoms if str(_one(graph.objects(subject, qtl.symptomLevel)) or "") == "start")
    root_count = sum(1 for subject in symptoms if str(_one(graph.objects(subject, qtl.symptomLevel)) or "") == "root")
    metrics["ttl_start_symptom_count"] = start_count
    metrics["ttl_root_symptom_count"] = root_count
    if start_count == 0:
        blockers.append("generated TTL preview 缺少 start FailureSymptom。")
    if root_count == 0:
        blockers.append("generated TTL preview 缺少 root FailureSymptom。")
    for transition in transitions:
        missing = [
            name
            for name, predicate in (
                ("transitionSource", qtl.transitionSource),
                ("transitionTarget", qtl.transitionTarget),
                ("testId", qtl.testId),
            )
            if _one(graph.objects(transition, predicate)) is None
        ]
        if missing:
            blockers.append(f"SymptomTransition {transition} 缺少 {', '.join(missing)}。")
    if not transitions:
        warnings.append("generated TTL preview 缺少 SymptomTransition；发布前建议确认是否为单节点树。")
    return blockers, warnings, metrics


def _append_generated_ttl(
    current_ttl: str,
    generated_ttl: str,
    release_artifact: TreeReleaseArtifact,
) -> str:
    prefix = current_ttl.rstrip()
    release_comment = "\n".join(
        [
            "",
            "",
            f"# Released Tree write: proposal={release_artifact.proposal_id}",
            f"# Release version: {release_artifact.manifest.release_version}",
            f"# Release artifact: {release_artifact.release_artifact_id}",
            generated_ttl.strip(),
            "",
        ]
    )
    return prefix + release_comment if prefix else release_comment.lstrip()


def _ttl_tree_ids_from_text(ttl_text: str, label: str) -> tuple[set[str], list[str]]:
    if not ttl_text.strip():
        return set(), [f"{label} 为空，无法执行生产 TTL 写入/回滚。"]
    try:
        from rdflib import Graph, Namespace
        from rdflib.namespace import RDF
    except ImportError:
        return set(), ["缺少 rdflib，无法解析 TTL。"]
    graph = Graph()
    try:
        graph.parse(data=ttl_text, format="turtle")
    except Exception as exc:
        return set(), [f"{label} 无法解析：{exc}"]
    qtl = Namespace(QTL)
    return {
        str(value)
        for subject in graph.subjects(RDF.type, qtl.FaultTree)
        for value in graph.objects(subject, qtl.treeId)
    }, []


def _production_tree_ids(
    production_ttl_path: str | Path | None,
    blockers: list[str],
    warnings: list[str],
) -> set[str]:
    if not production_ttl_path:
        return set()
    path = Path(production_ttl_path)
    if not path.exists():
        warnings.append(f"生产 TTL 文件不存在：{path}")
        return set()
    try:
        from rdflib import Graph, Namespace
        from rdflib.namespace import RDF
    except ImportError:
        blockers.append("缺少 rdflib，无法读取生产 TTL。")
        return set()
    graph = Graph()
    try:
        graph.parse(path, format="turtle")
    except Exception as exc:
        blockers.append(f"生产 TTL 无法解析：{exc}")
        return set()
    qtl = Namespace(QTL)
    return {
        str(value)
        for subject in graph.subjects(RDF.type, qtl.FaultTree)
        for value in graph.objects(subject, qtl.treeId)
    }


def _one(values):
    return next(iter(values), None)


def _safe_filename(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))
    return text.strip("_") or "unnamed"


def _read_jsonl(path: Path, model: type[T]) -> list[T]:
    if not path.exists():
        return []
    items: list[T] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(model.model_validate_json(line))
    return items


def _append_jsonl(path: Path, item: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(item.model_dump_json(ensure_ascii=False) + "\n")


def _write_jsonl(path: Path, items: list[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(item.model_dump_json(ensure_ascii=False) + "\n" for item in items),
        encoding="utf-8",
    )
