# AGENTS.md

本文件记录长期不变的协作规则、工程边界和验证命令。新会话或其他智能体进入本项目后，应先阅读本文件，再阅读 `PROJECT_STATE.md` 和 `TASKS.md`。

## 1. 协作规则

- 不要一味同意用户方案。若方案存在工程风险、产品风险、评测风险或长期不可维护风险，应明确指出，并给出更专业、更适合本项目的替代方案。
- 每次修改代码、文档、配置、数据脚本前，先用简短、易懂的语言说明：
  - 即将修改哪些文件或模块。
  - 使用什么方法实现。
  - 解决什么问题。
  - 可能产生什么影响。
- 说明后等待用户确认，再开始修改。用户明确说“确认”“继续”“按这个做”等，可视作授权本轮相关修改。
- 不要覆盖用户或其他智能体的未说明改动。遇到脏工作区时，先读清楚相关文件，再基于现状增量修改。
- 不要执行破坏性命令，例如 `git reset --hard`、`git checkout -- <file>`、批量删除数据等，除非用户明确要求。
- 不要在代码、文档、测试、replay、eval 输出里写入真实 API key。`.env` 只在本地使用，不应提交。

## 2. 工程规范

项目按“系统干净、项目隔离、依赖可追踪、配置可复现、垃圾可清理”的规范工作。

- Python 依赖放在项目 `.venv` 或 `uv` 管理环境中，不污染系统 Python。
- 不使用 `sudo pip`、`sudo npm`、`curl | sudo bash` 等不可控安装方式。
- 新增依赖时必须同步更新：
  - `pyproject.toml`
  - `README.md`
  - `.env.example`，如涉及环境变量
  - `.gitignore`，如涉及缓存、模型、索引、运行输出目录
- 新增缓存、模型、向量库、replay、eval 输出目录时，必须说明目录用途和清理方式。
- 系统级改动必须先说明原因、影响范围和卸载方式，并等待用户确认。

## 3. 产品与架构边界

本项目是“故障树引导诊断 + Tree Evolution Agent”。生产诊断主链路只使用已审核发布的 `Released Tree`；同时，系统负责从无树工单中发现候选故障树机会，组织 `TreeProposal`、临时 case-only 诊断、评测、人工审核、灰度验证和发布流转。动态生成的树在成为 `Released Tree` 之前，不能进入生产 PASS 主链路。

当前产品形态：

- `Streamlit` 诊断工作台。
- Python 核心诊断引擎。
- RDF/TTL 故障树数据层。
- 文档与历史工单 RAG。
- DeepSeek/OpenAI-compatible LLM provider 抽象。
- LangGraph 状态流转骨架。
- Planner、Tool Registry、HITL、Gate、Report、Replay、Eval、训练数据导出闭环。
- TreeProposal / Tree Evolution：从 unsupported case-only 工单沉淀候选树，经过 DRAFT、CANDIDATE、GRAY、RELEASED、REJECTED 等生命周期状态。

关键边界：

- 生产态诊断必须走人工审核发布的 `Released Tree`。
- 无故障树覆盖时，生产态不能 PASS，应给出不支持或风险阻断。
- LangGraph 是诊断主状态机：覆盖路由、case-only 路由、Gate 后 `WAITING_HITL / GATE_PASS / GATE_GRAY / GATE_FAIL` 状态标记、Report 和 Replay 都在同一 `DiagnosticState` 上运行；自动工具节点仍只预留，不执行真实自动工具。
- 开发态允许进入 `CASE_ONLY_EXPLORATORY`，基于历史工单/RAG/LLM 自主探索，同时生成或更新 `TreeProposal`；报告必须显式标注不可生产放行。
- case-only 不基于未审核新树进行诊断；它先执行“假设 -> HITL 检查 -> 更新假设状态 -> 再规划”的探索循环，再把全程证据沉淀为 `FaultTreeGenerationRequest` / `TreeProposal` 输入。
- case-only 人工检查结果只能更新 `ExploratoryFinding`、hypothesis 状态、证据链和后续动作；不能直接修改生产 TTL，也不能直接把 `REFUTED` 假设固化为候选 root cause family。
- `TreeProposal` 第二入口已支持从开发态 `FaultTreeGenerationRequest` 和跨 runs `FaultTreeRequestCluster` 写入/更新 `DRAFT_TREE`，只作为审核与评测输入。
- TreeProposal 晋升预审只输出 `READY_FOR_REVIEW / NEEDS_MORE_EVIDENCE / BLOCKED / NOT_APPLICABLE` 和审核材料，不自动变更状态；最终晋升必须由人工审核决定。
- TreeProposal 跨 proposal 聚合只能作为预审和专家审核证据；即使同类 bucket/root/test 统计良好，也不能绕过人工审核、准入材料、shadow eval、release manifest 和 registry/TTL 审计。
- TreeProposal 审核页必须优先服务专家阅读：展示 proposed tree 的 L1/L2/L3 结构、字段状态、transition/test 和从来源到生产 TTL 的流程状态；JSON 只能作为折叠调试/审计信息。
- Streamlit 左侧 sidebar 分为 `诊断工作台` 和 `树生成工作台`：批量文档生成、树生成 HITL 和 TreeProposal 审核属于树生成工作台；诊断中发现无树覆盖的第二入口保留在诊断工作台。
- `TreeProposal`、`DRAFT_TREE`、`CANDIDATE_TREE`、`GRAY_TREE` 都不能自动生产 PASS；只有 `RELEASED_TREE` 可以成为生产主链路。
- Released Tree registry 当前支持 `READY_FOR_TTL_WRITE` 审计队列、受控生产 TTL 写入和 rollback 演练：写入必须消费 READY 记录、复核 release artifact / TTL hash / production TTL parse / tree_id 去重、写入前生成备份，成功后标记 `REGISTERED`；rollback 可 dry-run 或恢复备份并标记 `ROLLED_BACK`。这些动作不自动改变 Gate 或分类器运行时缓存。
- 动态树生成必须遵守 `docs/tree_gen_agent.md`：LLM/Agent 只维护 `FailureSymptom`、`OntologyTest`、`OntologyMeasure`、`SymptomTransition` 等本体实体和关系，最终 `FaultTree` 由确定性 BFS 重建，不能让 LLM 直接输出最终 `FaultTree.symptom_ids`。
- 本体抽取字段、FieldStatus、关系和校验要求以 `docs/tree_ontology_schema.md` 为准。
- 树生成主路径必须 LLM-first；规则抽取只能作为 `LOW_CONF_DEBUG_DRAFT` 调试兜底，不能当作高质量候选树。
- 树生成 repair 阶段不能因为 `EXTRACTED_INFERRED` / `MISSING` 低置信而删除草案内容；这些内容应进入树生成 HITL 补全/确认队列。
- 树生成 HITL 建议必须先锚定原文 chunk，再结合 RAG，最后才用领域/工艺/维修专家知识补强；LLM 只能给 `SUGGESTED_*` 候选，人工确认后才可写回为 `CONFIRMED`。
- 任何 TreeProposal 晋升都必须有 replay/eval 结果、证据绑定、人工审核日志和回滚信息。
- LLM 只能做归一化、分类增强、解释、case-only 探索计划、报告润色或 rerank 辅助。关键 Gate 结论不能由 LLM 覆盖。
- 真实 SPC/BOM/曲线判异接口暂缓接入；当前故障树 test 全部按人工 HITL 处理。
- 后续由故障树生成 Agent 标注 test 的执行类型后，再扩展 `TestExecutionSpec` 和 Tool Executor 路由。

## 4. 模块边界

主要模块位于 `src/ft_diag_agent/`：

- `models.py`：Pydantic 领域模型，包括 `DiagnosticState`、`WorkOrder`、`DiagnosticAction`、`EvidenceItem`、`GateResult`、`ReplayRecord`、case-only 模型、返修风险模型等。
- `fault_tree.py`：TTL/RDF 解析、故障树索引、transition/test/measure/path 枚举。
- `work_orders.py`：mock/真实工单解析、粘贴文本解析。
- `classifier.py`：工单分类、故障树覆盖判断、生产态/开发态路由。
- `llm.py`：LLM provider 抽象，当前支持 DeepSeek/OpenAI-compatible 调用与规则兜底。
- `rag.py`：文档扫描、chunk、metadata、Chroma/lexical fallback 检索、eval 标签脱敏。
- `planner.py`：基于当前故障树节点或候选路径生成下一步 `DiagnosticAction`。
- `case_only_planner.py`：无故障树覆盖时生成探索假设、探索计划和 HITL 动作。
- `dynamic_tree.py`：unsupported development 工单的动态故障树候选请求和跨 runs 聚类。
- `tree_generation.py`：批量质量报告/8D/SOP/FMEA/维修资料生成 DRAFT_TREE、TreeGenerationJob 和 TreeProposal 的第一入口。
- `tree_proposals.py`：本地 TreeProposal Store、case link / eval / review log / artifact snapshot，以及从 case-only request 或 dynamic cluster 生成/更新 DRAFT_TREE 的第二入口。
- `tree_proposal_precheck.py`：TreeProposal 晋升预审，生成阻塞项、警告项、满足项和建议动作，并随人工审核日志持久化。
- `tree_proposal_analytics.py`：跨 proposal 聚合分析，按 phenomenon bucket、root cause family、repeated test、人工确认率和高风险反证辅助预审；不自动晋升、不写生产 TTL。
- `tree_proposal_view.py`：TreeProposal 审核视图辅助，生成生命周期状态、artifact 树表格和无 artifact 的 proposal skeleton。
- `tree_admission.py`：GRAY / RELEASED 准入材料包，供审核 UI 和晋升预审共用。
- `tree_release.py`：release manifest、rollback metadata、TTL diff 和 runtime-compatible TTL preview 发布前材料。
- `released_tree_registry.py`：Released Tree registry、生产 TTL 写入审计、受控写入执行和 rollback 演练。
- `rework_guard.py`：识别返修、前次误判、无效处置、相似返修案例，并生成反证检查建议。
- `tools.py`：统一 Tool Registry、Pydantic schema、工具调用记录与 evidence 映射。
- `gate.py`：确定性风险门禁，输出 `PASS / GRAY / FAIL`。
- `diagnostic_explain.py`：只读 `DiagnosticState`，生成诊断时间线、Planner/Evidence/Gate 因果解释和证据摘要；不改变 Gate、Planner 或 Replay。
- `report.py`：Markdown + JSON 报告生成。
- `workflow.py`：核心诊断引擎与 LangGraph 编排。
- `eval.py`：离线评测、指标计算、labeled v1 数据集读取。
- `replay.py`：运行轨迹记录。

UI 位于 `app/streamlit_app.py`：

- 工单选择/粘贴。
- 诊断启动。
- 当前节点与 HITL 检测录入。
- 诊断时间线、Planner/Evidence/Gate 因果解释和证据摘要。
- case-only 探索展示。
- 批量文档预生成候选树入口。
- 返修/误判风险展示。
- Gate、报告、Replay、Eval 页面。

## 5. Streamlit 开发注意事项

Streamlit 每次交互会重新执行脚本，因此必须注意：

- 长生命周期对象使用 `st.cache_resource`，例如故障树 repository、RAG、engine。
- 诊断状态放入 `st.session_state["diag_state"]`。
- 按钮触发后立即写入 `session_state`，必要时使用 `st.rerun()`。
- 不要在普通控件变化时自动运行批量 eval、重建索引或重新开始诊断。
- 大计算只在明确按钮点击时执行，例如文档扫描、eval。
- UI 修改后应重启本地 Streamlit 服务，并做浏览器冒烟检查。

## 6. 配置与密钥

本地真实配置文件：

- `.env`

示例配置文件：

- `.env.example`

LLM 相关变量只记录变量名，不记录真实值：

- `LLM_ENABLE`
- `LLM_PROVIDER`
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_FLASH_MODEL`
- `DEEPSEEK_PRO_MODEL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

当前默认策略：

- 开发调试、结构化抽取、常规报告优先使用 flash 模型。
- 低置信分类、复杂解释、case-only 自主探索可使用 pro 模型。
- 无 key 或 LLM 调用失败时，系统必须回退到规则/RAG路径，并记录风险。

## 7. 常用命令

安装依赖：

```bash
uv sync
```

如果已经有 `.venv`：

```bash
.venv/bin/python -m pip install -e .
```

启动 Streamlit：

```bash
STREAMLIT_BROWSER_GATHER_USAGE_STATS=false .venv/bin/streamlit run app/streamlit_app.py --server.address 127.0.0.1 --server.port 8502 --server.headless true
```

静态检查：

```bash
.venv/bin/ruff check .
```

单元测试：

```bash
.venv/bin/python -m pytest
```

编译检查：

```bash
.venv/bin/python -m compileall src app tests
```

labeled v1 诊断评测：

```bash
.venv/bin/python -m ft_diag_agent.eval --diagnostic-eval --eval-suite labeled_v1 --eval-output-dir datasets/eval_results_labeled_v1 --eval-runs-dir datasets/eval_runs
```

推荐完整验证顺序：

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest
.venv/bin/python -m compileall src app tests
.venv/bin/python -m ft_diag_agent.eval --diagnostic-eval --eval-suite labeled_v1 --eval-output-dir datasets/eval_results_labeled_v1 --eval-runs-dir datasets/eval_runs
```

## 8. 数据目录

- `corrected_fault_tree_instances.ttl`：当前故障树 TTL 样例，包含 FT_001 车机黑屏、FT_002 车门无法关闭。
- `data/raw_docs/`：SOP、FMEA、维修手册、mock 工单、labeled eval 数据。
- `data/chroma/`：Chroma 向量库持久化目录，可重建。
- `data/tree_generation/`：批量文档树生成 job/artifact 输出目录，可清理后重新生成。
- `data/tree_proposals/`：TreeProposal、case link、eval、review log、artifact/release 材料目录，可按审核需要清理。
- `data/released_trees/`：Released Tree registry、生产 TTL 写入/回滚审计和写入前备份目录，可在确认不需要发布追溯或回滚后清理。
- `runs/`：replay trace 输出目录，可按需要清理。
- `datasets/`：导出的 SFT/preference/eval 数据、版本化 eval runs 和评测结果。

不要把 `.env`、大型缓存、向量库、运行输出误写入代码逻辑。

## 9. 设计原则

- 第一版即按最终产品架构开发，不走“临时 MVP 后续推倒”的路线。
- 允许分阶段交付，但每一阶段都应服务最终架构。
- 故障树诊断是生产主线，case-only 是开发态探索能力。
- Gate 是确定性安全边界，不允许 LLM 覆盖。
- 所有人工选择、工具调用、证据、报告都要进入 replay，为 offline eval、SFT、Preference、DPO 做数据闭环。
- 新能力必须可测试、可重放、可评估。
