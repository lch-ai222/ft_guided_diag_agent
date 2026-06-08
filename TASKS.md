# TASKS.md

本文件是当前项目 backlog。优先级含义：

- `P0`：下一阶段最应该做，直接影响主线可用性、安全性或可评测性。
- `P1`：重要增强，影响产品体验、智能程度或长期演进。
- `P2`：工程质量、可维护性、数据闭环增强。
- `P3`：未来方向或条件成熟后再做。

## Backlog

### P0

#### P0.1 提升树内 Planner 下一步动作命中率（第一轮已完成）

背景：

- 第一轮前 labeled v1 总体 `next_action_hit_rate=0.5789`。
- 第一轮前 `TREE_COVERED_BLACK_SCREEN=0.2`，`TREE_COVERED_DOOR_CLOSE=0.5`。
- 2026-05-28 已增加 `CONFIRMATION_CHECK` 发布前补证动作和 `diagnostic_eval_details.jsonl`。
- Planner 补证动作后 labeled v1 总体 `next_action_hit_rate=0.9211`。
- LangGraph 条件分支后当前 labeled v1 总体 `next_action_hit_rate=0.8947`；下降来自 unsupported 生产态直接 Gate/Report，不再生成诊断动作。
- 当前 `TREE_COVERED_BLACK_SCREEN=1.0`，`TREE_COVERED_DOOR_CLOSE=1.0`。
- 剩余 miss 主要集中在 `ROUTING_GUARDRAIL`，不建议为了指标直接硬编码边界样本。

目标：

- FT_001 next action hit 从 `0.2` 提升到至少 `0.6`。
- FT_002 next action hit 从 `0.5` 提升到至少 `0.7`。
- 不降低 Gate safety，不增加 mispass。

建议实现：

- 已完成：在 `eval.py` 输出每条 case 的 expected next action、实际 planner actions、active node、executed checks、evidence chain。
- 已完成：在 `planner.py` 中增加疑似根因/关键诊断节点的非 blocking `CONFIRMATION_CHECK`。
- 增加失败案例分析脚本或 eval detail view。
- 后续在 `planner.py` 中继续引入更细的排序因子：
  - 工单已执行检查语义。
  - transition condition 命中程度。
  - active node 是否被语义推进过。
  - RAG 证据是否提示某分支。
  - ReworkGuard 反证优先级。
  - 缺关键检测优先级。
- 对“已执行检查但没有明确 test_id”的语义检查做更强映射。
- 避免为了命中标注而硬编码 case id。

验收：

- 已完成：`pytest` 通过。
- 已完成：labeled v1 eval 通过。
- 已完成：`next_action_hit_rate` 从 `0.5789` 提升到 `0.9211`；LangGraph 安全分流后稳定在 `0.8947`。
- 已完成：`gate_mispass_count=0`。
- 已完成：`wrong_tree_misdiagnosis_count=0`。
- 后续验收：真实工单补充后，确认 `CONFIRMATION_CHECK` 没有过拟合当前 labeled v1 模板。

#### P0.2 强化 LangGraph 状态流转（第一轮已完成）

背景：

- 第一轮前 `workflow.py` 有 LangGraph 编排，但整体仍偏线性：
  - intake
  - classify
  - retrieve
  - plan
  - gate
  - report
  - replay
- 还没有真正体现强 Agent 的条件循环、工具执行分支和动态计划修订。
- 2026-05-28 已完成第一轮条件边改造：coverage 分流、case-only 分流、自动工具占位分流。

目标：

- 让 LangGraph 成为真正状态机，而不是线性包装。

建议实现：

- 已完成：增加条件边：
  - `route_by_coverage`
    - `COVERED` -> `retrieve_tree`
    - `UNSUPPORTED + PRODUCTION` -> `gate`
    - `UNSUPPORTED + DEVELOPMENT` -> `retrieve_evidence` / `case_only_plan`
  - `plan`
    - 有自动工具动作 -> `execute_auto_actions`
    - 有 HITL 动作 -> `gate/report` 并等待用户
    - 无动作但可报告 -> `gate`
  - `gate`
    - 已完成：存在 blocking HITL 动作 -> `wait_hitl` -> `report`
    - 已完成：`PASS` -> `gate_pass` -> `report`
    - 已完成：`GRAY` -> `gate_gray` -> `report`
    - 已完成：`FAIL` -> `gate_fail` -> `report`
- 已完成：将 `execute_auto_actions()` 接入图上的未来可插拔节点。
- 已完成：`DiagnosticState` 显式记录 `workflow_phase`、`waiting_for_human`、`waiting_action_ids` 和 `workflow_notes`。
- 暂缓：将 `execute_auto_actions()` 从空实现扩展为真实工具执行节点；诊断仍以人工 HITL 为主。
- 将 replay 记录拆成更细粒度事件。

验收：

- 已完成：无 UI 也能通过 engine 完成端到端诊断。
- 已完成：HITL 仍能在 Streamlit 中稳定等待人工输入。
- 已完成：unsupported 生产态不会再走取树/取证/Planner 空路径。
- 已完成：非 LangGraph fallback 与 graph 路由语义保持一致。
- 待完成：所有节点输出更细粒度 replay 事件。

#### P0.3 Eval 平台第一阶段（部分完成）

背景：

- 现在 eval 能输出汇总指标，但失败分析还不方便。

目标：

- 让 eval 不只是跑分，而能指导开发。

建议实现：

- 已完成脚本输出：`diagnostic_eval_details.jsonl`，每条包含：
  - case_id
  - eval_group
  - expected_route
  - predicted_route
  - expected_tree
  - predicted_tree
  - expected_leaf
  - predicted_leaf
  - expected_next_action
  - actual_planned_actions
  - gate expected/predicted
  - failure_tags
  - short_error_reason
- 已完成：Streamlit Eval 页增加：
  - 总览指标。
  - 分组指标。
  - 失败案例表格。
  - 按 group / failure tag 过滤失败案例。
  - 点击 case 展开 expected/predicted、planner actions、已执行检测、证据摘要。
- 已完成：增加 `datasets/eval_runs/{run_id}/` 版本化评测输出，包含 metadata、summary、results、details 和混淆分析文件。
- 已完成：Streamlit Eval 页支持历史 Run 列表、baseline/current 指标 delta、关键回归/普通 warning、新增失败和已修复 case。
- 后续增加：
  - Gate/报告详情 drill-down。
  - 已完成第一版：按树、节点、test 的混淆分析表。
  - 已完成第一版：历史版本趋势对比。
  - 已完成第一版：失败 case replay 回放摘要。
  - 后续增强：更细粒度 replay 事件时间轴、趋势图、按节点/test 的版本间差异归因。

验收：

- 已完成：UI 可查看失败案例。
- 已完成：开发者能直接定位 Planner 错在分类、active node、transition、证据还是排序。
- 已完成：UI 可比较不同 eval run 的指标变化。
- 已完成：UI 可查看树/节点/test 混淆分析和失败 case replay 摘要。

#### P0.4 保护 `.env` 与敏感信息

背景：

- 用户曾在对话中提供真实 key。
- 项目必须确保真实 key 不被写入代码、文档、测试、replay、eval 输出。

目标：

- 建立敏感信息防护。

建议实现：

- 增加测试或脚本扫描：
  - `sk-`
  - `DEEPSEEK_API_KEY=` 后非空真实值
  - `OPENAI_API_KEY=` 后非空真实值
- README 说明 key 轮换建议。
- replay 写入前过滤常见 secret 字段。

验收：

- 已完成：`.env`、运行缓存、replay、Tree Generation 运行输出、TreeProposal 运行输出和 eval 输出已由 `.gitignore` 排除在 Git 基线外。
- 扫描通过。
- `.env` 不被提交。
- replay 不包含 API key。

#### P0.5 Tree Generation Extraction Eval（第一版已完成）

背景：

- 当前 Tree Proposal Eval 已覆盖 schema、结构、test coverage、evidence binding、HITL pending 和 shadow eval。
- 但批量树生成还缺少专门评估“原文抽取质量”的指标，包括原文事实召回、grounding、幻觉率和 L1/L2/L3 链路逻辑性。
- 没有这层评测时，后续受控生产 TTL 写入会缺少发布前质量门禁。

目标：

- 建立 `tree_generation_extraction_v1` 评测族，作为 Tree Proposal Eval 的子层。
- 该评测不直接决定生产 PASS，只作为 DRAFT/CANDIDATE 晋升、专家审核和发布前材料的质量输入。

已完成：

- 新增 `src/ft_diag_agent/tree_generation_eval.py`。
- 新增指标：`ontology_structure_score`、`field_completeness_rate`、`source_fact_recall`、`grounding_precision`、`hallucination_rate`、`path_coherence_score`、`test_actionability_rate`、`contradiction_count`、`duplicate_semantic_rate`。
- 输出 `rubric_version`、`source_fact_rows`、`artifact_grounding_rows` 和 `path_coherence_rows`。
- 批量文档树生成完成后自动写入一条 `tree_generation_extraction_v1` eval result。
- `DRAFT_TREE -> CANDIDATE_TREE` 预审要求已有 extraction eval；结构、链路或幻觉 blocker 会阻塞晋升；`source_fact_recall` 仅作为 warning。
- Streamlit TreeProposal 审核页 Eval tab 支持运行和展示抽取质量评测。

待增强：

- 引入人工 gold fact set，替代 v1 的 deterministic source fact 近似召回。
- 增加 LLM-as-judge / critic 可选评测，但必须保留 deterministic fallback。
- 已完成：将 extraction eval 汇总进发布前 release/admission package，并作为生产 TTL 写入前门禁。

验收：

- 已完成：无 LLM 环境下可运行 deterministic eval。
- 已完成：`CONFIRMED` 字段不计入 hallucination 分母。
- 已完成：低 grounding、高 hallucination、链路不通顺可输出 blocker/warning。

### P1

#### P1.0 TreeChangeProposal 第一版（轻量闭环已完成）

背景：

- Tree Evolution 不只要发现新树，也要支持已有 Released/Gray/Draft Tree 的持续变更。
- 工艺变化、检测项不可执行、阈值变化、分支误判、返修反证等，不应直接覆盖生产 TTL，而应形成版本化 change proposal。

目标：

- 在现有 `TreeProposal` 体系中支持双轨：`NEW_TREE` 用于无树覆盖新增树，`TREE_CHANGE` 用于已有树的分支、检测项、阈值、condition、executor、scope 等变更。

已完成：

- `TreeProposal` 增加 `proposal_kind`、`target_tree_id`、`target_tree_version`、`change_types`、`change_summary`、`change_patch` 和 `drift_signals`。
- 新增 `TreeProposalKind.NEW_TREE / TREE_CHANGE`。
- 新增 change types：`ADD_BRANCH`、`REMOVE_BRANCH`、`UPDATE_TEST`、`UPDATE_THRESHOLD`、`UPDATE_TRANSITION_CONDITION`、`UPDATE_EXECUTOR_TYPE`、`UPDATE_SCOPE`、`MERGE_NODE`、`SPLIT_NODE`、`DEPRECATE_TEST`。
- `DiagnosticEngine` 在 covered case 中识别工艺/阈值/检测项/误判/返修信号，生成 `tree_change_proposal`，但不改变 Gate。
- `TreeProposalStore` 支持 upsert `TREE_CHANGE` proposal，并写入 case link 和 artifact snapshot。
- Streamlit 诊断工作台展示“已有树变更候选”，支持写入/更新 TreeProposal Store。
- Report 输出“已有树变更候选”章节。

待增强：

- 将 change proposal 接入专门的 change eval、diff reviewer 和 release artifact patch 生成。
- 在 replay 扫描中跨 runs 聚合同一 Released Tree 的变更信号。
- 为 TreeChangeProposal 增加专家审核 UI 的 patch diff 视图。

验收：

- 已完成：covered case 生成 `TREE_CHANGE` 不影响生产 Gate。
- 已完成：`TREE_CHANGE` 不写 TTL，只作为版本化 patch 审核输入。

#### P1.1 case-only 探索模式第二阶段

背景：

- 当前 case-only 已能基于历史工单/RAG/LLM/规则生成假设和 HITL 检查。
- 但还不是高准确率的无树诊断 Agent。
- 新产品定位下，case-only 不只是“无树替代诊断”，也是 TreeProposal 的发现入口。

目标：

- 让无故障树覆盖时的开发态诊断更像自主诊断专家。

建议实现：

- 增加 case-only 诊断循环：
  - 已完成：生成假设。
  - 已完成：选择下一轮可执行检查。
  - 已完成：人工填写结果。
  - 已完成：更新假设状态。
  - 已完成：抑制已执行检查并继续生成下一轮检查。
- 引入假设状态：
  - 已完成：`OPEN`
  - 已完成：`SUPPORTED`
  - 已完成：`REFUTED`
  - 已完成：`NEEDS_EVIDENCE`
- 引入反证优先级：
  - 优先选择能排除高风险错误路径的检查。
- 引入相似案例聚类：
  - 从历史工单中提取 failure mode、component、symptom、repair outcome。
- 将 case-only 输出写入 TreeProposal 候选输入：
  - 已完成：root cause family。
  - 已完成：candidate tests。
  - 已完成：evidence ids。
  - 已完成：human confirmed/refuted findings。
  - 已完成：`REFUTED` 假设不会进入候选 root cause family。

验收：

- NON_TREE_CASE_ONLY 的 hypothesis hit 保持或提升。
- case-only next action hit 保持 `>=0.9`。
- 报告始终标注不可生产放行。
- 关键探索结果能被 TreeProposal Store 关联。
- 已完成：人工检查后会更新假设状态并 replan，Gate 仍保持 `GRAY`。

#### P1.2 TreeProposal Store 第一版（批量文档入口已完成）

背景：

- 项目边界已从“只诊断、不生成故障树”升级为“生产诊断只用 Released Tree，同时负责动态树演化流程”。
- 现有 `FaultTreeGenerationRequest`、`FaultTreeRequestCluster` 和 `dynamic_tree_clusters.jsonl` 已经证明能从 replay 中发现候选树模式。
- 下一步需要把候选树从“导出文件”升级为可审核、可评测、可演进的 TreeProposal Store。

目标：

- 建立本地文件型 TreeProposal Store，作为未来数据库表的兼容原型。
- 将 unsupported development case 和跨 runs cluster 沉淀为可追踪 proposal。
- 保持安全边界：proposal 不等于生产树，不能让 Gate PASS。

建议实现：

- 已完成模型：
  - `TreeGenerationJob`
  - `TreeGenerationInputDocument`
  - `OntologyExtractionPlan`
  - `OntologyExtractionPass`
  - `OntologyValidationIssue`
  - `OntologyEntityDraft`
  - `SymptomTransitionDraft`
  - `TreeGenerationValidationReport`
  - `TreeGenerationArtifact`
  - `TreeProposal`
  - `TreeProposalStatus`
  - `TreeProposalCaseLink`
  - `TreeProposalEvalResult`
  - `TreeProposalReviewLog`
- 待完成模型：
  - 发布 manifest / rollback metadata 等正式发布模型
- 已完成文件型生成与审核入口：
  - `src/ft_diag_agent/tree_generation.py`
  - `src/ft_diag_agent/tree_proposals.py`
  - `data/tree_generation/jobs/`
  - `data/tree_generation/uploads/`
  - `data/tree_generation/artifacts/`
  - `data/tree_proposals/proposals.jsonl`
  - `data/tree_proposals/case_links.jsonl`
  - `data/tree_proposals/eval_results.jsonl`
  - `data/tree_proposals/review_logs.jsonl`
  - `data/tree_proposals/artifacts/{proposal_id}/`
- 已完成抽取核心强化：
  - `docs/tree_ontology_schema.md` 作为本体抽取真源规则。
  - 已补充故障树业务语义：`start=L1`、`inner=L2`、`root=L3`，root 不可再拆分。
  - 已补充 FieldStatus 生命周期：抽取阶段 `EXTRACTED_EXPLICIT/EXTRACTED_INFERRED/MISSING`，补全阶段 `SUGGESTED_*`，确认/验证阶段 `CONFIRMED/VERIFIED`。
  - 已补充 HITL 边界：树生成 HITL 用于补全知识库，诊断 HITL 用于执行当前案例人工检测。
  - LLM-first 多轮本体抽取：PASS_1 候选实体、PASS_2 实体分级和 start 合并、PASS_3 transition 绑定、结构校验后 PASS_4 修复。
  - 把 `docs/tree_gen_agent.md` 中 start/inner/root、动作/异常拆分、root 终止性、transition 绑定 test 等规则注入 prompt。
  - 兼容 `tree_gen_agent.md` 风格字段：`symptom_name/symptom_level`、`test_name`、`source/target/test_id`。
  - 兼容宽候选字段：`entities`、`root_causes`、`failure_modes`、`abnormal_states`、`checks`、`diagnostic_paths`、`causal_chains`。
  - 校验失败后的 LLM repair pass。
  - PASS_4 repair 不允许因 `EXTRACTED_INFERRED` / `MISSING` 低置信而删除待确认内容；代码层会恢复被误删的待确认实体、检查项、措施或 transition，并进入树生成 HITL 候选。
  - PASS_2 建图失败但 PASS_1 有候选实体时，确定性组装 `NEEDS_REPAIR_LLM_DRAFT`，避免回退旧规则抽取。
  - 每轮 `OntologyExtractionPass` 记录 `output_counts` / `output_preview` / `raw_output` / `raw_text`，UI 可查看适配预览、LLM parsed JSON 和模型原始响应文本；preview 已保留关键 status。
  - PASS_1 空 JSON 或全空候选会触发一次强约束重试。
  - PASS_1 适配器已兼容 `risk_notes` 字符串和对象型 `transition_hints`。
  - 任务标题、补充说明只能作为 metadata / prompt 上下文，不能作为候选实体或 transition 的证据来源。
  - 空图返回不再算成功抽取。
  - 多 chunk 输入和 source refs。
  - 规则 fallback 明确标记为 `LOW_CONF_DEBUG_DRAFT`。
  - `TreeGenerationArtifact.stage_timings` 持久化阶段耗时，Streamlit 运行时展示当前生成阶段和耗时表。
  - Streamlit 和 `scripts/render_tree_generation_tree.py` 已支持 Mermaid 树结构图，节点显示 level/status，边上显示 test。
  - Tree Generation job 列表按更新时间倒序，生成完成后自动选中新 job，并在表单中展示本次 LLM enable/provider/model 配置，避免旧任务低置信报错被误读为本次结果。
  - 树生成 HITL 已支持专家建议选项：LLM 基于原文 chunk、RAG 和领域/工艺/维修专家约束生成 `SUGGESTED_GROUNDED` / `SUGGESTED_LOW_CONF` 选项。
  - 用户确认建议、保留当前值或手动修订后，会写入 `TreeGenerationHitlDecision`，将对应草稿字段推进到 `CONFIRMED`，并重跑结构校验和确定性 BFS 预览。
  - 已修复树生成 HITL 补全候选写回时修改已实例化 selectbox widget key 导致的 Streamlit `session_state` 异常。
  - `TreeProposalStore` 已支持 proposal upsert、case link、eval result、review log 和 artifact snapshot。
  - `TreeProposal` 已增加 `source_request_id` / `source_cluster_id`，用于追溯 case-only request 和跨 runs cluster 来源。
  - Streamlit 已增加 TreeProposal 审核页，第一阶段支持批准 `DRAFT_TREE -> CANDIDATE_TREE`、请求修改和拒绝。
  - TreeProposal 审核页已增加“候选树结构”tab：有 artifact 时展示真实 L1/L2/L3 树图和节点/transition 状态；无 artifact 时展示 `DISCOVERY_ONLY` skeleton。
  - TreeProposal 审核页已增加从来源输入到生产 TTL 发布的 8 步流程状态条。
  - `Tree Proposal Eval` 第一版已实现确定性指标：schema validation、test coverage、evidence binding、HITL confirmed/pending 和 unsafe blockers，并可在审核页运行和展示。
- 从现有输入生成 proposal：
  - 已完成：`FaultTreeGenerationRequest`
  - 已完成：`FaultTreeRequestCluster`
  - `ReplayRecord`
  - case-only hypotheses/findings/actions
  - RAG evidence
- 更新 `.gitignore`、README 清理方式和开发者说明。
- 已完成代码修正：
  - 抽取阶段默认 status 不再使用 `SUGGESTED_GROUNDED` / `SUGGESTED_LOW_CONF`。
  - 若 LLM 在抽取阶段返回 `SUGGESTED_*`，系统会映射为 `EXTRACTED_INFERRED`。
  - draft entity properties 标记 `needs_generation_hitl` / `hitl_reasons`，用于后续树生成 HITL 补全。
  - 已实现从 `TreeGenerationArtifact` 扫描 `MISSING` / `EXTRACTED_INFERRED` 字段生成 HITL 补全候选列表。
  - 已实现基于原文 + RAG + 专家知识的 HITL 建议选项生成和人工确认写回。
  - 已实现 HITL 确认写回后隐藏已解决建议项，并提示待补全字段数量变化。
  - 已实现从 unsupported development case 的 `FaultTreeGenerationRequest` 写入/更新 `DRAFT_TREE` proposal。
  - 已实现从跨 runs `FaultTreeRequestCluster` 选中或批量写入/更新 `DRAFT_TREE` proposal。
  - 已实现 request/cluster 第二入口的稳定 proposal id、case link 去重和 artifact snapshot。
- 待完成代码：
  - 将 TreeProposal 审核扩展为批量审核流、replay-based Tree Proposal Eval 和发布前审计。
  - 将审核通过的草稿树转换为正式 TTL / release manifest / rollback 信息的发布链路。

验收：

- 已完成：批量文档入口能生成 `DRAFT_TREE` proposal。
- 已完成：候选本体草案、transition、结构校验、确定性 BFS 重建预览可查看。
- 已完成：树生成核心不再把规则抽取当主路径；无 LLM 时只能生成低置信 debug 草案。
- 已完成：Tree Generation 可展示阶段状态、阶段耗时、HITL 补全候选和 Mermaid 树结构图。
- 已完成：TreeProposal Store 可写入 proposals、review logs 和 artifact snapshot；审核 UI 支持 `DRAFT_TREE -> CANDIDATE_TREE`。
- 已完成：Tree Proposal Eval 第一版可写入 `eval_results.jsonl`，并在审核 UI 展示最新指标和阻塞项。
- 已完成：TreeProposal 审核 UI 可展示 proposed tree 结构、抽取状态、边上 test 和生命周期状态条，不再只依赖 JSON/预审文本。
- 已完成：Streamlit sidebar 可在 `诊断工作台` 与 `树生成工作台` 之间切换；批量文档生成和审核已从诊断主页面拆出。
- 已完成：unsupported development case 能生成或更新 `DRAFT_TREE` proposal。
- 已完成：跨 runs cluster 能选中或批量生成/更新 proposal。
- 已完成：proposal 与 source cases、evidence、candidate tests 可追溯。
- 不影响 Released Tree 生产诊断。
- Gate 对 proposal 仍不能 PASS。

#### P1.3 TreeProposal 生命周期规则

背景：

- 有了 Proposal Store 后，需要让状态晋升有确定规则，避免“看起来像树”就进入灰度或生产。
- 2026-06-03 已完成第一版确定性晋升预审；预审只输出建议和阻塞项，不自动晋升，最终仍由人工审核决定。

目标：

- 已完成：实现 `DRAFT_TREE -> CANDIDATE_TREE` 的第一版规则评估。
- 已完成：为后续 `CANDIDATE_TREE -> GRAY_TREE -> RELEASED_TREE` 留出审核和 eval 扩展点。

建议实现：

- 已完成 `DRAFT_TREE -> CANDIDATE_TREE` 预审：
  - start 不是 `MISSING` 占位。
  - 至少 1 个 root cause family。
  - 至少 1 个 candidate test。
  - 有 source case / case link。
  - 有 evidence ids 或 source refs。
  - 有 `TreeGenerationArtifact` 快照。
  - Tree Proposal Eval 无 unsafe findings。
  - 支持 case 数建议阈值为 3，不足时 warning。
  - evidence binding rate 建议阈值为 60%，不足时 warning。
  - 已完成第一版跨 proposal 聚合预审：按 phenomenon bucket 汇总同类 proposal、root cause family、repeated test、人工确认有效率和高风险反证。
  - 聚合预审只追加 blocker/warning/satisfied/recommended action，不自动晋升。
  - 预审结果写入 review log 的 `precheck_result`，供审计。
- `CANDIDATE_TREE -> GRAY_TREE`：
  - 已完成第一版预审框架。
  - 当前会继承 DRAFT 预审，并要求专家初审日志。
  - 已完成 replay-based Tree Proposal Eval / shadow diagnosis simulation 第一版：读取 `runs/*.jsonl` 或传入 replay records，按 proposal start/root/test/evidence 与历史 replay 做离线匹配，输出相关 case、支持率、检查命中率、证据命中率、失败案例和 unsafe findings。
  - 已完成 GRAY 准入材料包第一版：把 proposed tree artifact、source case/evidence、结构 eval、shadow eval、候选审核日志和适用范围逐项展示，并供预审消费。
  - 缺少 shadow eval 或 shadow eval 有 unsafe findings 时会阻塞；shadow eval 无阻塞时允许提交人工审核进入 `GRAY_TREE`。
- `GRAY_TREE -> RELEASED_TREE`：
  - 已完成第一版预审框架。
  - 已完成 release manifest / rollback metadata / TTL diff 发布前材料包第一版。
  - 已完成 RELEASED 准入材料包第一版：把 release artifact、release manifest、rollback metadata、TTL diff、generated TTL preview、golden/shadow 结果、`CANDIDATE_TREE -> GRAY_TREE` 审核日志和专家正式签核逐项展示，并供预审消费。
  - 已完成 Released Tree registry / 生产 TTL 写入审计第一版：`RELEASED_TREE` proposal 通过 release artifact、签核、rollback、`tree_generation_extraction_v1`、TTL parse、tree_id 唯一性和生产 TTL 重复检查后，写入 `READY_FOR_TTL_WRITE` registry 记录。
  - 已完成受控生产 TTL 写入与回滚演练第一版：写入动作必须消费 READY 记录，复核 release artifact TTL hash，写入前生成备份，追加 generated TTL preview，成功后 registry entry 变为 `REGISTERED`；rollback dry-run 验证备份可恢复，正式 rollback 恢复 TTL 并标记 `ROLLED_BACK`。

验收：

- 已完成：状态晋升只生成建议/阻塞项，不自动发布。
- 已完成：不满足门槛时输出阻塞原因、warning 和 recommended actions。
- 已完成：审核日志可追溯，包含预审快照。
- 已完成：replay-based Tree Proposal Eval / shadow diagnosis simulation 第一版。
- 已完成：release manifest、rollback metadata、TTL diff 发布前材料包第一版。
- 已完成：GRAY / RELEASED 准入材料包第一版，审核 UI 和晋升预审共用同一套材料状态。
- 已完成：Released Tree registry / 生产 TTL 写入审计、受控写入执行和 rollback dry-run/execute 第一版。
- 已完成：TreeProposal 审核 UI 增加“跨 Proposal 聚合”tab，展示同类现象、root cause family、重复 test、人工确认率和高风险反证。
- 待完成：线上/准线上 shadow service、发布后监控、自动触发回滚策略和生产注册服务化。

#### P1.4 UI 诊断时间线（第一轮已完成）

背景：

- 当前 UI 能录入 HITL，但复杂流程下用户不容易理解“为什么现在查这个”。
- 第一轮前诊断概览、证据、Planner、Gate 和 Replay 分散在不同 tab，专家需要从 JSON 中反推因果链。

目标：

- 提供诊断时间线，让人工检测、Planner、证据、Gate 更可解释。

建议实现：

- 增加时间线组件：
  - 已完成：工单输入。
  - 已完成：分类/覆盖。
  - 已完成：路径或 case-only 探索计划。
  - 已完成：Planner 检查动作。
  - 已完成：人工结果与 evidence 更新。
  - 已完成：Gate 状态。
  - 已完成：报告与 Replay。
- 每个动作显示：
  - 已完成：action_type / test / target。
  - 已完成：planner reason。
  - 已完成：risk_notes。
  - 已完成：evidence_refs。
  - 已完成：Gate 影响。
- 新增 `diagnostic_explain.py` 作为纯后端解释层，Streamlit 只消费解释结果，避免把因果规则散落在 UI 中。
- 诊断页默认先展示“诊断时间线”“Planner / Evidence / Gate 因果解释”“证据摘要”，原始 Gate JSON、报告 JSON 和证据链 JSON 保留为折叠审计材料。

验收：

- 已完成：用户能清楚看到诊断是否已开始、处于等待 HITL、Gate 阻塞或报告完成。
- 已完成：用户能从因果解释表看到某个 Planner 动作为什么被规划、对应证据是否已支持、对 Gate 有何影响。
- 已完成：证据摘要按来源、支持对象、强度和解释展示，原始 JSON 不再是第一阅读入口。
- 待完成：更细粒度 replay 事件回放、报告版本对比、生产级视觉 polish。

#### P1.5 RAG 证据质量增强

背景：

- 当前 RAG 返回证据候选，但证据质量评分还比较粗。

目标：

- 增强 RAG 的可追溯性和证据可信度。

建议实现：

- chunk metadata 增加：
  - doc_type
  - tree_id
  - phenomenon
  - work_order_id
  - source_file
  - section
  - page
  - sanitized
- EvidenceItem 增加更细分类：
  - supports
  - contradicts
  - contextual
- 检索结果进入 Planner 前做 evidence judge。
- UI 展示来源文件、段落、脱敏状态。

验收：

- RAG 命中可追溯。
- labeled eval 不出现标签泄漏。
- Planner 不把弱上下文当作根因确认。

#### P1.6 Report 版本与人工修订

背景：

- 当前报告可生成，但修订闭环还弱。

目标：

- 让报告成为可审核、可修订、可追踪版本。

建议实现：

- `report_versions` 加入 `DiagnosticState`。
- Streamlit 支持人工确认/驳回报告。
- 驳回原因写入 preference/replay。
- 报告字段完整率进入 eval。

验收：

- 每次报告修改可追踪。
- preference_pairs 能包含报告偏好样本。

### P2

#### P2.1 训练数据导出增强

背景：

- 当前已有 SFT/preference 数据出口，但样本质量门控不足。

目标：

- 输出更适合后续 SFT / LoRA / QLoRA / DPO 的数据。

建议实现：

- planner SFT：
  - 输入 state summary。
  - 输出 action list。
  - 包含 rejected action。
- report SFT：
  - 输入 state + evidence + gate。
  - 输出 markdown/json report。
- preference pairs：
  - accepted vs rejected planner action。
  - accepted vs rejected report section。
- 数据质量过滤：
  - Gate PASS 但证据不足的样本剔除。
  - case-only 不可生产 PASS 的样本单独标记。
  - 人工反馈缺失的样本降权。

验收：

- 导出 JSONL 可被后续训练脚本直接消费。
- 每条样本带来源 replay id。

#### P2.2 Tool Executor 协议完善

背景：

- 真实 SPC/BOM/曲线判异接口暂缓，但接口协议要提前稳住。

目标：

- 保持后续替换工具实现时不改状态机。

建议实现：

- 完善 `TestExecutionSpec`：
  - executor_type
  - tool_name
  - input_schema
  - result_schema
  - transition_mapping
  - timeout
  - retry policy
  - human confirmation required
- 增加 stub 工具测试。
- 增加自动工具失败回退 HITL 的策略。

验收：

- 接入真实工具只需新增 tool，不需要重写 Planner/Gate。

#### P2.3 数据质量测试扩充

背景：

- 当前已有 TTL 缺字段、RAG 无结果、LLM fallback 等测试基础，但还可扩展。

目标：

- 防止真实数据进入后系统静默误判。

建议测试：

- TTL 缺 `testName`。
- TTL 缺 `condition`。
- TTL 缺 measure。
- transition 指向不存在节点。
- 多棵树 start symptom 相似。
- 文档为空。
- 文档重复。
- CSV 编码异常。
- LLM JSON malformed。
- DeepSeek timeout。
- 工单否定词：“无黑屏”“无车门抱怨”。
- 多系统事故。

验收：

- 数据质量风险进入 `data_quality_notes` 或 Gate risk notes。
- 不因脏数据直接 PASS。

#### P2.4 README 与开发者说明持续维护

背景：

- `docs/developer_guide.md` 已存在，但每次变更都要同步。

目标：

- 保证交接文档不会过期。

建议实现：

- 每次模块变更时同步：
  - `README.md`
  - `docs/developer_guide.md`
  - `PROJECT_STATE.md`
  - `TASKS.md`
- 增加“文档是否更新”的 PR checklist。

验收：

- 新会话能仅靠三个根目录文件 + developer guide 接续开发。

#### P2.5 Replay 可视化

背景：

- replay 已记录，但阅读 JSONL 不方便。

目标：

- 在 UI 中能回放一次诊断。

建议实现：

- 读取 `runs/*.jsonl`。
- 展示 state_before/state_after diff。
- 展示 planner_output、tool_call、gate_result、human_decision。
- 支持按 case_id 搜索。

验收：

- 可以复盘一次误诊或 Gate 阻断原因。

### P3

#### P3.1 真正训练 LoRA / QLoRA / DPO

背景：

- 当前只是保留训练数据能力。
- 不建议在样本量不足时训练。

启动条件：

- 至少数百到数千条高质量 replay。
- 有明确人工 accepted/rejected。
- Eval 平台能稳定防回归。
- 数据脱敏和合规通过。

目标：

- 训练 planner SFT。
- 训练 report SFT。
- 基于 preference pairs 做 DPO 或类似偏好优化。

验收：

- 离线 eval 优于规则/LLM baseline。
- Gate safety 不下降。
- 不引入不可解释高风险动作。

#### P3.2 真实 SPC/BOM/曲线判异接口接入

背景：

- 当前全部 test 暂按人工 HITL。

启动条件：

- 上游故障树生成 Agent 已标注 test executor type。
- 真实接口协议稳定。
- 有测试环境或 mock server。

目标：

- SPC 查询工艺参数。
- BOM 查询配置和零件版本。
- 曲线判异模型分析点焊曲线。
- 历史质量案例系统查询。

验收：

- 工具结果转 EvidenceItem。
- 工具失败自动进入 HITL。
- 所有工具调用有 trace 和 latency。

#### P3.3 Neo4j 或图数据库替换 RDF 内存层

背景：

- 当前 RDF 内存解析足够第一版。

启动条件：

- 故障树规模明显增大。
- 需要跨树复杂图查询。
- 需要多人共享服务化图存储。

目标：

- 保留 `GraphRepository` 接口。
- 将 `RdfFaultTreeRepository` 替换或并存为 Neo4j implementation。

验收：

- 上层 Planner/Gate/Report 不感知存储替换。

#### P3.4 多 Agent / A2A 诊断协作

背景：

- 长期可能需要分类 Agent、Planner Agent、工具 Agent、报告 Agent、故障树生成 Agent 协作。

目标：

- 形成可控的多 Agent 架构，而不是让一个 LLM 自由诊断。

建议边界：

- Router Agent：只做路由和覆盖判断。
- Planner Agent：生成下一步检查。
- Evidence Agent：整理证据和反证。
- Gate Agent：不使用 LLM 覆盖规则，只生成解释。
- Report Agent：生成报告草稿。
- FaultTree Draft Agent：只在开发态生成候选树。

验收：

- 所有 Agent 输出均 Pydantic 校验。
- 所有 Agent 决策进入 replay。
- Gate 仍是确定性安全边界。

#### P3.5 Tree Evolution 高级能力

背景：

- TreeProposal Store 和基础生命周期进入 P1 后，P3 只保留更高级的服务化、自动化和多工厂复用能力。

目标：

- 让 Tree Evolution 从项目本地文件型 store 升级为可多人协作、可跨工厂复用、可回滚发布的服务化能力。

高级能力：

- TreeProposal 数据库化。
- 多工厂/多车型 proposal 合并与隔离。
- Gray Tree shadow diagnosis 对比平台。
- Released Tree registry 服务化。
- TTL release manifest 与 rollback 自动化。
- 与故障树生成 Agent 的 A2A 调用协议。
- 专家审核工作流和权限管理。

指标：

- 树覆盖率。
- 节点命中率。
- test 可执行率。
- 诊断准确率。
- 返修率。
- Gate 误放行率。

验收：

- 本地 store 可迁移到数据库而不改诊断主链路。
- 能解释为什么一棵树从 Gray 晋升到 Released。
- 发布和回滚都有审计日志。

## 当前推荐执行顺序

1. 线上/准线上 shadow diagnosis 服务化与发布后监控。
2. 自动触发 rollback 策略和生产 registry 服务化。
3. `P1.0 TreeChangeProposal` 增强：change eval、patch diff、跨 runs 聚合和 release patch artifact。
4. `P1.1 case-only 探索模式第二阶段`
5. `P0.3 Eval 平台第一阶段：趋势对比 / 混淆分析`
6. `P0.4 保护 .env 与敏感信息`

原因：

- 项目边界已升级为“诊断 + Tree Evolution”，批量文档入口、case-only/cluster 第二入口、DRAFT 晋升预审、离线 replay/shadow eval 第一版、发布前材料包第一版、准入材料包第一版、registry READY 队列、受控 TTL 写入和回滚演练第一版已完成；当前最缺的是正式 shadow/gray 服务化和发布后监控。
- `tree_generation_extraction_v1` 已补上原文抽取质量门禁，后续 TTL 写入不能只看 schema/结构 eval，还要看 grounding、幻觉率和链路逻辑。
- TreeProposal 预审、registry READY、受控 TTL 写入和手动 rollback 已有第一版；真正生产运行仍需要发布后监控、自动触发回滚策略和服务化 registry。
- Tree Evolution 已从“新增树 proposal”扩展为 `NEW_TREE / TREE_CHANGE` 双轨，真实生产中的工艺、阈值和检测项变化应走 TreeChangeProposal。
- case-only 仍重要，但现在应服务于临时诊断和 TreeProposal 发现，而不是替代 Released Tree 主链路。
- Eval 平台仍需增强，并拆分为 Classification Eval、Diagnosis Eval、Tree Proposal Eval。
- 敏感信息防护仍应尽早自动化，避免 replay 或文档意外泄漏 key。
