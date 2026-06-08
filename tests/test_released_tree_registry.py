from ft_diag_agent.fault_tree import RdfFaultTreeRepository
from ft_diag_agent.models import TreeProposalStatus
from ft_diag_agent.released_tree_registry import ReleasedTreeRegistry, audit_released_tree_registration
from ft_diag_agent.tree_release import build_tree_release_artifact
from tests.test_tree_proposal_eval import _valid_artifact
from tests.test_tree_proposal_precheck import _proposal
from tests.test_tree_release import _gray_review_log, _passed_evals


def _ready_release_artifact(proposal, artifact):
    return build_tree_release_artifact(
        proposal,
        artifact,
        eval_results=_passed_evals(proposal, artifact),
        review_logs=[_gray_review_log(proposal)],
        generated_by="release_owner",
        formal_signoff_reviewer="release_expert",
        formal_signoff_rationale="结构、shadow eval 和回滚材料已复核。",
        release_version="v20260606-test",
    )


def _released_proposal():
    proposal = _proposal()
    proposal.status = TreeProposalStatus.GRAY_TREE
    proposal.candidate_failure_domain = "车身"
    return proposal


def test_release_ttl_preview_is_runtime_parseable(tmp_path) -> None:
    proposal = _released_proposal()
    artifact = _valid_artifact()
    release_artifact = _ready_release_artifact(proposal, artifact)
    ttl_path = tmp_path / "release_preview.ttl"
    ttl_path.write_text(release_artifact.generated_ttl_preview, encoding="utf-8")

    repository = RdfFaultTreeRepository(ttl_path)

    assert release_artifact.manifest.candidate_tree_id in repository.trees
    assert repository.enumerate_paths(release_artifact.manifest.candidate_tree_id)


def test_registry_blocks_non_released_proposal(tmp_path) -> None:
    proposal = _released_proposal()
    artifact = _valid_artifact()
    release_artifact = _ready_release_artifact(proposal, artifact)

    audit = audit_released_tree_registration(proposal, release_artifact)

    assert audit.verdict == "BLOCKED"
    assert "proposal 尚未处于 RELEASED_TREE，不能进入生产 TTL 写入队列。" in audit.blockers
    assert audit.registry_entry is None


def test_registry_writes_ready_entry_for_released_proposal(tmp_path) -> None:
    proposal = _released_proposal()
    artifact = _valid_artifact()
    release_artifact = _ready_release_artifact(proposal, artifact)
    proposal.status = TreeProposalStatus.RELEASED_TREE
    production_ttl = tmp_path / "production.ttl"
    production_ttl.write_text("@prefix qtl: <http://lianshan.ai/ontology/qlt_fta#> .\n", encoding="utf-8")
    registry = ReleasedTreeRegistry(tmp_path / "released_trees")

    audit = registry.audit_and_register_ready_entry(
        proposal,
        release_artifact,
        production_ttl_path=production_ttl,
    )

    assert audit.verdict == "READY_FOR_TTL_WRITE"
    assert audit.registry_entry is not None
    assert audit.blockers == []
    entries = registry.load_entries()
    assert len(entries) == 1
    assert entries[0].candidate_tree_id == release_artifact.manifest.candidate_tree_id
    assert registry.latest_audit_result(proposal.proposal_id).verdict == "READY_FOR_TTL_WRITE"


def test_registry_blocks_release_artifact_without_extraction_suite(tmp_path) -> None:
    proposal = _released_proposal()
    artifact = _valid_artifact()
    release_artifact = _ready_release_artifact(proposal, artifact)
    proposal.status = TreeProposalStatus.RELEASED_TREE
    release_artifact.source_eval_suites = []

    audit = audit_released_tree_registration(proposal, release_artifact)

    assert audit.verdict == "BLOCKED"
    assert "release artifact 未消费 tree_generation_extraction_v1。" in audit.blockers


def test_registry_blocks_invalid_generated_ttl(tmp_path) -> None:
    proposal = _released_proposal()
    artifact = _valid_artifact()
    release_artifact = _ready_release_artifact(proposal, artifact)
    proposal.status = TreeProposalStatus.RELEASED_TREE
    release_artifact.generated_ttl_preview = "not valid turtle"

    audit = audit_released_tree_registration(proposal, release_artifact)

    assert audit.verdict == "BLOCKED"
    assert any("generated TTL preview 不是合法 Turtle" in item for item in audit.blockers)
    assert audit.registry_entry is None


def test_registry_blocks_duplicate_tree_id(tmp_path) -> None:
    proposal = _released_proposal()
    artifact = _valid_artifact()
    release_artifact = _ready_release_artifact(proposal, artifact)
    proposal.status = TreeProposalStatus.RELEASED_TREE
    registry = ReleasedTreeRegistry(tmp_path / "released_trees")
    first = registry.audit_and_register_ready_entry(proposal, release_artifact)
    assert first.registry_entry is not None

    other = _released_proposal()
    other.proposal_id = "TP-OTHER"
    other.status = TreeProposalStatus.RELEASED_TREE
    second = audit_released_tree_registration(
        other,
        release_artifact,
        registry_entries=registry.load_entries(),
    )

    assert second.verdict == "BLOCKED"
    assert any("Released Tree registry 已存在 candidate_tree_id" in item for item in second.blockers)


def test_production_ttl_write_blocks_without_ready_entry(tmp_path) -> None:
    proposal = _released_proposal()
    artifact = _valid_artifact()
    release_artifact = _ready_release_artifact(proposal, artifact)
    proposal.status = TreeProposalStatus.RELEASED_TREE
    production_ttl = tmp_path / "production.ttl"
    production_ttl.write_text("@prefix qtl: <http://lianshan.ai/ontology/qlt_fta#> .\n", encoding="utf-8")
    registry = ReleasedTreeRegistry(tmp_path / "released_trees")

    result = registry.execute_production_ttl_write(
        proposal,
        release_artifact,
        production_ttl_path=production_ttl,
    )

    assert result.verdict == "BLOCKED"
    assert any("缺少 READY_FOR_TTL_WRITE registry entry" in item for item in result.blockers)
    assert release_artifact.manifest.candidate_tree_id not in production_ttl.read_text(encoding="utf-8")


def test_production_ttl_write_registers_tree_after_ready_entry(tmp_path) -> None:
    proposal = _released_proposal()
    artifact = _valid_artifact()
    release_artifact = _ready_release_artifact(proposal, artifact)
    proposal.status = TreeProposalStatus.RELEASED_TREE
    production_ttl = tmp_path / "production.ttl"
    original_ttl = "@prefix qtl: <http://lianshan.ai/ontology/qlt_fta#> .\n"
    production_ttl.write_text(original_ttl, encoding="utf-8")
    registry = ReleasedTreeRegistry(tmp_path / "released_trees")
    ready = registry.audit_and_register_ready_entry(
        proposal,
        release_artifact,
        production_ttl_path=production_ttl,
    )
    assert ready.verdict == "READY_FOR_TTL_WRITE"

    result = registry.execute_production_ttl_write(
        proposal,
        release_artifact,
        production_ttl_path=production_ttl,
    )

    assert result.verdict == "REGISTERED"
    assert result.write_plan is not None
    assert (tmp_path / "released_trees" / "backups").exists()
    assert result.write_plan.backup_path
    assert release_artifact.manifest.candidate_tree_id in production_ttl.read_text(encoding="utf-8")
    entries = registry.load_entries()
    assert entries[0].registry_status == "REGISTERED"
    assert registry.latest_write_result(proposal.proposal_id).verdict == "REGISTERED"
    assert registry.load_write_results(proposal.proposal_id)[0].write_plan.current_ttl_sha256


def test_production_ttl_write_blocks_hash_mismatch(tmp_path) -> None:
    proposal = _released_proposal()
    artifact = _valid_artifact()
    release_artifact = _ready_release_artifact(proposal, artifact)
    proposal.status = TreeProposalStatus.RELEASED_TREE
    production_ttl = tmp_path / "production.ttl"
    production_ttl.write_text("@prefix qtl: <http://lianshan.ai/ontology/qlt_fta#> .\n", encoding="utf-8")
    registry = ReleasedTreeRegistry(tmp_path / "released_trees")
    ready = registry.audit_and_register_ready_entry(
        proposal,
        release_artifact,
        production_ttl_path=production_ttl,
    )
    assert ready.verdict == "READY_FOR_TTL_WRITE"
    release_artifact.generated_ttl_preview = release_artifact.generated_ttl_preview.replace(
        "锁扣位置偏差",
        "锁扣位置偏差-改动",
        1,
    )

    result = registry.execute_production_ttl_write(
        proposal,
        release_artifact,
        production_ttl_path=production_ttl,
    )

    assert result.verdict == "BLOCKED"
    assert "registry entry ttl_sha256 与 release artifact generated TTL 不一致。" in result.blockers
    assert registry.load_entries()[0].registry_status == "READY_FOR_TTL_WRITE"


def test_production_ttl_rollback_dry_run_and_execute(tmp_path) -> None:
    proposal = _released_proposal()
    artifact = _valid_artifact()
    release_artifact = _ready_release_artifact(proposal, artifact)
    proposal.status = TreeProposalStatus.RELEASED_TREE
    production_ttl = tmp_path / "production.ttl"
    production_ttl.write_text("@prefix qtl: <http://lianshan.ai/ontology/qlt_fta#> .\n", encoding="utf-8")
    registry = ReleasedTreeRegistry(tmp_path / "released_trees")
    registry.audit_and_register_ready_entry(
        proposal,
        release_artifact,
        production_ttl_path=production_ttl,
    )
    write = registry.execute_production_ttl_write(
        proposal,
        release_artifact,
        production_ttl_path=production_ttl,
    )
    assert write.verdict == "REGISTERED"
    written_text = production_ttl.read_text(encoding="utf-8")
    assert release_artifact.manifest.candidate_tree_id in written_text

    dry_run = registry.rollback_production_ttl_write(
        proposal.proposal_id,
        production_ttl_path=production_ttl,
        dry_run=True,
    )

    assert dry_run.verdict == "ROLLBACK_READY"
    assert release_artifact.manifest.candidate_tree_id in production_ttl.read_text(encoding="utf-8")
    assert registry.load_entries()[0].registry_status == "REGISTERED"

    rollback = registry.rollback_production_ttl_write(
        proposal.proposal_id,
        production_ttl_path=production_ttl,
        dry_run=False,
    )

    assert rollback.verdict == "ROLLED_BACK"
    assert release_artifact.manifest.candidate_tree_id not in production_ttl.read_text(encoding="utf-8")
    assert registry.load_entries()[0].registry_status == "ROLLED_BACK"
    assert registry.latest_rollback_result(proposal.proposal_id).verdict == "ROLLED_BACK"
