# PROJECT_STATE.md

更新时间：2026-06-08

本文件记录当前项目实现状态。新会话或其他智能体应先读 `AGENTS.md`，再读本文件，最后读 `TASKS.md`。

## 1. 当前总体状态

项目已经从最初草案推进为一个可运行的“故障树引导诊断 + Tree Evolution Agent”原型产品。当前不是纯 Demo，而是按最终产品架构搭建了模块边界、状态模型、工具协议、人工检测、Gate、报告、replay、eval、训练数据出口和动态树演化前置能力。

当前主线能力：

- 以“新工单”为诊断入口。
- 先做工单解析、故障类型判断、故障树覆盖判断。
- 有覆盖时进入故障树诊断。
- 无覆盖时：
  - 生产态输出不支持并阻断 PASS。
  - 开发态进入 case-only 探索诊断，并沉淀动态故障树候选请求/聚类。
- 新目标：把无树 case-only 诊断改造为 TreeProposal 发现、临时诊断、评测、人工审核、灰度和 Released Tree 发布链路。
- 当前已支持两类 TreeProposal 发现入口：批量文档 Tree Generation，以及开发态 case-only request / 跨 runs cluster 写入 DRAFT_TREE。
- TreeProposal 审核页已支持确定性晋升预审；预审只输出阻塞/警告/建议并写入审核日志，不自动晋升。
- TreeProposal 审核页已支持人类可读的 proposed tree 审核视图：有 `TreeGenerationArtifact` 时展示真实 L1/L2/L3 树图、节点状态和 transition/test；无 artifact 时展示 `DISCOVERY_ONLY` skeleton 并明确阻塞。
- TreeProposal 审核页已支持从“来源输入”到“生产 TTL 发布”的 8 步流程状态条，绿色表示完成、蓝色表示当前、红色表示阻塞、灰色表示未开始。
- Tree Generation Extraction Eval 第一版已支持 `tree_generation_extraction_v1`：结构评分、字段完整率、原文事实召回、grounding、幻觉率、链路逻辑、test 可执行性、反证和重复率；批量生成后自动写入 eval result，DRAFT 晋升预审会消费该结果。
- Tree Evolution 已扩展为双轨：`NEW_TREE` 用于无树覆盖新增树，`TREE_CHANGE` 用于已有树的检测项、阈值、transition condition、executor、scope 或分支变化。covered 诊断中出现工艺漂移/反证/返修信号时可生成 `tree_change_proposal`，但不改变 Gate、不写 TTL。
- Streamlit 已拆分为 sidebar 页面：`诊断工作台` 保留工单诊断和诊断中发现无树的第二入口；`树生成工作台` 承载批量文档树生成、树生成 HITL 补全和 TreeProposal 审核。
- 所有故障树 test 当前均按人工 HITL 录入。
- Gate 决定 `PASS / GRAY / FAIL`。
- 生成 Markdown + JSON 报告。
- 记录 replay trace。
- 支持 labeled v1 离线评测。

新的产品边界：

- 生产主链路只能使用人工审核发布的 `Released Tree`。
- `DRAFT_TREE`、`CANDIDATE_TREE`、`GRAY_TREE` 不能让 Gate 自动 `PASS`。
- 动态树生成必须遵守 `docs/tree_gen_agent.md`：先维护本体实体和 `SymptomTransition`，再确定性重建 `FaultTree`。
- Tree Evolution 的正式设计文档为 `docs/tree_evolution_plan.md`。

故障树业务边界：

- 故障树是可执行诊断知识库，不是普通文档摘要树。
- `start = L1`，表示一棵树唯一入口故障/失效现象。
- `inner = L2`，表示中间异常状态，可有多层。
- `root = L3`，表示不可再拆分的根因；若仍可继续拆分，则必须建模为 inner。
- 树生成阶段 HITL 用于补全知识库字段；诊断阶段 HITL 用于人工执行当前案例检测并回填测试结果。
- 抽取阶段 FieldStatus 为 `EXTRACTED_EXPLICIT`、`EXTRACTED_INFERRED`、`MISSING`；`SUGGESTED_*` 只属于补全阶段。
- 当前代码已将树生成抽取阶段默认状态改为 `EXTRACTED_INFERRED` / `MISSING`；若 LLM 返回 `SUGGESTED_*`，会映射为 `EXTRACTED_INFERRED`。
- 当前代码已在 draft entity properties 中标记 `needs_generation_hitl` / `hitl_reasons`，并能从 `TreeGenerationArtifact` 生成 HITL 补全候选列表；UI 可基于原文 chunk、RAG 和领域/工艺/维修专家约束生成建议选项，用户确认后写回草稿字段为 `CONFIRMED` 并重跑校验/预览。
- 批量树生成已拆为 `PASS_1` 候选抽取、`PASS_2` 实体分级、`PASS_3` transition 绑定、结构校验、`PASS_4` 修复和确定性 BFS 重建；`PASS_4` 不允许因 `EXTRACTED_INFERRED` / `MISSING` 低置信而删除待确认内容。
- Tree Generation UI 已增加运行阶段状态流、阶段耗时统计、Mermaid 树结构图和当前 LLM 配置提示；生成完成后默认选中新 job，历史任务列表按更新时间倒序，避免旧 job 的 LLM 失败信息误导为本次结果。
- Tree Generation HITL 补全候选写回后不再修改已实例化的 `tree_generation_job_select` widget key，避免 Streamlit `session_state` 异常；更新同一 job 后只刷新 `last_tree_generation_job_id` 并 rerun。
- Tree Generation HITL 确认写回后会剪除已解决的建议卡片，并显示待补全字段数量变化，避免用户无法判断是否生效。

## 2. 当前技术栈

- Python 3.11
- uv / `.venv`
- Pydantic
- rdflib
- LangGraph
- LangChain 相关抽象
- DeepSeek/OpenAI-compatible LLM provider
- Chroma + lexical fallback RAG
- pandas
- Streamlit
- pytest
- ruff

## 3. 当前数据资产

### 故障树

文件：

- `corrected_fault_tree_instances.ttl`

当前包含两棵树：

- `FT_001`：车机黑屏
- `FT_002`：车门无法关闭

已实现能力：

- TTL/RDF 解析。
- `FailureSymptom`、`OntologyTest`、`OntologyMeasure`、`SymptomTransition`、`FaultTree` 解析。
- start/root/path 索引。
- 当前节点 outgoing transitions 查询。
- 路径枚举。
- 数据质量 notes。

### 文档与工单

目录：

- `data/raw_docs/`

当前用于：

- SOP/FMEA/维修手册 RAG。
- mock 工单加载。
- labeled eval v1 数据集加载。
- case-only 历史工单检索。
- ReworkGuard 相似返修案例检索。

### Tree Evolution 数据

目录：

- `data/tree_proposals/`

规划文件：

- `proposals.jsonl`
- `case_links.jsonl`
- `eval_results.jsonl`
- `review_logs.jsonl`
- `artifacts/{proposal_id}/`

用途：

- 保存从 unsupported case-only 工单发现的 TreeProposal。
- 保存 proposal 与工单的支持/反驳关系。
- 保存候选树评测结果。
- 保存人工审核日志；灰度、发布和回滚信息仍待正式发布链路补齐。

### Tree Generation 数据

目录：

- `data/tree_generation/`

当前用途：

- 批量文档预生成候选树的 jobs、uploads、artifacts。
- 保存 `TreeGenerationJob`、候选本体草案、结构校验报告、确定性 BFS 重建预览。
- 输出 `TreeProposal(status=DRAFT_TREE)` 到 `data/tree_proposals/proposals.jsonl`。

### labeled eval v1

目录：

- `data/raw_docs/diagnostic_eval_labeled_cases_v1`

数据规模：

- FT_001 车机黑屏：10 条。
- FT_002 车门无法关闭：10 条。
- 非故障树 case-only：12 条。
- 分流边界/误放行防护：6 条。
- 总计：38 条。

用途：

- 覆盖判断。
- 树选择。
- 最终叶子。
- Gate 安全。
- case-only 探索。
- 否定词、相邻现象、多系统事故、信息不足防护。
- 返修/误判识别。

RAG 对 labeled eval 数据做了标签脱敏，避免把 `expected_*`、真实维修闭环、人工复核结论等答案泄漏给诊断过程。

## 4. 已完成模块

### 4.1 工程骨架

已完成：

- `pyproject.toml`
- `.env.example`
- `.gitignore`
- `README.md`
- `src/`
- `app/`
- `tests/`
- `docs/`
- `data/raw_docs/`
- Git 基线策略：源码、测试、文档、配置样例和必要样例数据纳入版本控制；`.env`、缓存、replay、Tree Generation 运行输出、TreeProposal 运行输出和 eval 输出由 `.gitignore` 排除。
- `data/chroma/`
- `runs/`
- `datasets/`

### 4.2 领域模型

文件：

- `src/ft_diag_agent/models.py`

已实现核心模型：

- `DiagnosticState`
- `IntakeRequest`
- `NormalizedPhenomenon`
- `WorkOrder`
- `WorkOrderClassification`
- `CoverageDecision`
- `DiagnosisMode`
- `DiagnosticAction`
- `TestExecutionSpec`
- `ExecutedTest`
- `EvidenceItem`
- `ToolCallRecord`
- `GateResult`
- `DiagnosisReport`
- `ReplayRecord`
- `CaseOnlyHypothesis`
- `ExploratoryDiagnosticPlan`
- `ExploratoryFinding`
- `SimilarReworkCase`
- `ReworkRiskAssessment`

### 4.3 故障树数据层

文件：

- `src/ft_diag_agent/fault_tree.py`

已实现：

- rdflib TTL 解析。
- 树、节点、transition、test、measure 内存索引。
- `search_trees()`。
- `enumerate_paths()`。
- `outgoing_transitions()`。
- `transition_for_test()`。
- `make_candidate_causes()`。
- `start_node_id()`。
- `get_test()` / `get_symptom()`。

当前边界：

- 第一版使用 RDF 内存解析。
- 已通过接口边界保留未来替换 Neo4j 的空间。

### 4.4 工单解析与分类

文件：

- `src/ft_diag_agent/work_orders.py`
- `src/ft_diag_agent/classifier.py`
- `src/ft_diag_agent/intake.py`

已实现：

- mock 工单文件解析。
- 粘贴工单文本解析。
- 原始文本宽松解析，支持非严格结构化文本。
- 规则 + 故障树 start symptom 检索 + RAG + LLM judge 的混合分类。
- 输出 `tree_id`、`confidence`、`coverage_status`、`reasoning_summary`。
- 生产态和开发态路由。

当前问题：

- 分类仍偏规则 + LLM 增强，不是完整学习型分类器。
- 边界工单、多系统事故、否定词处理已有测试，但仍需要更多真实数据验证。

### 4.5 LLM provider

文件：

- `src/ft_diag_agent/llm.py`
- `src/ft_diag_agent/settings.py`

已实现：

- DeepSeek/OpenAI-compatible provider 抽象。
- `.env` 配置读取。
- 无 key 或调用失败时规则兜底。
- Pydantic 校验结构化输出。

当前策略：

- 默认用 DeepSeek。
- flash 用于开发调试、抽取、简单报告。
- pro 用于低置信分类、复杂解释、case-only 探索。

注意：

- 不要在任何代码或文档中写真实 key。

### 4.6 RAG

文件：

- `src/ft_diag_agent/rag.py`

已实现：

- `PDF/MD/TXT/CSV` 文档扫描。
- chunk。
- metadata 解析。
- Chroma 持久化。
- lexical fallback。
- 按 `doc_type` 等 metadata 过滤。
- labeled eval 数据脱敏。

当前边界：

- 当前检索用于证据候选，不直接决定根因。
- Chroma embedding 若不可用，仍可走 lexical fallback。

### 4.7 Planner

文件：

- `src/ft_diag_agent/planner.py`
- `src/ft_diag_agent/case_only_planner.py`
- `src/ft_diag_agent/rework_guard.py`
- `src/ft_diag_agent/workflow.py`

已实现三类动作：

- `TEST`：故障树 test，对应当前节点 outgoing transition。
- `CASE_ONLY_HITL`：无故障树覆盖时的开发态探索动作。
- `REWORK_COUNTER_CHECK`：返修/前次误判反证检查。
- `CONFIRMATION_CHECK`：到达疑似根因或关键诊断节点后的发布前补证动作。
- `FaultTreeGenerationRequest`：开发态无覆盖工单的动态故障树候选生成请求。
- `TreeGenerationJob`：批量文档生成候选树的任务记录。
- `TreeGenerationArtifact`：候选本体草案、transition、结构校验和重建预览。
- `OntologyExtractionPlan` / `OntologyExtractionPass`：记录 LLM-first 多轮抽取、校验和修复过程。
- `OntologyValidationIssue`：结构校验问题，包含 rule_id、severity 和修复建议。
- `TreeProposal`：动态树演化的候选树记录，当前批量入口生成 `DRAFT_TREE`。

故障树 Planner 当前逻辑：

- 若有 `active_tree_id` + `active_node_id`，从当前节点 outgoing transitions 生成下一步 test。
- 若当前节点已有疑似根因或关键诊断节点命中，生成非 blocking 的补证动作，例如 PMIC 输出、背光驱动、锁扣涂色啮合、二道锁信号等。
- 若无活动节点，则从候选路径中选择下一项未执行 test。
- 已执行 test 会被抑制。
- 当前所有 test 的 executor 均为 `HUMAN`，工具名为 `human_input`。

case-only Planner 当前逻辑：

- 基于历史工单/RAG/LLM/规则生成初始假设。
- 输出探索目标、假设列表、下一步 HITL 检查。
- 提交人工结果后生成 `ExploratoryFinding`。
- 已实现多轮探索循环：根据检查结果更新假设状态 `OPEN / SUPPORTED / REFUTED / NEEDS_EVIDENCE`，抑制已执行检查，并围绕仍需证据的假设生成下一轮 `CASE_ONLY_HITL` 动作。
- 不会在新工单输入时直接生成可诊断的新树；case-only 先探索取证，`FaultTreeGenerationRequest` / `TreeProposal` 只是后续树生成和审核输入。
- 开发态不能生产 PASS。

动态故障树候选请求当前逻辑：

- 文件：`src/ft_diag_agent/dynamic_tree.py`
- 只在 `UNSUPPORTED + CASE_ONLY_EXPLORATORY` 且已有 case-only 假设/计划/发现时生成。
- 输出 `DiagnosticState.fault_tree_generation_request` 和 `DiagnosticState.fault_tree_request_cluster`。
- 候选请求包含来源工单、候选入口现象、候选故障域、候选根因假设、建议检查项、证据 ID、来源引用、本体建模约束和校验步骤。
- 已被 case-only 循环 `REFUTED` 的假设不会进入候选 root cause family，只作为探索发现/反证证据保留。
- 聚类对象包含 cluster key、支持案例、合并假设、合并检查、审核状态建议和下一步建议。
- Replay 导出会跨 `runs/*.jsonl` 合并相似动态树请求，生成 `datasets/dynamic_tree_clusters.jsonl`。
- Streamlit Replay 页可以手动扫描 `runs/` 并查看动态树候选聚类历史。
- 聚类 `support_count` 按独立诊断 case 数计算，避免把工单号、RAG 引用等辅助 ID 误算为审核样本。
- 对齐 `docs/tree_gen_agent.md`：诊断 Agent 不直接生成最终 FaultTree；后续故障树生成 Agent 应先维护本体实体和 `SymptomTransition`，最终 FaultTree 由 start BFS 确定性重建。
- 当前请求和聚类只用于开发态沉淀和人工审核入口，不写入生产 TTL，不影响 Gate，不能生产 PASS。

Tree Evolution 新规划：

- 文档：`docs/tree_evolution_plan.md`
- 将现有 `FaultTreeGenerationRequest` / `FaultTreeRequestCluster` 作为 TreeProposal 的前置输入。
- 新增 `TreeProposal Store` 后，unsupported development case 应生成或更新 `TreeProposal`。
- 生命周期：
  - `DRAFT_TREE`
  - `CANDIDATE_TREE`
  - `GRAY_TREE`
  - `RELEASED_TREE`
  - `REJECTED`
- `DRAFT_TREE` / `CANDIDATE_TREE` / `GRAY_TREE` 不允许生产 PASS。
- `RELEASED_TREE` 才能进入生产故障树主链路。
- 晋升必须依赖 classification eval、diagnosis eval、tree proposal eval 和人工审核日志。

批量文档生成入口当前逻辑：

- 文件：`src/ft_diag_agent/tree_generation.py`
- UI：`app/streamlit_app.py` 中“树生成：批量文档预生成候选树”。
- 输入：用户从 `data/raw_docs/` 选择或上传 `PDF/MD/TXT/CSV`。
- 输出：
  - `TreeGenerationJob`
  - `OntologyEntityDraft`
  - `SymptomTransitionDraft`
  - `TreeGenerationValidationReport`
  - `OntologyValidationIssue`
  - 确定性 BFS `rebuilt_fault_tree` 预览
  - `TreeProposal(status=DRAFT_TREE)`
- 阶段耗时 `stage_timings`
- 已新增 `docs/tree_ontology_schema.md` 作为 ontology 类、属性、关系、字段状态和抽取要求的核心真源。
- 树生成核心已改为 LLM-first：
  - 多 chunk 输入。
  - PASS_1 候选实体抽取：候选异常状态、检查项、处置措施和诊断链提示。
  - PASS_2 实体分类、去重、合并同义 start、判定 start/inner/root。
  - PASS_3 基于已分级实体生成 `SymptomTransition`，并在边上绑定 test。
  - 结构校验失败后触发 PASS_4 LLM repair pass。
  - PASS_4 repair 后有确定性防删保护：若 repair 误删 `EXTRACTED_INFERRED` / `MISSING` 的待确认实体、检查项、措施或 transition，会恢复并进入树生成 HITL 候选队列。
  - 若 PASS_2 建图失败但 PASS_1 有候选实体，系统会用候选实体确定性组装 `NEEDS_REPAIR_LLM_DRAFT`。
  - `OntologyExtractionPass` 记录 `output_counts` / `output_preview` / `raw_output` / `raw_text`，UI 可同时查看适配后的抽取结果、LLM parsed JSON 和模型原始响应文本；preview 中保留关键 status，避免误把 preview 当作缺状态的事实源。
  - PASS_1 候选抽取遇到 `{}` 或全空候选时，会自动触发一次强约束重试。
  - PASS_1 适配器已兼容 `risk_notes` 字符串和对象型 `transition_hints`，避免有效 LLM 返回因非核心字段格式小偏差掉入规则 fallback。
  - 任务标题、补充说明仅作为 job metadata / prompt 上下文，不作为候选实体证据；规则 fallback 只从输入文档 chunk 抽取。
  - schema adapter 兼容 `tree_gen_agent.md` 风格字段，如 `symptom_name/symptom_level`、`source/target/test_id`。
  - candidate adapter 兼容 `entities/root_causes/failure_modes/abnormal_states/checks/diagnostic_paths/causal_chains`。
  - 空本体图不能视为抽取成功，会进入 repair、确定性候选组装或低置信兜底。
  - 最终仍由确定性 BFS 重建预览。
  - `TreeGenerationArtifact.stage_timings` 持久化各阶段耗时；Streamlit 运行中展示当前阶段，完成后展示耗时表。
  - Streamlit 和 `scripts/render_tree_generation_tree.py` 支持 Mermaid 树结构可视化，节点显示 level/status，边显示绑定 test。
  - Tree Generation job 列表按 `updated_at/created_at` 倒序展示，生成成功后 selectbox 自动选中最新 job，并在表单中展示本次 LLM enable/provider/model 配置。
  - 树生成 HITL 已支持 `TreeGenerationHitlSuggestion` / `TreeGenerationHitlDecision`：LLM 仅提供基于原文 + RAG + 专家知识的建议选项，人工确认后才写回草稿 artifact，并将字段推进到 `CONFIRMED`。
  - `src/ft_diag_agent/tree_proposals.py` 已实现本地文件型 `TreeProposalStore`，统一读写 proposals、case links、eval results、review logs 和 artifact 快照；Streamlit 已有 TreeProposal 审核页，第一阶段只允许 `DRAFT_TREE -> CANDIDATE_TREE`、请求修改或拒绝。
  - `src/ft_diag_agent/tree_proposal_eval.py` 已实现 Tree Proposal Eval 第一版确定性指标：结构校验、test coverage、evidence binding、HITL confirmed/pending 和 unsafe blockers，并写入 `eval_results.jsonl`。
  - `src/ft_diag_agent/tree_proposal_eval.py` 已实现 replay-based shadow eval 第一版：读取 `runs/*.jsonl` 或传入 replay records，按 proposal 的 start/root/test/evidence 信号匹配历史 replay，输出相关 case 数、root/test 支持率、证据命中率、失败案例和 unsafe findings；该能力只作为 `CANDIDATE_TREE -> GRAY_TREE` 离线审核材料，不写 TTL、不影响生产 Gate。
  - `src/ft_diag_agent/tree_release.py` 已实现 release manifest / rollback metadata / TTL diff 发布前材料包第一版：从 `GRAY_TREE` proposal、artifact、eval results 和 review logs 生成 `manifest.json`、`rollback_metadata.json`、`ttl_diff.md` 和 `release_artifact.json`，保存到 `data/tree_proposals/artifacts/{proposal_id}/release/`；发布材料必须消费 `tree_generation_extraction_v1`、结构 eval、shadow eval、Gray 审核日志和正式签核。
  - `src/ft_diag_agent/tree_admission.py` 已实现 GRAY / RELEASED 准入材料包第一版：把 artifact、case/evidence、抽取质量 eval、结构 eval、shadow eval、候选审核、release artifact、manifest、rollback、TTL diff、Gray 审核和正式签核整理成逐项材料状态、阻塞项、警告项和建议动作。
  - `src/ft_diag_agent/released_tree_registry.py` 已实现 Released Tree registry / 生产 TTL 写入审计和受控执行第一版：只允许 `RELEASED_TREE` proposal 在 release artifact ready、正式签核、rollback、`tree_generation_extraction_v1`、runtime-compatible TTL preview、tree_id 唯一性和生产 TTL 重复检查通过后，写入 `data/released_trees/registry.jsonl` 的 `READY_FOR_TTL_WRITE` 记录；写入动作必须消费 READY 记录，生成备份后追加 TTL，成功后标记 `REGISTERED`；rollback dry-run 验证备份可恢复，正式 rollback 恢复 TTL 并标记 `ROLLED_BACK`。审计历史写入 `ttl_audit_results.jsonl`、`ttl_write_results.jsonl` 和 `ttl_rollback_results.jsonl`。
  - `src/ft_diag_agent/tree_proposal_precheck.py` 已实现 TreeProposal 晋升预审：`DRAFT_TREE -> CANDIDATE_TREE` 检查 start/root/test、source case、evidence、artifact、Tree Proposal Eval unsafe findings、support case count 和 evidence binding rate；`CANDIDATE_TREE -> GRAY_TREE` 和 `GRAY_TREE -> RELEASED_TREE` 预审会消费同一套 admission package，避免审核 UI 与阻塞规则漂移。
  - `src/ft_diag_agent/tree_generation_eval.py` 已实现 Tree Generation Extraction Eval 第一版：输出 `tree_generation_extraction_v1`，评估 ontology 结构、字段完整率、原文事实召回、grounding、幻觉率、链路逻辑、test 可执行性、反证和重复率；DRAFT 晋升预审要求该 eval 已运行。
  - `src/ft_diag_agent/tree_proposal_analytics.py` 已实现跨 proposal 聚合预审第一版：按 phenomenon bucket 汇总同类 proposal、root cause family、repeated test、人工确认有效率和高风险反证；聚合结论只追加 blocker/warning/satisfied/recommended action，不自动晋升、不写 TTL、不影响 Gate。
  - `src/ft_diag_agent/tree_proposal_view.py` 已实现 TreeProposal 审核视图辅助：8 步生命周期状态、artifact 树节点/transition 表和无 artifact 时的 proposal skeleton Mermaid；Replay/Shadow 步骤会显示 shadow eval 是否完成或阻塞。
  - `TreeProposal` 已支持 `proposal_kind=NEW_TREE/TREE_CHANGE`；`DiagnosticState.tree_change_proposal` 可记录 covered case 中发现的已有树变更候选，`TreeProposalStore.upsert_tree_change_proposal()` 可写入本地 store。
- 规则抽取仅作为 LLM 不可用/失败时的 `LOW_CONF_DEBUG_DRAFT` 调试兜底，不作为高质量可用树。
- 当前已支持受控写入正式 TTL 文件和手动 rollback 演练，但不自动接生产分类、不允许未发布树 Gate PASS；发布后监控、自动回滚触发和服务化 registry 尚未完成。

Tree Generation 第二入口当前逻辑：

- 文件：`src/ft_diag_agent/tree_proposals.py`
- UI：`app/streamlit_app.py` 中“动态故障树候选请求”和“跨 runs 动态故障树聚类”。
- TreeProposal 审核 UI 已增加“跨 Proposal 聚合”tab，展示同类现象 bucket、root cause family、重复 test、人工确认率和高风险反证。
- 输入：
  - 开发态 unsupported case-only 诊断生成的 `FaultTreeGenerationRequest`。
  - 从 `runs/*.jsonl` 扫描合并出的 `FaultTreeRequestCluster`。
- 输出：
  - `TreeProposal(status=DRAFT_TREE, source_type=WORK_ORDER_TRIGGER)`。
  - `TreeProposal(status=DRAFT_TREE, source_type=DYNAMIC_CLUSTER)`。
  - `TreeProposalCaseLink`，记录 source case、work order、候选 root cause family、candidate tests 和人工确认占位。
  - `data/tree_proposals/artifacts/{proposal_id}/proposal.json` 快照。
- 重复写入使用稳定 proposal id，只更新同一 proposal 和 case link，不产生重复 proposal。
- 第二入口不调用 LLM，不写正式 TTL，不影响生产 Gate，只把开发态探索结果沉淀为可审核 proposal。

ReworkGuard 当前逻辑：

- 识别返修、维修后复现、前次处置无效、相似返修案例。
- 对典型误判模式生成避免重复动作和反证检查。
- 相似案例检索只在当前工单已有弱风险信号时启用。
- 推荐反证检查会被 Planner 提升为 `REWORK_COUNTER_CHECK`，排在普通故障树 test 前。

当前问题：

- 故障树 Planner 仍然偏确定性规则，不是强 agent planning。
- 树内下一步动作命中率已通过补证动作显著提升，但仍需防止补证模板在真实数据上过拟合。
- LLM rerank/解释层已预留思路，但还不是主力。

### 4.8 Tool Registry 与 HITL

文件：

- `src/ft_diag_agent/tools.py`
- `app/streamlit_app.py`

已实现：

- `ToolRegistry`
- `DiagnosticTool`
- `human_input`
- `fault_tree_search`
- `rag_search`
- `report_generate`
- stub：SPC/BOM/FP-Growth/quality case 等。

当前策略：

- 故障树上所有 test 暂时都视为人工检测。
- 人在 Streamlit 里填写检测结论、读数、是否支持分支、证据强度、备注、是否采纳 Planner。
- 提交后写入 `executed_tests`、`evidence_chain`、`tool_calls`、`human_feedback`、`replay_trace`。

### 4.9 Gate

文件：

- `src/ft_diag_agent/gate.py`

已实现：

- `PASS / GRAY / FAIL` 确定性门禁。
- 无匹配故障树生产态不能 PASS。
- unsupported 生产态 FAIL。
- unsupported 开发态 case-only 只能 GRAY，不能生产 PASS。
- 缺关键证据或存在未完成 blocking action 时 GRAY。
- 活动叶子 + 证据满足时 PASS。
- 返修风险进入 risk notes 和 required actions。

重要边界：

- LLM 不允许覆盖 Gate 结论。

### 4.10 Report

文件：

- `src/ft_diag_agent/report.py`

已实现：

- Markdown 报告。
- JSON 报告。
- 标准现象。
- 故障树路径/候选根因。
- 当前根因。
- 证据链。
- 检测过程。
- Gate 状态。
- 推荐处置。
- 数据质量风险。
- case-only 不可生产放行提示。
- 返修/误判风险章节。

### 4.11 Replay、Eval、训练数据出口

文件：

- `src/ft_diag_agent/replay.py`
- `src/ft_diag_agent/eval.py`
- `scripts/`

已实现：

- 每次诊断写入 replay。
- 可导出 planner/report/preference 数据。
- labeled v1 eval。
- 版本化 eval run：`datasets/eval_runs/{run_id}/` 保存 metadata、summary、results、details 和树/节点/test 混淆文件。
- Streamlit Eval 页支持 baseline/current 历史 run 对比、指标 delta、关键回归/普通 warning、新增失败和已修复 case。
- Streamlit Eval 页支持树、节点和 test 维度混淆分析。
- 失败 case drill-down 已能展示 replay 回放摘要和 replay JSON。
- 指标包括：
  - coverage accuracy
  - route accuracy
  - tree selection accuracy
  - final leaf accuracy
  - Gate accuracy
  - production Gate safety
  - case-only hypothesis hit
  - next action hit
  - reject accuracy
  - rework/misdiagnosis identification
  - gate mispass count
  - guardrail misroute count
  - wrong tree misdiagnosis count

当前边界：

- Eval 已支持离线脚本、结果导出、版本化 run、Streamlit 失败案例 drill-down、多版本对比、树/节点/test 混淆分析和 replay 摘要联动。
- 仍缺少多个 run 的趋势图、图节点级 replay 事件流和自动根因归因报告。
- SFT/LoRA/QLoRA/DPO 目前是数据准备能力，不默认训练。

### 4.12 Streamlit UI

文件：

- `app/streamlit_app.py`

已实现：

- 工单选择。
- 粘贴工单文本。
- 诊断启动。
- 覆盖状态展示。
- 当前节点展示。
- outgoing transitions 展示。
- Planner 动作选择。
- 人工检测录入。
- case-only 探索计划与假设展示。
- 诊断时间线：工单输入、分类/覆盖、路径或探索计划、Planner 检查动作、人工结果与证据、Gate、报告/Replay。
- Planner / Evidence / Gate 因果解释：把规划原因、证据状态和 Gate 影响放在同一表中展示。
- 证据摘要：按来源、支持对象、强度和解释展示，原始证据 JSON 折叠作为审计材料。
- 返修/误判风险展示。
- Gate 展示。
- 报告展示。
- replay/eval 查看。

已做过针对 Streamlit rerun 的处理：

- `st.cache_resource` 缓存 repository、RAG、engine。
- `st.session_state["diag_state"]` 保存诊断状态。
- eval 只在点击按钮时运行。

当前问题：

- 生产级视觉 polish、角色化审核视图和移动端体验仍待优化。
- replay 仍主要是 JSON 审计视图，缺少事件级回放和版本对比。
- 反证检查、case-only 探索、普通 test 已能通过时间线和因果解释区分，但后续可继续增加专业角色视角的过滤器。

## 5. 最近一次验证结果

最近一次代码验证时间：2026-06-08，完成受控生产 TTL 写入执行、rollback 演练、`tree_generation_extraction_v1` 发布门禁和文档同步后。

命令：

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall src app tests
.venv/bin/python -m ft_diag_agent.eval --diagnostic-eval --eval-suite labeled_v1 --eval-output-dir datasets/eval_results_labeled_v1 --eval-runs-dir datasets/eval_runs
```

结果：

- ruff：通过。
- pytest：`105 passed`。
- compileall：通过。
- labeled v1 eval：通过，`gate_mispass_count=0`，`wrong_tree_misdiagnosis_count=0`。

核心评测指标：

- cases: `38`
- covered_cases: `20`
- unsupported_cases: `18`
- coverage_accuracy: `1.0`
- route_accuracy: `1.0`
- tree_selection_accuracy: `0.9524`
- final_leaf_accuracy: `1.0`
- gate_accuracy: `1.0`
- production_gate_safety_rate: `1.0`
- case_only_hypothesis_hit_rate: `0.75`
- next_action_hit_rate: `0.8947`
- reject_accuracy: `1.0`
- rework_or_misdiagnosis_identification_rate: `1.0`
- gate_mispass_count: `0`
- guardrail_misroute_count: `0`
- wrong_tree_misdiagnosis_count: `0`

分组观察：

- `NON_TREE_CASE_ONLY`
  - case_only_hypothesis_hit_rate: `0.9167`
  - next_action_hit_rate: `1.0`
- `TREE_COVERED_BLACK_SCREEN`
  - next_action_hit_rate: `1.0`
- `TREE_COVERED_DOOR_CLOSE`
  - next_action_hit_rate: `1.0`
- `ROUTING_GUARDRAIL`
  - next_action_hit_rate: `0.3333`

重要结论：

- 覆盖判断、路由、Gate 安全性已经比较稳定。
- 树内 Planner 下一步动作命中率已显著提升；剩余 next action miss 主要集中在 guardrail 边界样本。
- LangGraph 条件分支后，unsupported 生产态会直接 Gate/Report，不再为了 next-action 指标生成诊断动作；因此总 next-action 指标略降，但生产安全边界更清晰。
- Eval 现在额外输出 `diagnostic_eval_details.jsonl`，用于定位失败 case 的 expected/actual、planner actions、executed tests、evidence 和 failure tags。

## 6. 当前服务状态

最近一次已启动 Streamlit：

- URL: `http://127.0.0.1:8502/`

启动命令：

```bash
STREAMLIT_BROWSER_GATHER_USAGE_STATS=false .venv/bin/streamlit run app/streamlit_app.py --server.address 127.0.0.1 --server.port 8502 --server.headless true
```

如果端口被占用：

```bash
lsof -ti tcp:8502
kill <PID>
```

然后重新启动。

## 7. 已知问题与风险

### 7.1 Planner 不是强 Agent 编排

当前 LangGraph 已承载主流程节点，但 Planner 主体仍是规则型：

- 当前节点 -> outgoing transition -> HITL action。
- 候选路径 -> 下一未执行 test。
- ReworkGuard -> 反证 HITL action。
- case-only -> RAG/LLM/规则探索动作。

风险：

- 对复杂、多分支、多证据冲突场景的自主规划能力有限。
- 树内下一步动作命中率仍不足。

### 7.2 LangGraph 已有条件状态流，但还不是完整 Agent Graph

已实现：

- `route_by_coverage` 条件边：
  - 覆盖故障树 -> `retrieve_tree`
  - unsupported 生产态 -> 直接 `gate`
  - unsupported 开发态 -> `retrieve_evidence` 并进入 case-only 探索
- `retrieve_evidence` 条件边：
  - fault-tree 诊断 -> `apply_existing_checks`
  - case-only 探索 -> `assess_rework_risk`
- `plan_case_only` 条件边：
  - 预留自动工具动作 -> `execute_auto_actions`
  - 当前 HITL 策略 -> `gate`
- `gate` 条件边：
  - 存在 blocking human_input 动作 -> `wait_hitl`
  - `PASS` -> `gate_pass`
  - `GRAY` -> `gate_gray`
  - `FAIL` -> `gate_fail`
- `DiagnosticState.workflow_phase` 已记录 `WAITING_HITL / GATE_PASS / GATE_GRAY / GATE_FAIL` 等阶段；`waiting_for_human` 和 `waiting_action_ids` 显式表达是否等待人工。
- 非 LangGraph fallback 已同步同样的 unsupported 路由语义。

仍未完成：

- 自动工具执行节点仍是占位，尚未接真实 SPC/BOM/曲线判异。
- replay 记录仍是整段 state snapshot，尚未拆成更细粒度图事件。
- 缺少多 agent 协作。
- 缺少动态计划修订节点。

当前可用，但还不是最终强编排形态。

### 7.3 Eval 平台仍需继续增强

已有 labeled v1 指标、`diagnostic_eval_details.jsonl`、版本化 eval run、Streamlit 失败案例 drill-down、历史 run 对比、树/节点/test 混淆分析和 replay 摘要联动。

仍需继续增强：

- replay 仍是整段 state snapshot 摘要，缺少图节点级事件流和逐步回放。
- 历史 run 对比当前是表格 delta，缺少趋势图和多个 run 的时间序列。
- 混淆分析当前按 expected/predicted 聚合，后续可增加按 transition/test/node 的版本间差异归因。

### 7.4 case-only 探索仍需迭代

已有比较可用的 demo 能力，但要达到“不依赖故障树也能较高准确率诊断”，还需要：

- 更多真实历史工单。
- 更强案例相似度模型。
- 诊断知识结构化。
- 反事实/反证检查生成。
- 工单闭环结果评估。
- 与动态故障树生成 Agent 融合。

### 7.5 SFT / LoRA / QLoRA / DPO 不应过早训练

当前已经有 replay 和导出能力，但样本规模和质量还不够。

风险：

- 过早训练会固化错误诊断路径。
- 如果 eval 标签或人工反馈质量不足，会放大偏差。

建议：

- 先扩充真实标注数据。
- 先提升 eval 平台和数据质量评分。
- 当 replay/preference 数据达到稳定规模后再训练。

### 7.6 LLM provider 风险

DeepSeek 当前可作为 OpenAI-compatible provider 使用，但：

- 模型名、API 行为可能变化。
- JSON 输出可能不稳定。
- 网络或 quota 问题会影响增强能力。

必须保持规则兜底。

## 8. 建议下一步

优先做 P0：

1. 线上/准线上 shadow diagnosis 服务化与发布后监控。
2. 自动触发 rollback 策略和生产 registry 服务化。
3. 保护 `.env` 与 replay 敏感信息。
4. 将 replay 从整段 state snapshot 拆成更细粒度事件，服务 Eval 回放、TreeChangeProposal 聚合和 Agent Graph 调试。

推荐下一项具体开发：

- P1 优先继续 TreeProposal 生命周期：Tree Generation Extraction Eval、TreeChangeProposal 第一版、受控 TTL 写入和 rollback 演练已完成，下一步增强 change eval、patch diff、跨 runs 变更聚合和 release patch artifact。
- 随后补正式生产运行链路：发布后监控、自动回滚触发和正式 shadow/gray 服务化。
- Eval 后续拆成 Classification Eval、Diagnosis Eval、Tree Proposal Eval、Tree Generation Extraction Eval / Change Eval 等评测族，并增加多 run 趋势图、图节点级 replay 回放和自动差异归因。
- LangGraph 后续重点不是再加线性节点，而是引入“工具执行 -> 状态更新 -> 再规划”的安全循环。

详见 `TASKS.md`。
