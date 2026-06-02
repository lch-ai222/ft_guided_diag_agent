# 故障树本体抽取规范

更新时间：2026-05-30

本文档是树生成/抽取的核心要求。所有批量文档生成、工单触发生成、外部故障树生成 Agent 接入，都必须优先遵守本文档。它不是展示文档，而是 ontology 抽取、校验、修复和人工审核的真源规则。

## 1. 项目背景与故障树定义

本项目中的故障树不是普通树状摘要，也不是为了展示而生成的流程图。它是一种可执行的诊断知识库：

- 从人、产线、检测系统或工单中能观察到的失效表象开始。
- 通过检查、观察、分析、测试逐步细化异常状态。
- 最终定位到不可再拆分的根本原因。
- 诊断 Agent 在运行时沿 `SymptomTransition` 执行检查，依据检查结果决定下一步流向。

故障树层级固定为：

- `start = L1 = FailureSymptom = 入口故障/失效现象`：一棵树有且仅有一个 start，所有枝叶都围绕它展开。它必须是用户、现场、产线或检测系统能直接观察/报告的故障表象。
- `inner = L2 = 中间异常状态`：经过检查、观察、分析后得到的更具体异常状态，比 L1 更细化，但还没有到最终根因。L2 可以有多层，只要它仍然可以继续被诊断分解。
- `root = L3 = 根因`：不可再拆分的最终根因。若某个“root”还能继续拆成更底层原因，则它不是 L3 root，而应降级为 inner。

## 2. 总原则

- 不允许 LLM 直接输出最终 `FaultTree` 作为生产树。
- LLM/Agent 只负责抽取和维护本体草案：
  - `FailureSymptom`
  - `OntologyTest`
  - `OntologyMeasure`
  - `SymptomTransition`
- 最终 `FaultTree` 必须由 start 节点沿 `SymptomTransition.source -> target` 确定性 BFS 重建。
- 抽取结果必须保留字段状态、证据来源和缺口表达。
- 低质量或无 LLM 的规则兜底结果只能标记为 `LOW_CONF_DEBUG_DRAFT`，不能作为高质量候选树。

## 3. FieldStatus 生命周期

所有可空字段必须有对应状态。FieldStatus 不是简单置信度标签，而是字段在“抽取 -> 补全 -> 确认 -> 验证”生命周期中的状态。

| 状态 | 含义 |
|---|---|
| `EXTRACTED_EXPLICIT` | 抽取阶段使用。原文中直接出现且信息充分，置信度高 |
| `EXTRACTED_INFERRED` | 抽取阶段使用。原文有部分支持，但需要弱推断，置信度相对低 |
| `MISSING` | 抽取阶段使用。原文完全缺失，字段为空，必须进入补全 |
| `SUGGESTED_GROUNDED` | 补全阶段使用。LLM 基于原文语境、RAG 和行业知识生成的有证据约束建议 |
| `SUGGESTED_LOW_CONF` | 补全阶段使用。线索较弱或场景适配不足的低置信建议 |
| `CONFIRMED` | 人工确认 |
| `VERIFIED` | 经过验证动作确认 |

强制规则：

- 字段为空或 `null` 时，对应 status 必须是 `MISSING`。
- 不允许用空字符串冒充已抽取。
- 抽取阶段只允许使用 `EXTRACTED_EXPLICIT`、`EXTRACTED_INFERRED`、`MISSING`。
- `SUGGESTED_GROUNDED` 和 `SUGGESTED_LOW_CONF` 只允许在补全阶段使用，不应冒充原文抽取结果。
- `EXTRACTED_EXPLICIT` 默认不触发 HITL 补全，但仍可被人工审核。
- `EXTRACTED_INFERRED` 和 `MISSING` 必须进入 HITL 补全候选队列。
- repair 阶段不得仅因为字段状态是 `EXTRACTED_INFERRED` 或 `MISSING` 而删除实体、检查项、措施或 transition；这些状态表达“待补全/待确认”，不是“无效”。只有重复、方向非法、引用无法补齐或明确违反本体约束时才允许删除或合并。
- 所有实体和关系应尽量绑定 `chunk_ids` / `source_refs`。

## 4. HITL 补全与诊断 HITL 的区别

本项目有两类 HITL，语义不同，不能混用。

### 4.1 树生成阶段 HITL

树生成阶段 HITL 的目标是补全和审核知识库字段。

触发条件：

- 字段状态为 `EXTRACTED_INFERRED`。
- 字段状态为 `MISSING`。
- 图结构校验提示缺失关键节点、检查项、transition 或证据。

补全方式：

- LLM 必须基于原文语境生成候选建议。
- LLM 必须结合 RAG 检索到的产品、维修手册、FMEA、SOP、8D、质量报告等资料。
- LLM 可以使用自身领域/工艺/维修知识，但只能作为辅助推理，不能脱离当前原文和 RAG 场景自由发挥。
- UI 应提供类似 planning mode 的补问/候选选项，由用户作为领域、工艺、维修或质量专家确认。
- 建议阶段只能生成 `SUGGESTED_GROUNDED` / `SUGGESTED_LOW_CONF` 选项，不能直接改写草稿树。
- 用户确认建议、保留当前值或手动修订后，系统才写入 `TreeGenerationHitlDecision`，将对应字段推进为 `CONFIRMED`，并重跑结构校验和确定性 BFS 预览。
- 用户确认后，字段状态更新为 `CONFIRMED`。

### 4.2 诊断阶段 HITL

诊断阶段 HITL 的目标不是补全知识库，而是执行当前案例中的人工检测。

典型流程：

- Planner 选择当前节点下的某个 `OntologyTest`。
- 若该 test 是人工检测，则 UI 提示用户执行检测并录入结果。
- Agent 将人工检测结果写入证据链。
- 诊断流根据 test 结果和 `SymptomTransition.condition` 决定下一步走向。
- 检测结果充分支持某字段或状态时，可将相关诊断证据标记为 `VERIFIED`。

## 5. FailureSymptom

`FailureSymptom` 表达“异常状态是什么”，不是动作、流程或处置。

必填字段：

- `symptom_id`
- `symptom_name`
- `symptom_name_status`
- `symptom_level`
- `symptom_desc_status`

可选字段：

- `symptom_desc`
- `symptom_chunk_ids`
- `measure_ids`

层级：

- `start`：L1，入口故障/失效现象。一棵树有且仅有一个 start，必须是客户、产线、检测系统可以直接观察或报告的最大公约数现象。
- `inner`：L2，中间异常状态。必须是经过检查、观察、分析后得到的更具体异常状态。L2 可以有多层，只要仍可继续诊断分解。
- `root`：L3，根因。必须是不可再拆分、可处置、可验证闭环的最终原因。若还能继续拆分，则不是 root，应作为 inner。

禁止：

- 把“检查、验证、复测、读取、分析、处理、整改、培训”建成 `FailureSymptom`。
- 把“未知原因”“待确认原因”作为可发布 root。
- 同一失效域拆成多个语义重叠 start。
- 将可继续拆分的中间原因标成 root。

## 6. OntologyTest

`OntologyTest` 表达“如何判断/检查”，不是异常状态。

必填字段：

- `test_id`
- `test_name_status`
- `test_unit_status`
- `test_hilim_status`
- `test_lolim_status`
- `test_rule_status`
- `test_target_status`
- `test_desc_status`

可选字段：

- `test_name`
- `test_unit`
- `test_hilim`
- `test_lolim`
- `test_rule`
- `test_target`
- `test_desc`
- `test_chunk_ids`

规则：

- “测量、读取、观察、拆检、比对、复测、验证、确认”等动作优先建为 `OntologyTest`。
- 一条 `SymptomTransition` 必须至少关联一个真实存在的 `OntologyTest`。
- 原文缺检查项时，可以创建占位 test，但 `test_name_status=MISSING`。

## 7. OntologyMeasure

`OntologyMeasure` 表达处置措施、维修动作、工艺改善。

必填字段：

- `measure_id`
- `measure_name`
- `measure_name_status`
- `measure_desc_status`

可选字段：

- `measure_desc`
- `measure_chunk_ids`

规则：

- “更换、维修、调整、修复、整改、刷新、标定、返修”等动作优先建为 `OntologyMeasure`。
- 措施通常挂到 root 节点。
- 措施不是检查项，也不是异常状态。

## 8. SymptomTransition

`SymptomTransition` 表达“如何从上级异常定位到下级异常”。

必填字段：

- `source`
- `target`
- `test_id`
- `condition_status`
- `transition_desc_status`

可选字段：

- `condition`
- `transition_desc`
- `transition_chunk_ids`

规则：

- 方向必须是 `start -> inner/root` 或 `inner -> inner/root`。
- 不允许 `root` 有出边。
- `start` 不应有入边。
- 同一 `source,target` 只能有一条 transition。
- `test_id` 不允许为空数组。
- 如果原文说“通过读取日志发现 IIC 通信超时”：
  - `OntologyTest = 读取日志`
  - `FailureSymptom = IIC 通信超时`
  - `SymptomTransition = 上级异常 -> IIC 通信超时`

## 9. FaultTree

`FaultTree` 是全局诊断图上的诱导子图，只保存节点集合。

规则：

- Agent 不直接创建/维护最终 `FaultTree`。
- 每个 start 节点通过 BFS 自动生成一棵树。
- 树包含从 start 可达的所有 `FailureSymptom`。
- 修改 transition 后必须重新 rebuild。

## 10. 抽取流程

高质量抽取必须至少包含以下轮次：

1. 文档 chunk 与证据编号。
2. LLM 第一轮：抽取候选实体。
3. LLM 第二轮：实体去重、start 合并、层级修正。
4. LLM 第三轮：生成 `SymptomTransition`，每条边绑定 test。
5. 结构校验。
6. 若有 error，LLM 修复轮根据错误修复本体草案。
7. 确定性 BFS rebuild preview。
8. 生成 `TreeProposal(status=DRAFT_TREE)`。

当前批量入口将流程实现为：

- `PASS_1`：候选实体抽取。
- `PASS_2`：实体分类、start 合并和 `start/inner/root` 分级。
- `PASS_3`：`SymptomTransition` 生成和 test 绑定。
- `VALIDATE`：确定性结构校验。
- `PASS_4`：仅根据校验问题修复结构。
- deterministic BFS rebuild preview。

规则 fallback 只允许在 LLM 不可用时生成 `LOW_CONF_DEBUG_DRAFT`，用于调试 UI 和流程，不作为高质量候选树。
规则 fallback 和 LLM 抽取都不得把生成任务标题、补充说明当作证据来源；这些内容只能作为 job metadata / prompt 上下文。若输入资料本身缺少入口现象、根因或检查项，应输出 `MISSING` 占位并进入生成阶段 HITL 补全。

## 11. 校验规则

必须校验：

- 有且仅有一个 start，除非任务明确要求多棵树。
- 至少一个 root。
- 全局 transition 图无环。
- 每个 transition 引用至少一个 test。
- 每个 transition 引用的 source/target/test 均存在。
- root 无出边。
- start 无入边。
- 每个 inner 有路径到至少一个 root。
- 每个 inner/root 可反向回溯到 start。
- 同一 `source,target` 不重复。
- 空字段和 `MISSING` 状态一致。
- 抽取阶段不得输出 `SUGGESTED_GROUNDED` / `SUGGESTED_LOW_CONF` 作为已抽取字段。
- root 节点不得继续拥有下游诊断分解。

校验输出必须可操作：

- `severity`
- `rule_id`
- `message`
- `entity_refs`
- `repair_hint`

## 12. 常见反模式

- LLM 直接输出整棵最终树。
- 把动作建为 symptom。
- root 不是最深可处置异常。
- 把还能继续拆分的中间异常标成 root。
- transition 没有 test。
- 多个同义 start。
- 只有 JSON schema 校验，没有图规则校验。
- 只跑一轮抽取，不做修复。
- 没有 chunk/source 引用。
- 无 LLM 时把规则抽取结果当可用树。
- 抽取阶段用 `SUGGESTED_*` 混淆原文抽取和补全建议。
- LLM 脱离当前原文和 RAG，仅凭行业常识生成补全选项。
