# Tree Evolution 开发方案

更新时间：2026-06-01

本文档定义本项目从“只做故障树诊断”升级为“故障树诊断 + 动态树演化”的目标架构、生命周期、评测口径和开发计划。它是 `docs/tree_gen_agent.md` 的下游落地方案：`tree_gen_agent.md` 说明如何高质量生成/重建故障树，本文件说明诊断 Agent 何时提出生成需求、如何评测、如何审核，以及何时允许进入生产。

## 1. 核心结论

最终形态不是“故障树路线”和“无树智能诊断路线”二选一，而是三条链路共存：

1. `Released Tree Diagnosis`
   - 生产主链路。
   - 只使用人工审核、评测通过、有版本和回滚信息的 `RELEASED_TREE`。
   - Gate 可以在证据满足时 `PASS`。

2. `Exploratory Diagnosis + Tree Discovery`
   - 无树新工单的开发态临时诊断。
   - 使用历史工单/RAG/LLM/人工 HITL 生成探索计划和临时建议。
   - Gate 只能 `GRAY`，报告必须标注不可生产放行。
   - 同时沉淀 TreeProposal 证据、假设、检查项和 replay。

3. `Tree Evolution Pipeline`
   - 把多个无树工单中的稳定诊断模式聚合为候选故障树。
   - 经过生成、校验、离线 replay、专家审核、灰度验证，最终发布为 `RELEASED_TREE`。

这比纯 case-only 智能诊断更可控，也比完全静态树库更能持续覆盖新故障类型。

## 2. 边界原则

- 生产诊断只能使用 `RELEASED_TREE`。
- `DRAFT_TREE`、`CANDIDATE_TREE`、`GRAY_TREE` 都不能让 Gate 自动 `PASS`。
- 无树 case-only 诊断是临时诊断和发现入口，不是生产主链路。
- 动态树生成必须遵守 `docs/tree_gen_agent.md`：
  - 不让 LLM 直接输出最终 `FaultTree`。
  - LLM/Agent 只维护 `FailureSymptom`、`OntologyTest`、`OntologyMeasure`、`SymptomTransition`。
  - 最终 `FaultTree` 由 start 节点沿 `SymptomTransition` 确定性 BFS 重建。
  - 所有生成结果必须结构校验、证据绑定、人工审核。
- 任何晋升状态都必须可追溯：
  - 来源工单。
  - replay。
  - 评测结果。
  - 专家审核日志。
  - 版本号。
  - rollback 信息。

## 3. TreeProposal Store

第一版建议使用项目本地文件型 store，而不是只靠目录移动表达状态。

推荐目录：

```text
data/tree_proposals/
  proposals.jsonl
  case_links.jsonl
  eval_results.jsonl
  review_logs.jsonl
  artifacts/
    TP-xxxx/
      proposal.json
      draft_ontology.ttl
      rebuilt_tree.ttl
      validation_report.json
      replay_report.json
      release_manifest.json
```

未来迁移数据库时，对应表：

- `tree_proposal`
- `tree_proposal_case_link`
- `tree_proposal_eval_result`
- `tree_proposal_review_log`
- `tree_proposal_artifact`

文件型 store 的好处：

- 项目隔离，不引入外部服务。
- 易于 gitignore 和清理。
- JSONL 适合追加审计日志。
- 后续可平滑迁移到 SQLite/Postgres/Neo4j。

## 4. 核心模型

### 4.1 `TreeProposal`

输入：

- `FaultTreeGenerationRequest`
- `FaultTreeRequestCluster`
- `ReplayRecord`
- case-only hypotheses/findings/actions
- RAG evidence
- 人工 HITL 结果

核心字段：

- `proposal_id`
- `status`
- `phenomenon_bucket`
- `candidate_start_symptom`
- `candidate_failure_domain`
- `root_cause_families`
- `candidate_tests`
- `candidate_transitions`
- `source_case_ids`
- `evidence_ids`
- `source_refs`
- `confidence_summary`
- `risk_notes`
- `created_at`
- `updated_at`

输出：

- TreeProposal store 记录。
- UI 候选树审核页。
- Tree Proposal Eval 输入。
- tree generation agent 输入。

### 4.2 `TreeProposalCaseLink`

用途：

- 记录每个 proposal 支持或反驳哪些工单。

核心字段：

- `proposal_id`
- `case_id`
- `work_order_id`
- `link_type=SUPPORTS / REFUTES / AMBIGUOUS`
- `matched_root_cause_family`
- `useful_tests`
- `human_confirmed`
- `notes`

### 4.3 `TreeProposalEvalResult`

用途：

- 保存候选树在 classification、diagnosis、tree proposal 三类 eval 中的结果。

核心字段：

- `proposal_id`
- `eval_suite`
- `status_at_eval`
- `metrics`
- `failure_cases`
- `unsafe_findings`
- `created_at`

### 4.4 `TreeProposalReviewLog`

用途：

- 保存人工审核和状态变更。

核心字段：

- `proposal_id`
- `from_status`
- `to_status`
- `reviewer`
- `decision=APPROVE / REJECT / REQUEST_CHANGES`
- `rationale`
- `required_changes`
- `created_at`

## 5. 生命周期

### 5.1 `DRAFT_TREE`

来源：

- 单个 unsupported development case。
- 跨 runs 聚类发现相似 case。
- 人工从 Replay 页发起。

允许能力：

- 用于 case-only 临时诊断参考。
- 用于生成候选本体建模请求。
- 用于聚合更多工单证据。

禁止能力：

- 不能进入生产树库。
- 不能让 Gate `PASS`。
- 不能对生产工单自动给最终根因。

### 5.2 `CANDIDATE_TREE`

建议升级规则：

- 同一 `phenomenon_bucket` 下 `DRAFT_TREE` 或支持 case 数量 >= 5。
- 至少 3 个案例支持同一 root cause family。
- 关键检查项重复出现 >= 3 次。
- 人工确认有效率 >= 60%。
- 没有高风险反证。

生成内容：

- 候选本体实体。
- 候选 `SymptomTransition`。
- 候选检查项。
- 候选处置措施。
- 初步校验报告。

禁止能力：

- 不能生产 PASS。
- 不能自动替换 Released Tree。

### 5.3 `GRAY_TREE`

建议升级规则：

- offline replay 通过。
- test coverage 达标。
- 每个 L4/root cause 至少有一个 test。
- 关键节点有 evidence。
- unsafe suggestion rate 低于阈值。
- 专家初审通过。

允许能力：

- 可作为辅助诊断建议。
- 可在开发态或灰度环境提示“该树处于灰度验证中”。
- 可收集 shadow diagnosis 对比指标。

禁止能力：

- 不能自动 `PASS`。
- 不能覆盖 Released Tree 的生产结论。

### 5.4 `RELEASED_TREE`

建议升级规则：

- 专家正式审核通过。
- golden set 通过。
- wrong route / wrong root cause 风险可控。
- 适用车型、工厂、工位、版本范围明确。
- 版本号生成。
- rollback 信息完整。
- release manifest 完整。

允许能力：

- 进入生产主链路。
- 工单分类可路由到该树。
- Gate 可在证据满足时 `PASS`。

### 5.5 `REJECTED`

进入条件：

- 证据不足。
- 与已有 Released Tree 重复。
- root cause family 不稳定。
- 检查项不可执行。
- 离线 replay 或专家审核失败。
- 存在不可接受的误放行风险。

处理：

- 保留审计记录。
- 不删除来源 replay。
- 后续如果新证据充分，可创建新 proposal，不直接复活旧状态。

## 6. 三类 Eval

### 6.1 Classification Eval

验证工单进入哪条链路。

指标：

- `coverage_accuracy`
- `route_accuracy`
- `tree_selection_accuracy`
- `unsupported_detection_accuracy`
- `ambiguous_detection_accuracy`
- `wrong_tree_route_rate`
- `guardrail_misroute_count`

输入：

- 标注工单。
- Released Tree registry。
- Gray Tree registry，作为辅助但不允许生产 PASS。

输出：

- 分类评测报告。
- 错误路由 case 明细。
- tree proposal 误吸收风险提示。

### 6.2 Diagnosis Eval

验证诊断过程质量。

指标：

- `final_leaf_accuracy`
- `top3_root_cause_recall`
- `first_useful_test_rate`
- `average_steps_to_root_cause`
- `human_action_count`
- `tool_call_success_rate`
- `unsafe_pass_rate`
- `gate_mispass_count`
- `wrong_root_cause_rate`

输入：

- Released/Gray/Candidate tree。
- replay/golden set。
- 人工结果或模拟 test result。

输出：

- 诊断链路评测报告。
- 失败 case drill-down。
- Planner/Gate 风险定位。

### 6.3 Tree Proposal Eval

验证动态生成候选树是否值得晋升。

指标：

- `schema_valid_rate`
- `test_coverage_rate`
- `evidence_binding_rate`
- `duplicate_branch_rate`
- `unreachable_node_rate`
- `missing_test_rate`
- `misleading_branch_rate`
- `expert_acceptance_rate`
- `replay_success_rate`
- `unsafe_suggestion_rate`

输入：

- TreeProposal。
- 候选本体 TTL。
- rebuilt FaultTree。
- replay/golden set。
- 专家审核结果。

输出：

- 是否允许 `DRAFT -> CANDIDATE`。
- 是否允许 `CANDIDATE -> GRAY`。
- 是否允许 `GRAY -> RELEASED`。
- 失败原因和要求补充的证据/检查项。

## 7. 与 `docs/tree_gen_agent.md` 的关系

诊断 Agent 不应该把 `TreeProposal` 直接当作最终 FaultTree。

正确交接方式：

1. 诊断 Agent 发现 unsupported 工单模式。
2. 诊断 Agent 生成或更新 TreeProposal。
3. TreeProposal 达到 `CANDIDATE_TREE` 门槛。
4. 调用或人工运行故障树生成 Agent。
5. 生成 Agent 查询来源证据和现有本体。
6. 生成 Agent 写入 `FailureSymptom`、`OntologyTest`、`OntologyMeasure`、`SymptomTransition`。
7. 运行 ontology validation。
8. 由确定性 `rebuild_fault_trees` 生成 FaultTree。
9. 诊断 Agent 执行 Tree Proposal Eval。
10. 专家审核。
11. 进入 `GRAY_TREE` 或 `RELEASED_TREE`。

必须避免：

- 让 LLM 直接输出最终树 JSON 后用于生产。
- 让 LLM 直接维护 `FaultTree.symptom_ids`。
- 无审核地把 case-only 诊断结果写入 TTL。
- 因为几个相似工单就自动发布生产树。

## 8. 开发计划

### 阶段 A：文档与边界更新

目标：

- 更新项目定位。
- 明确 Tree Evolution 是正式产品能力。
- 明确生产 PASS 仍只属于 Released Tree。

交付：

- README、AGENTS、Developer Guide、PROJECT_STATE、TASKS 更新。
- 本文件作为实现蓝图。

### 阶段 B：TreeProposal Store 与批量文档生成入口第一版（部分完成）

目标：

- 把现有 `FaultTreeGenerationRequest` / `FaultTreeRequestCluster` 升级为可持久化的 TreeProposal。
- 支持用户批量上传质量报告、8D、SOP、FMEA、维修资料，提前生成 `DRAFT_TREE` proposal。

交付：

- 已完成第一批模型：
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
  - release manifest
  - rollback metadata
  - TreeProposal artifact manifest / 发布版本索引
- 已完成模块：
  - `src/ft_diag_agent/tree_generation.py`
  - `src/ft_diag_agent/tree_proposals.py`
- 已完成核心要求：
  - 新增 `docs/tree_ontology_schema.md` 作为 ontology 抽取真源规则。
  - 批量入口改为 LLM-first 多轮本体抽取。
  - `PASS_1` 抽取候选异常状态、检查项、措施和诊断链提示。
  - `PASS_2` 做实体分类、同义 start 合并和 `start/inner/root` 分级。
  - `PASS_3` 基于已分级实体生成 `SymptomTransition` 并在边上绑定 test。
  - 校验失败后触发 `PASS_4` LLM repair pass。
  - `PASS_4` 不允许因 `EXTRACTED_INFERRED` / `MISSING` 低置信而删除待确认内容；代码层会恢复被误删的实体、检查项、措施或 transition，并送入树生成 HITL 候选队列。
  - 结构校验输出 `OntologyValidationIssue`。
  - 多 chunk 输入保留 `chunk_id` / `source_path`。
  - 规则 fallback 只标记为 `LOW_CONF_DEBUG_DRAFT`，不作为高质量候选树。
  - `TreeGenerationArtifact.stage_timings` 持久化各阶段耗时，Streamlit 运行时展示当前阶段和耗时表。
  - Streamlit 和 `scripts/render_tree_generation_tree.py` 支持 Mermaid 树结构可视化，节点显示 level/status，边显示绑定 test。
  - `generation_hitl_items(artifact)` 能从 `EXTRACTED_INFERRED` / `MISSING` 字段生成树生成 HITL 补全候选。
  - `TreeGenerationHitlSuggestion` / `TreeGenerationHitlDecision` 支持基于原文 chunk、RAG 和专家知识的建议选项，以及人工确认写回草稿字段。
  - Tree Generation UI 展示本次 LLM enable/provider/model 配置，生成后自动选中新 job，历史 job 按更新时间倒序。
  - `TreeProposalStore` 统一读写 proposals、case links、eval results、review logs 和 artifact snapshots。
  - Streamlit TreeProposal 审核页支持 proposal 状态筛选、审核日志查看和第一阶段 `DRAFT_TREE -> CANDIDATE_TREE` 审核动作。
  - Tree Proposal Eval 第一版提供确定性 schema/test/evidence/HITL 指标和 unsafe blockers，并写入 `eval_results.jsonl`。
- 已完成目录：
  - `data/tree_generation/`
  - `data/tree_proposals/`
- 更新 `.gitignore` 和 README 清理命令。

验收：

- 已完成：批量文档入口产生 `TreeProposal(status=DRAFT_TREE)`。
- 已完成：生成本体草案、结构校验和确定性 BFS 重建预览。
- 已完成：Tree Generation 展示运行阶段、阶段耗时、HITL 补全候选和 Mermaid 树结构图。
- 已完成：树生成阶段 HITL 专家建议选项和人工确认写回；确认后字段状态进入 `CONFIRMED` 并重跑校验/预览。
- 已完成：Tree Generation 生成后默认展示最新 job，避免历史低置信任务被误认为当前运行结果。
- 已完成：将 HITL 决策接入 TreeProposal review log，并提供基础审核页。
- 已完成：Tree Proposal Eval 第一版确定性指标和审核页展示。
- 待完成：批量审核流、replay-based Tree Proposal Eval 和发布前审计。
- 待完成：审核通过草稿树发布为正式 TTL，并生成 release manifest / rollback metadata。
- 待完成：unsupported development case 直接生成/更新 TreeProposal。
- 待完成：跨 runs cluster 可生成/更新 TreeProposal。
- 不影响现有生产诊断。

### 阶段 C：生命周期规则

目标：

- 实现 `DRAFT -> CANDIDATE` 的规则判断。

交付：

- phenomenon bucket 聚合。
- root cause family 聚合。
- repeated test 统计。
- 人工确认有效率统计。
- 高风险反证检查。

验收：

- 支持 case 数不足时不升级。
- 达到门槛时只给出升级建议，不自动发布。

### 阶段 D：Tree Proposal Eval

目标：

- 建立候选树专用评测。

交付：

- schema validation 指标。
- test coverage 指标。
- evidence binding 指标。
- duplicate/unreachable/missing test 指标。
- replay success 指标。
- unsafe suggestion 指标。

验收：

- 已完成第一版：每个 proposal 可运行确定性 eval 并写入 eval result。
- 待完成增强：接入 replay success、unsafe suggestion 深度指标和版本对比。
- Eval 失败能输出阻塞原因。

### 阶段 E：人工审核 UI

目标：

- 在 Streamlit 中审核 TreeProposal。

交付：

- Proposal 列表。
- 状态筛选。
- 来源 case 查看。
- evidence/test/root cause family 查看。
- 审核日志写入。
- 请求修改/拒绝/晋升操作。

验收：

- 审核动作可追溯。
- 未审核 proposal 不能变成 Gray/Released。

### 阶段 F：Gray Tree 与 Released Tree

目标：

- 建立灰度树和发布树的安全接入。

交付：

- Gray Tree registry。
- Released Tree registry。
- release manifest。
- rollback metadata。
- 分类器区分 Released vs Gray。

验收：

- Gray Tree 只辅助诊断，不能 PASS。
- Released Tree 才能进入生产主链路。
- rollback 后分类器不再路由到对应版本。

## 9. 当前已有基础

已经完成：

- case-only exploratory 诊断。
- `FaultTreeGenerationRequest`。
- `FaultTreeRequestCluster`。
- 批量文档 Tree Generation 入口：LLM-first Pass1-4、结构校验、确定性 BFS 重建、`DRAFT_TREE` proposal、阶段耗时、HITL 候选扫描和 Mermaid 树结构图。
- `TreeProposal`、`TreeProposalCaseLink`、`TreeProposalEvalResult`、`TreeProposalReviewLog` 基础模型。
- 跨 `runs/*.jsonl` 聚类导出 `datasets/dynamic_tree_clusters.jsonl`。
- Replay/Eval/Dataset 基础。
- Streamlit Replay 页查看动态树候选聚类。

下一步最合适做：

1. 从 dynamic cluster / unsupported development case 生成或更新 TreeProposal。
2. Tree Proposal Eval 指标和发布前审计。
3. TreeProposal 批量审核流。
4. DRAFT -> CANDIDATE 规则评估。
