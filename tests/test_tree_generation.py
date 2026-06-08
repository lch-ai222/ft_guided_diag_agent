from pathlib import Path

from ft_diag_agent.models import (
    EvidenceItem,
    FieldStatus,
    OntologyEntityDraft,
    OntologyEntityType,
    SymptomTransitionDraft,
    TreeGenerationArtifact,
    TreeGenerationHitlDecision,
    TreeGenerationHitlSuggestion,
    TreeGenerationHitlSuggestionOption,
    TreeGenerationInputDocument,
    TreeGenerationJob,
    TreeGenerationJobStatus,
    TreeGenerationQuality,
    TreeProposal,
    TreeProposalStatus,
)
from ft_diag_agent.settings import Settings
from ft_diag_agent.tree_generation import (
    BatchTreeGenerationService,
    LlmCandidateAttempt,
    LlmCandidateExtraction,
    LlmOntologyDraftGraph,
    _artifact_from_candidates,
    _artifact_preview,
    _llm_build_graph,
    _llm_extract_candidates,
    _llm_graph_to_artifact,
    generation_hitl_items,
    preserve_hitl_candidates_after_repair,
    render_tree_generation_mermaid,
    validate_tree_generation_artifact,
)
from ft_diag_agent.tree_generation_eval import TREE_GENERATION_EXTRACTION_EVAL_SUITE


def test_tree_generation_validation_reports_actionable_graph_issues() -> None:
    artifact = TreeGenerationArtifact(
        job_id="TGJ-TEST",
        symptoms=[
            OntologyEntityDraft(
                entity_id="S_START",
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name="动力受限",
                level="start",
            ),
            OntologyEntityDraft(
                entity_id="S_ROOT",
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name="高压互锁接触不良",
                level="root",
            ),
        ],
        transitions=[
            SymptomTransitionDraft(
                transition_id="TR_BAD",
                source_id="S_ROOT",
                target_id="S_START",
                test_ids=[],
            )
        ],
    )

    report = validate_tree_generation_artifact(artifact)
    rule_ids = {issue.rule_id for issue in report.issues}

    assert not report.is_valid
    assert "TRANSITION_TEST_MISSING" in rule_ids
    assert "START_HAS_INCOMING" in rule_ids
    assert "ROOT_HAS_OUTGOING" in rule_ids
    assert all(issue.repair_hint for issue in report.issues)


def test_batch_tree_generation_creates_draft_tree_proposal(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    report = docs / "quality_8d_power_limit.md"
    report.write_text(
        """
# 8D 报告：车辆动力受限

故障现象：仪表提示动力受限，请安全停车，SOC 正常但扭矩请求被限制。
业务域：动力系统

原因分析：BMS 单体压差瞬时升高导致 VCU 扭矩降额。
故障原因：高压互锁回路接触不良导致间歇性降功率。

检查项：读取 VCU/BMS DTC 和冻结帧。
检查项：检查高压互锁状态和连接器端子接触阻抗。
检测：复核加速踏板开度与电机扭矩请求一致性。

处置措施：修复高压互锁连接器端子并复测。
整改：更新 BMS 单体压差监控阈值标定。
""",
        encoding="utf-8",
    )
    settings = Settings(
        tree_generation_dir=tmp_path / "tree_generation",
        tree_proposals_dir=tmp_path / "tree_proposals",
        llm_enable=False,
    )
    service = BatchTreeGenerationService(settings)

    job = service.run_batch_job(title="动力受限候选树", source_paths=[report], use_llm=False)

    assert job.status == TreeGenerationJobStatus.COMPLETED
    assert job.artifact
    assert job.artifact.extraction_quality == TreeGenerationQuality.LOW_CONF_DEBUG_DRAFT
    assert job.artifact.extraction_passes[0].pass_type == "RULE_LOW_CONF_DEBUG_FALLBACK"
    assert job.artifact.validation_report
    assert job.artifact.validation_report.is_valid
    assert job.artifact.validation_report.issues == []
    assert job.artifact.rebuilt_fault_tree["build_method"] == "deterministic_bfs_preview"
    assert job.proposal
    assert job.proposal.status == TreeProposalStatus.DRAFT_TREE
    assert "规则低置信" in job.proposal.confidence_summary
    assert job.proposal.candidate_start_symptom.startswith("仪表提示动力受限")
    assert job.proposal.root_cause_families
    assert job.proposal.candidate_tests
    assert (tmp_path / "tree_generation" / "jobs" / f"{job.job_id}.json").exists()
    assert (tmp_path / "tree_generation" / "artifacts" / job.job_id / "artifact.json").exists()
    assert (tmp_path / "tree_proposals" / "proposals.jsonl").exists()
    eval_results = (tmp_path / "tree_proposals" / "eval_results.jsonl").read_text(encoding="utf-8")
    assert TREE_GENERATION_EXTRACTION_EVAL_SUITE in eval_results
    assert job.artifact.stage_timings
    assert {item["stage_id"] for item in job.artifact.stage_timings} >= {"copy_inputs", "read_chunks", "fallback"}


def test_batch_tree_generation_surfaces_llm_failure_reason(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    report = tmp_path / "quality_8d_power_limit.md"
    report.write_text(
        """
# 8D 报告：车辆动力受限

故障现象：仪表提示动力受限。
原因分析：BMS 单体压差瞬时升高。
检查项：读取 VCU/BMS DTC。
处置措施：复测高压互锁连接器。
""",
        encoding="utf-8",
    )
    settings = Settings(
        tree_generation_dir=tmp_path / "tree_generation",
        tree_proposals_dir=tmp_path / "tree_proposals",
        llm_enable=True,
        llm_provider="deepseek",
    )
    service = BatchTreeGenerationService(settings)

    job = service.run_batch_job(title="动力受限候选树", source_paths=[report], use_llm=True)

    assert job.status == TreeGenerationJobStatus.COMPLETED
    assert job.artifact
    assert job.artifact.extraction_quality == TreeGenerationQuality.LOW_CONF_DEBUG_DRAFT
    fallback = job.artifact.extraction_passes[0]
    assert fallback.pass_type == "RULE_LOW_CONF_DEBUG_FALLBACK"
    assert "DEEPSEEK_API_KEY 未配置" in fallback.summary


def test_load_jobs_orders_by_update_time(tmp_path: Path) -> None:
    service = BatchTreeGenerationService(
        Settings(tree_generation_dir=tmp_path / "tree_generation", tree_proposals_dir=tmp_path / "tree_proposals")
    )
    newer = TreeGenerationJob(
        job_id="TGJ-11111111",
        title="newer",
        created_at="2026-06-01T10:00:00+00:00",
        updated_at="2026-06-01T10:10:00+00:00",
    )
    older = TreeGenerationJob(
        job_id="TGJ-ffffffff",
        title="older",
        created_at="2026-06-01T09:00:00+00:00",
        updated_at="2026-06-01T09:10:00+00:00",
    )

    service.save_job(older)
    service.save_job(newer)

    assert [job.job_id for job in service.load_jobs()] == ["TGJ-11111111", "TGJ-ffffffff"]


def test_llm_graph_adapter_accepts_tree_gen_agent_style_fields() -> None:
    graph = LlmOntologyDraftGraph.model_validate(
        {
            "extraction_summary": "构建充电口盖诊断图",
            "failure_symptoms": [
                {
                    "id": "S001",
                    "symptom_name": "充电口盖无法开启",
                    "symptom_level": "start",
                    "symptom_name_status": "SUGGESTED_GROUNDED",
                    "symptom_desc": "用户无法打开充电口盖",
                    "symptom_chunk_ids": ["doc.md:0"],
                },
                {
                    "id": "S002",
                    "symptom_name": "充电口盖执行器卡滞",
                    "symptom_level": "root",
                    "symptom_desc": "执行器机械卡滞导致无法释放",
                    "symptom_chunk_ids": ["doc.md:0"],
                },
            ],
            "ontology_tests": [
                {
                    "id": "T001",
                    "test_name": "执行充电口盖开闭动作测试",
                    "test_target": "确认执行器是否响应",
                    "test_rule": "无动作或卡滞则支持执行器异常",
                    "test_chunk_ids": ["doc.md:0"],
                }
            ],
            "symptom_transitions": [
                {
                    "source": "S001",
                    "target": "S002",
                    "test_id": ["T001"],
                    "condition": "执行器无响应或卡滞",
                    "transition_chunk_ids": ["doc.md:0"],
                }
            ],
        }
    )
    artifact = _llm_graph_to_artifact(
        "TGJ-ADAPTER",
        graph,
        [{"chunk_id": "doc.md:0", "source_path": "doc.md", "text": "充电口盖执行器卡滞"}],
    )
    report = validate_tree_generation_artifact(artifact)

    assert report.is_valid
    assert artifact.symptoms[0].name == "充电口盖无法开启"
    assert artifact.symptoms[0].name_status == "EXTRACTED_INFERRED"
    assert artifact.transitions[0].source_id == "DRAFT_S_START_001"
    assert artifact.transitions[0].target_id == "DRAFT_S_ROOT_002"
    assert artifact.transitions[0].test_ids == ["DRAFT_T_001"]


def test_llm_graph_adapter_preserves_inner_diagnostic_layer() -> None:
    graph = LlmOntologyDraftGraph.model_validate(
        {
            "extraction_summary": "构建异响诊断图",
            "symptoms": [
                {
                    "name": "右后门颠簸路异响",
                    "level": "start",
                    "description": "入口异响现象",
                    "name_status": "EXTRACTED_EXPLICIT",
                    "description_status": "EXTRACTED_EXPLICIT",
                    "evidence_refs": ["rattle.md:0"],
                },
                {
                    "name": "右后门门锁预紧不足",
                    "level": "inner",
                    "description": "检查后得到的中间异常状态",
                    "name_status": "EXTRACTED_INFERRED",
                    "description_status": "EXTRACTED_INFERRED",
                    "evidence_refs": ["rattle.md:0"],
                },
                {
                    "name": "右后门锁扣安装位置偏差",
                    "level": "root",
                    "description": "可直接调整的终止根因",
                    "name_status": "EXTRACTED_EXPLICIT",
                    "description_status": "EXTRACTED_EXPLICIT",
                    "evidence_refs": ["rattle.md:0"],
                },
            ],
            "tests": [
                {
                    "name": "门锁预紧状况检查",
                    "target": "确认门锁预紧是否不足",
                    "rule": "预紧不足则进入锁扣位置测量",
                    "name_status": "EXTRACTED_INFERRED",
                    "evidence_refs": ["rattle.md:0"],
                },
                {
                    "name": "锁扣位置测量",
                    "target": "确认锁扣 Z/Y 向是否超差",
                    "rule": "锁扣位置超差则支持锁扣安装位置偏差",
                    "name_status": "EXTRACTED_EXPLICIT",
                    "evidence_refs": ["rattle.md:0"],
                },
            ],
            "transitions": [
                {
                    "source_name": "右后门颠簸路异响",
                    "target_name": "右后门门锁预紧不足",
                    "test_names": ["门锁预紧状况检查"],
                    "condition": "检查发现门锁预紧不足",
                    "condition_status": "EXTRACTED_INFERRED",
                    "evidence_refs": ["rattle.md:0"],
                },
                {
                    "source_name": "右后门门锁预紧不足",
                    "target_name": "右后门锁扣安装位置偏差",
                    "test_names": ["锁扣位置测量"],
                    "condition": "锁扣 Z/Y 向位置超差",
                    "condition_status": "EXTRACTED_EXPLICIT",
                    "evidence_refs": ["rattle.md:0"],
                },
            ],
        }
    )
    artifact = _llm_graph_to_artifact(
        "TGJ-RATTLE",
        graph,
        [{"chunk_id": "rattle.md:0", "source_path": "rattle.md", "text": "右后门颠簸路异响"}],
    )
    report = validate_tree_generation_artifact(artifact)

    assert report.is_valid
    assert any(item.name == "右后门门锁预紧不足" and item.level == "inner" for item in artifact.symptoms)
    assert artifact.transitions[0].source_id == "DRAFT_S_START_001"
    assert artifact.transitions[0].target_id == "DRAFT_S_INNER_002"
    assert artifact.transitions[1].source_id == "DRAFT_S_INNER_002"
    assert artifact.transitions[1].target_id == "DRAFT_S_ROOT_003"


def test_llm_build_graph_uses_separate_leveling_and_transition_passes(monkeypatch) -> None:
    calls: list[str] = []

    class FakeProvider:
        def __init__(self, _settings):
            self.last_error = None
            self.last_model = "fake-pro"
            self.last_payload = None
            self.last_raw_content = None

        def json_completion(self, *, user_prompt, response_model, **_kwargs):
            calls.append(user_prompt)
            if "PASS_2_LEVELING" in user_prompt:
                payload = {
                    "extraction_summary": "完成实体分级",
                    "candidate_failure_domain": "右后门颠簸路异响",
                    "symptoms": [
                        {"name": "右后门颠簸路异响", "level": "start", "name_status": "EXTRACTED_EXPLICIT"},
                        {"name": "右后门门锁预紧不足", "level": "inner", "name_status": "EXTRACTED_INFERRED"},
                        {"name": "右后门锁扣安装位置偏差", "level": "root", "name_status": "EXTRACTED_EXPLICIT"},
                    ],
                    "tests": [{"name": "锁扣位置测量", "name_status": "EXTRACTED_EXPLICIT"}],
                    "transitions": [],
                }
            else:
                payload = {
                    "extraction_summary": "完成 transition 绑定",
                    "candidate_failure_domain": "右后门颠簸路异响",
                    "symptoms": [
                        {"name": "右后门颠簸路异响", "level": "start", "name_status": "EXTRACTED_EXPLICIT"},
                        {"name": "右后门门锁预紧不足", "level": "inner", "name_status": "EXTRACTED_INFERRED"},
                        {"name": "右后门锁扣安装位置偏差", "level": "root", "name_status": "EXTRACTED_EXPLICIT"},
                    ],
                    "tests": [{"name": "锁扣位置测量", "name_status": "EXTRACTED_EXPLICIT"}],
                    "transitions": [
                        {
                            "source_name": "右后门颠簸路异响",
                            "target_name": "右后门门锁预紧不足",
                            "test_names": ["锁扣位置测量"],
                        },
                        {
                            "source_name": "右后门门锁预紧不足",
                            "target_name": "右后门锁扣安装位置偏差",
                            "test_names": ["锁扣位置测量"],
                        },
                    ],
                }
            self.last_payload = payload
            self.last_raw_content = str(payload)
            return response_model.model_validate(payload)

    monkeypatch.setattr("ft_diag_agent.tree_generation.LlmProvider", FakeProvider)

    attempt = _llm_build_graph(
        Settings(llm_enable=True, llm_provider="deepseek"),
        "异响test",
        None,
        [{"chunk_id": "rattle.md:0", "source_path": "rattle.md", "text": "右后门颠簸路异响"}],
        LlmCandidateExtraction.model_validate(
            {
                "symptom_candidates": [
                    {"name": "右后门颠簸路异响", "suggested_level": "start"},
                    {"name": "右后门门锁预紧不足", "suggested_level": "inner"},
                    {"name": "右后门锁扣安装位置偏差", "suggested_level": "root"},
                ],
                "test_candidates": [{"name": "锁扣位置测量"}],
            }
        ),
    )

    assert attempt.graph
    assert len(calls) == 2
    assert "PASS_2_LEVELING" in calls[0]
    assert "PASS_3_TRANSITION_BINDING" in calls[1]
    assert any(item.level == "inner" for item in attempt.graph.symptoms)


def test_previews_keep_status_and_generation_hitl_uses_artifact_status() -> None:
    artifact = TreeGenerationArtifact(
        job_id="TGJ-HITL",
        symptoms=[
            OntologyEntityDraft(
                entity_id="S_START",
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name="右后门颠簸路异响",
                name_status="EXTRACTED_EXPLICIT",
                level="start",
                description="入口现象",
                description_status="EXTRACTED_EXPLICIT",
            ),
            OntologyEntityDraft(
                entity_id="S_INNER",
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name="右后门门锁预紧不足",
                name_status="EXTRACTED_INFERRED",
                level="inner",
                description=None,
                description_status="MISSING",
            ),
        ],
        tests=[
            OntologyEntityDraft(
                entity_id="T_LOCK",
                entity_type=OntologyEntityType.ONTOLOGY_TEST,
                name="锁扣位置测量",
                name_status="EXTRACTED_INFERRED",
                description="测量锁扣 Z/Y 向位置",
                description_status="EXTRACTED_INFERRED",
            )
        ],
    )
    preview = _artifact_preview(artifact)
    hitl_items = generation_hitl_items(artifact)

    assert preview["symptoms"][0]["name_status"] == "EXTRACTED_EXPLICIT"
    assert preview["symptoms"][1]["description_status"] == "MISSING"
    assert preview["tests"][0]["name_status"] == "EXTRACTED_INFERRED"
    assert {item["field"] for item in hitl_items} >= {"name", "description"}
    assert any(item["object_id"] == "S_INNER" and item["status"] == "MISSING" for item in hitl_items)


def test_generate_hitl_suggestions_uses_source_and_rag_context(tmp_path: Path, monkeypatch) -> None:
    report = tmp_path / "rattle.md"
    report.write_text("右后门颠簸路异响，检查发现锁扣位置偏差，需测量锁扣 Z/Y 向位置。", encoding="utf-8")
    service = BatchTreeGenerationService(
        Settings(
            tree_generation_dir=tmp_path / "tree_generation",
            tree_proposals_dir=tmp_path / "tree_proposals",
            llm_enable=True,
            llm_provider="deepseek",
        )
    )
    artifact = TreeGenerationArtifact(
        job_id="TGJ-HITL-SUG",
        symptoms=[
            OntologyEntityDraft(
                entity_id="S_START",
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name="右后门颠簸路异响",
                name_status=FieldStatus.EXTRACTED_EXPLICIT,
                level="start",
            ),
            OntologyEntityDraft(
                entity_id="S_ROOT",
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name="锁扣位置偏差",
                name_status=FieldStatus.EXTRACTED_INFERRED,
                level="root",
                description=None,
                description_status=FieldStatus.MISSING,
                source_refs=["rattle.md:0"],
            ),
        ],
    )
    job = TreeGenerationJob(
        job_id="TGJ-HITL-SUG",
        title="异响 HITL",
        input_documents=[
            TreeGenerationInputDocument(
                source_path=str(report),
                filename=report.name,
                chunk_ids=["rattle.md:0"],
            )
        ],
        artifact=artifact,
    )
    service.save_job(job)

    prompts: list[str] = []

    class FakeProvider:
        def __init__(self, settings):
            self.last_error = None
            self.last_model = "fake-pro"
            self.last_payload = None
            self.last_raw_content = None

        def json_completion(self, *, system_prompt, user_prompt, response_model, complexity="fast", max_tokens=1200):
            prompts.append(user_prompt)
            payload = {
                "summary": "基于原文和 RAG 建议补全锁扣偏差说明。",
                "options": [
                    {
                        "value": "右后门锁扣 Z/Y 向安装位置偏差导致锁止预紧不足。",
                        "status": "SUGGESTED_GROUNDED",
                        "rationale": "原文提到锁扣位置偏差，RAG 补充锁止预紧诊断口径。",
                        "confidence": 0.82,
                        "source_refs": ["rattle.md:0"],
                        "rag_refs": ["sop.md"],
                    }
                ],
            }
            self.last_payload = payload
            self.last_raw_content = str(payload)
            return response_model.model_validate(payload)

    class FakeRag:
        def search(self, query: str, top_k: int = 5, filters=None):
            return [
                EvidenceItem(
                    source_type="RAG",
                    source_id="sop.md:0",
                    claim="锁扣位置偏差需测量 Z/Y 向并评估锁止预紧。",
                    source_refs=["sop.md"],
                )
            ]

    monkeypatch.setattr("ft_diag_agent.tree_generation.LlmProvider", FakeProvider)

    updated = service.generate_hitl_suggestions("TGJ-HITL-SUG", rag=FakeRag())

    assert updated and updated.artifact
    suggestion = updated.artifact.hitl_suggestions[0]
    assert suggestion.options[0].status == FieldStatus.SUGGESTED_GROUNDED
    assert "锁扣 Z/Y 向安装位置偏差" in suggestion.options[0].value
    assert "右后门颠簸路异响" in prompts[0]
    assert "锁止预紧" in prompts[0]


def test_apply_hitl_decision_confirms_field_and_refreshes_preview(tmp_path: Path) -> None:
    service = BatchTreeGenerationService(
        Settings(tree_generation_dir=tmp_path / "tree_generation", tree_proposals_dir=tmp_path / "tree_proposals")
    )
    option = TreeGenerationHitlSuggestionOption(
        value="右后门锁扣 Z/Y 向安装位置偏差导致锁止预紧不足。",
        status=FieldStatus.SUGGESTED_GROUNDED,
        rationale="原文和 RAG 均支持。",
        confidence=0.8,
    )
    suggestion = TreeGenerationHitlSuggestion(
        suggestion_id="TGHS-DESC",
        object_type=OntologyEntityType.FAILURE_SYMPTOM.value,
        object_id="S_ROOT",
        field="description",
        current_status=FieldStatus.MISSING,
        current_value=None,
        reason="缺少描述",
        options=[option],
        recommended_option_id=option.option_id,
    )
    artifact = TreeGenerationArtifact(
        job_id="TGJ-HITL-APPLY",
        symptoms=[
            OntologyEntityDraft(
                entity_id="S_START",
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name="右后门颠簸路异响",
                name_status=FieldStatus.EXTRACTED_EXPLICIT,
                level="start",
            ),
            OntologyEntityDraft(
                entity_id="S_ROOT",
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name="锁扣位置偏差",
                name_status=FieldStatus.EXTRACTED_INFERRED,
                level="root",
                description_status=FieldStatus.MISSING,
            ),
        ],
        tests=[
            OntologyEntityDraft(
                entity_id="T_LOCK",
                entity_type=OntologyEntityType.ONTOLOGY_TEST,
                name="锁扣位置测量",
            )
        ],
        transitions=[
            SymptomTransitionDraft(
                transition_id="TR_LOCK",
                source_id="S_START",
                target_id="S_ROOT",
                test_ids=["T_LOCK"],
            )
        ],
        hitl_suggestions=[suggestion],
    )
    proposal = TreeProposal(
        proposal_id="TP-HITL-APPLY",
        source_job_id="TGJ-HITL-APPLY",
        phenomenon_bucket="右后门颠簸路异响",
        candidate_start_symptom="右后门颠簸路异响",
    )
    job = TreeGenerationJob(job_id="TGJ-HITL-APPLY", title="HITL apply", artifact=artifact, proposal=proposal)
    service.save_job(job)
    service.append_proposal(proposal)
    before_hitl_count = len(generation_hitl_items(artifact))
    decision = TreeGenerationHitlDecision(
        suggestion_id="TGHS-DESC",
        object_type=OntologyEntityType.FAILURE_SYMPTOM.value,
        object_id="S_ROOT",
        field="description",
        action="ACCEPT_OPTION",
        selected_option_id=option.option_id,
    )

    updated = service.apply_hitl_decision("TGJ-HITL-APPLY", decision)

    assert updated and updated.artifact
    root = next(item for item in updated.artifact.symptoms if item.entity_id == "S_ROOT")
    assert root.description_status == FieldStatus.CONFIRMED
    assert "锁止预紧不足" in (root.description or "")
    assert updated.artifact.hitl_decisions[0].decision_id == decision.decision_id
    assert all(item.suggestion_id != "TGHS-DESC" for item in updated.artifact.hitl_suggestions)
    assert len(generation_hitl_items(updated.artifact)) < before_hitl_count
    assert updated.artifact.validation_report
    assert updated.artifact.rebuilt_fault_tree["build_method"] == "deterministic_bfs_preview"
    logs = service.proposal_store.load_review_logs("TP-HITL-APPLY")
    assert logs and logs[0].decision == "APPROVE"
    assert "树生成 HITL 确认字段" in logs[0].rationale


def test_pass4_preserves_inferred_candidates_for_hitl() -> None:
    before = TreeGenerationArtifact(
        job_id="TGJ-PRESERVE",
        symptoms=[
            OntologyEntityDraft(
                entity_id="S_START",
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name="右后门颠簸路异响",
                name_status="EXTRACTED_EXPLICIT",
                level="start",
            ),
            OntologyEntityDraft(
                entity_id="S_INFERRED",
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name="右后门密封接触异常",
                name_status="EXTRACTED_INFERRED",
                level="inner",
                description_status="EXTRACTED_INFERRED",
            ),
        ],
        tests=[
            OntologyEntityDraft(
                entity_id="T_INFERRED",
                entity_type=OntologyEntityType.ONTOLOGY_TEST,
                name="密封条检查",
                name_status="EXTRACTED_INFERRED",
                description_status="EXTRACTED_INFERRED",
            )
        ],
        transitions=[
            SymptomTransitionDraft(
                transition_id="TR_INFERRED",
                source_id="S_START",
                target_id="S_INFERRED",
                test_ids=["T_INFERRED"],
                condition="检查发现密封接触异常",
                condition_status="EXTRACTED_INFERRED",
                description_status="EXTRACTED_INFERRED",
            )
        ],
    )
    repaired = TreeGenerationArtifact(
        job_id="TGJ-PRESERVE",
        symptoms=[
            OntologyEntityDraft(
                entity_id="S_START",
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name="右后门颠簸路异响",
                name_status="EXTRACTED_EXPLICIT",
                level="start",
            )
        ],
        tests=[],
        transitions=[],
    )

    restored = preserve_hitl_candidates_after_repair(before, repaired)
    hitl_items = generation_hitl_items(repaired)

    assert restored == 3
    assert any(item.name == "右后门密封接触异常" for item in repaired.symptoms)
    assert any(item.name == "密封条检查" for item in repaired.tests)
    assert repaired.transitions and repaired.transitions[0].target_id == "S_INFERRED"
    assert any(item["object_id"] == "S_INFERRED" for item in hitl_items)


def test_render_tree_generation_mermaid_includes_tests_on_edges() -> None:
    artifact = TreeGenerationArtifact(
        job_id="TGJ-VIZ",
        symptoms=[
            OntologyEntityDraft(
                entity_id="S_START",
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name="右后门颠簸路异响",
                level="start",
            ),
            OntologyEntityDraft(
                entity_id="S_ROOT",
                entity_type=OntologyEntityType.FAILURE_SYMPTOM,
                name="锁扣位置偏差",
                level="root",
            ),
        ],
        tests=[
            OntologyEntityDraft(
                entity_id="T_LOCK",
                entity_type=OntologyEntityType.ONTOLOGY_TEST,
                name="锁扣位置测量",
            )
        ],
        transitions=[
            SymptomTransitionDraft(
                transition_id="TR_LOCK",
                source_id="S_START",
                target_id="S_ROOT",
                test_ids=["T_LOCK"],
            )
        ],
    )

    mermaid = render_tree_generation_mermaid(artifact)

    assert mermaid.startswith("```mermaid")
    assert "右后门颠簸路异响" in mermaid
    assert "锁扣位置偏差" in mermaid
    assert '-->|"锁扣位置测量"|' in mermaid


def test_candidate_adapter_accepts_broad_llm_shapes_and_assembles_charge_lid_tree() -> None:
    candidates = LlmCandidateExtraction.model_validate(
        {
            "extraction_summary": "充电口盖问题候选抽取",
            "phenomena": ["充电口盖无法弹开"],
            "root_causes": [
                "执行器低温推力下降",
                "口盖上边缘间隙偏小",
                "密封圈湿态吸附/摩擦增加",
                "执行器弹出行程余量不足",
            ],
            "checks": [
                {"check_name": "读取 BCM 日志和执行器驱动信号", "test_target": "排查控制信号异常"},
                {"check_name": "执行器低温推力测试", "test_target": "确认低温推力是否低于标准"},
                {"check_name": "充电口盖间隙测量", "test_target": "确认上边缘间隙和面差"},
                {"check_name": "密封圈摩擦与水膜吸附检查", "test_target": "确认湿态摩擦是否增大"},
                {"check_name": "执行器弹出行程检查", "test_target": "确认弹出行程余量"},
            ],
            "permanent_actions": [
                "将充电口盖面差控制目标收严",
                "供应商评估执行器弹出行程余量提升方案",
            ],
            "causal_chains": [
                "充电口盖无法弹开 -> 执行器低温推力下降",
                "充电口盖无法弹开 -> 口盖上边缘间隙偏小",
            ],
        }
    )
    artifact = _artifact_from_candidates(
        "TGJ-CHARGE",
        candidates,
        [{"chunk_id": "charge.md:0", "source_path": "charge.md", "text": "充电口盖无法弹开"}],
    )
    report = validate_tree_generation_artifact(artifact)
    root_names = {item.name for item in artifact.symptoms if item.level == "root"}
    test_names = {item.name for item in artifact.tests if item.name}

    assert report.is_valid
    assert any(item.name == "充电口盖无法弹开" and item.level == "start" for item in artifact.symptoms)
    assert "执行器低温推力下降" in root_names
    assert "口盖上边缘间隙偏小" in root_names
    assert "密封圈湿态吸附/摩擦增加" in root_names
    assert "执行器低温推力测试" in test_names
    assert "充电口盖间隙测量" in test_names
    assert artifact.transitions
    assert all(item.test_ids for item in artifact.transitions)
    assert_no_extraction_suggested_statuses(artifact)


def test_rule_fallback_does_not_extract_task_metadata(tmp_path: Path) -> None:
    report = tmp_path / "charge_lid.md"
    report.write_text(
        """
# 充电口盖资料

现场记录：低温洗车后，车辆充电口盖偶发无法弹开。
原因分析：执行器低温推力不足，口盖上边缘间隙偏小会增加复现概率。
检查项：执行器低温推力测试。
处置措施：优化执行器弹出行程余量。
""",
        encoding="utf-8",
    )
    settings = Settings(
        tree_generation_dir=tmp_path / "tree_generation",
        tree_proposals_dir=tmp_path / "tree_proposals",
        llm_enable=False,
    )
    service = BatchTreeGenerationService(settings)

    job = service.run_batch_job(
        title="充电口盖test5",
        description="请从质量报告/8D/维修资料中抽取入口现象、根因族、检查项、处置措施和诊断转移。",
        source_paths=[report],
        use_llm=False,
    )

    assert job.proposal
    serialized = job.proposal.model_dump_json(ensure_ascii=False)
    assert "充电口盖test5" not in serialized
    assert "请从质量报告/8D/维修资料中抽取入口现象" not in serialized
    assert job.proposal.candidate_start_symptom.startswith("MISSING 占位入口现象")
    assert job.artifact
    assert job.artifact.symptoms[0].name_status == "MISSING"


def test_llm_raw_payload_is_persisted_when_candidate_adapter_is_empty(tmp_path: Path, monkeypatch) -> None:
    report = tmp_path / "charge_lid.md"
    report.write_text("现场记录：充电口盖无法弹开。", encoding="utf-8")
    raw_payload = {
        "summary": "模型返回了非适配字段",
        "entry_observation_text": "充电口盖无法弹开",
        "root_causes": [],
    }

    def fake_extract_candidates(*_args, **_kwargs):
        return LlmCandidateAttempt(
            candidates=LlmCandidateExtraction.model_validate(raw_payload),
            error=None,
            model="deepseek-v4-pro",
            raw_payload=raw_payload,
            raw_text='{"summary":"模型返回了非适配字段","entry_observation_text":"充电口盖无法弹开"}',
        )

    monkeypatch.setattr("ft_diag_agent.tree_generation._llm_extract_candidates", fake_extract_candidates)
    service = BatchTreeGenerationService(
        Settings(
            tree_generation_dir=tmp_path / "tree_generation",
            tree_proposals_dir=tmp_path / "tree_proposals",
            llm_enable=True,
            llm_provider="deepseek",
        )
    )

    job = service.run_batch_job(title="充电口盖test5", source_paths=[report], use_llm=True)

    assert job.artifact
    fallback = job.artifact.extraction_passes[0]
    assert fallback.pass_type == "RULE_LOW_CONF_DEBUG_FALLBACK"
    assert fallback.raw_output == raw_payload
    assert fallback.raw_text
    assert "entry_observation_text" in fallback.raw_text


def test_llm_candidate_extraction_retries_empty_json_response(tmp_path: Path, monkeypatch) -> None:
    report = tmp_path / "charge_lid.md"
    report.write_text("现场记录：充电口盖无法弹开。检查项：执行器推力测试。", encoding="utf-8")
    calls: list[str] = []

    class FakeProvider:
        def __init__(self, _settings):
            self.last_error = None
            self.last_model = None
            self.last_payload = None
            self.last_raw_content = None

        def json_completion(self, *, user_prompt, response_model, **_kwargs):
            calls.append(user_prompt)
            self.last_model = "deepseek-v4-pro"
            if len(calls) == 1:
                self.last_payload = {}
                self.last_raw_content = "{}"
                return response_model.model_validate({})
            payload = {
                "extraction_summary": "强约束重试后完成抽取",
                "phenomena": ["充电口盖无法弹开"],
                "root_causes": ["执行器推力不足"],
                "checks": [{"check_name": "执行器推力测试", "test_target": "确认推力是否达标"}],
                "transition_hints": [
                    {
                        "source_symptom_name": "充电口盖无法弹开",
                        "test_name": "执行器推力测试",
                        "target_symptom_name": "执行器推力不足",
                    }
                ],
                "risk_notes": "1. 复现率低。2. 需要更多样本验证。",
            }
            self.last_payload = payload
            self.last_raw_content = (
                '{"extraction_summary":"强约束重试后完成抽取","phenomena":["充电口盖无法弹开"]}'
            )
            return response_model.model_validate(payload)

    monkeypatch.setattr("ft_diag_agent.tree_generation.LlmProvider", FakeProvider)

    attempt = _llm_extract_candidates(
        Settings(llm_enable=True, llm_provider="deepseek"),
        "充电口盖test",
        "请抽取候选树",
        [{"chunk_id": "charge.md:0", "source_path": str(report), "text": report.read_text(encoding="utf-8")}],
    )

    assert len(calls) == 2
    assert attempt.candidates
    assert attempt.candidates.symptom_candidates
    assert attempt.candidates.risk_notes == ["复现率低。", "需要更多样本验证。"]
    assert "source_symptom_name" in attempt.candidates.transition_hints[0]
    assert attempt.raw_payload
    assert len(attempt.raw_payload["attempts"]) == 2
    assert attempt.raw_text
    assert "PASS_1_PRIMARY" in attempt.raw_text
    assert "PASS_1_STRICT_RETRY" in attempt.raw_text


def test_batch_tree_generation_ignores_unsupported_files(tmp_path: Path) -> None:
    report = tmp_path / "notes.docx"
    report.write_text("故障现象：车身异响", encoding="utf-8")
    service = BatchTreeGenerationService(
        Settings(tree_generation_dir=tmp_path / "tree_generation", tree_proposals_dir=tmp_path / "tree_proposals")
    )

    job = service.run_batch_job(title="无有效资料", source_paths=[report], use_llm=False)

    assert job.status == TreeGenerationJobStatus.FAILED
    assert job.error == "没有可用输入资料；仅支持 PDF/MD/TXT/CSV。"


def assert_no_extraction_suggested_statuses(artifact: TreeGenerationArtifact) -> None:
    suggested = {"SUGGESTED_GROUNDED", "SUGGESTED_LOW_CONF"}
    for entity in artifact.symptoms + artifact.tests + artifact.measures:
        assert entity.name_status not in suggested
        assert entity.description_status not in suggested
    for transition in artifact.transitions:
        assert transition.condition_status not in suggested
        assert transition.description_status not in suggested
