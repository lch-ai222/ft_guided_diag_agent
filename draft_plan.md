⸻

制造质量诊断 Agent – 模块化执行方案

⸻

状态说明（2026-06-01）：

本文是项目早期模块化草案，不代表当前最新开发进展。当前实现状态和下一步优先级以 `PROJECT_STATE.md`、`TASKS.md`、`docs/developer_guide.md` 和 `docs/tree_evolution_plan.md` 为准。当前产品边界已经升级为“故障树引导诊断 + Tree Evolution Agent”：生产诊断只使用已审核发布的 `Released Tree`，批量 Tree Generation 只生成 `DRAFT_TREE` proposal，并已支持 LLM-first Pass1-4、结构校验、阶段耗时、HITL 补全候选和 Mermaid 树结构图；完整树生成 HITL 写回、审核、发布 manifest、rollback metadata 和正式 TTL 发布仍待完成。

⸻

1️⃣ Intake / 现象归一化模块

* 功能描述：接收故障现象描述，解析原始文本、车型、VIN、工厂、工位等信息，生成标准化故障现象和上下文。
* 输入：

{
  "raw_input": "低速行驶时左前门异响",
  "vehicle_project": "X01",
  "vin_list": ["VIN001","VIN002"],
  "factory": "A工厂",
  "station": "总装线-P12",
  "timestamp": "2026-05-19"
}

* 输出：

{
  "phenomenon": "左前门异响",
  "vehicle_info": {"project":"X01","VINs":["VIN001","VIN002"],"factory":"A工厂","station":"P12"},
  "context": {"time_window":"最近7天","scenario":"EOL检测"}
}

* 技术：
    * LLM + prompt template
    * 关键实体抽取（Pydantic/regex）
    * Embedding 相似现象匹配
* 学习/练习：
    * Prompt engineering
    * 文本归一化、实体识别
    * Python 数据结构处理

⸻

2️⃣ Fault Tree Retrieval / 故障树检索模块

* 功能描述：根据归一化现象检索故障树，返回相关路径和检测项。
* 输入：

{"phenomenon": "左前门异响"}

* 输出：

[
  {
    "path": ["左前门异响","车门系统","门锁/铰链/密封条","装配间隙异常"],
    "tests": ["检查门锁扭矩","检查铰链间隙","路测复现"],
    "evidence": ["历史案例A","8D报告B"]
  }
]

* 技术：
    * SQL / Graph DB（Neo4j）
    * Tree traversal + embedding similarity
    * RAG（可选）
* 学习/练习：
    * Graph/树结构处理
    * 向量检索
    * 故障树数据建模

⸻

3️⃣ Case Retrieval / 历史案例检索

* 功能描述：检索历史工单、维修案例、质量报告，用作候选根因证据。
* 输入：

{"phenomenon": "左前门异响","vehicle_project":"X01","top_k":5}

* 输出：

[
  {"case_id":"C001","root_cause":"门锁装配间隙异常","tests":["扭矩检查","路测复现"],"repair_action":"调整门锁装配"},
  {"case_id":"C002","root_cause":"铰链间隙异常","tests":["铰链检查"],"repair_action":"调整铰链"}
]

* 技术：
    * Embedding + vector DB（Chroma/FAISS）
    * Hybrid search（关键词 + embedding）
    * reranking（BGE reranker）
* 学习/练习：
    * Embedding 搜索
    * SQL + vector DB
    * RAG pipeline

⸻

4️⃣ RAG / 文档检索模块

* 功能描述：检索 SOP/FMEA/维修手册等文档，提供候选根因或检测项参考。
* 输入：

{"component":"门锁","failure_mode":"装配间隙异常","top_k":3}

* 输出：

[
  {"doc_id":"D001","text":"门锁扭矩应在20~25Nm，超出需调整","source":"SOP"},
  {"doc_id":"D002","text":"铰链间隙大于0.5mm需返工","source":"FMEA"}
]

* 技术：
    * Vector DB + embedding
    * LLM summarization（生成可用检测建议）
    * Chunking + metadata filter
* 学习/练习：
    * RAG pipeline
    * 文档 chunking
    * 向量检索与重排

⸻

5️⃣ Planner / 动态诊断计划生成

* 功能描述：根据现有证据、候选根因和工具可用性，生成下一步检测/验证计划。
* 输入：

{
  "candidate_causes": ["门锁装配间隙异常","铰链间隙异常"],
  "current_evidence": ["历史案例A","SPC异常"],
  "tools_available": ["SPC工具","人工检查"]
}

* 输出：

[
  {"test_id":"T001","target_cause":"门锁装配间隙异常","type":"AUTO","tool":"SPC工具","priority":1},
  {"test_id":"T002","target_cause":"铰链间隙异常","type":"HUMAN","tool":null,"priority":2}
]

* 技术：
    * Rule-based scoring / hybrid heuristic
    * LLM structured output（可选）
    * LangGraph + state transitions
* 学习/练习：
    * Decision scoring
    * Planner implementation
    * LangGraph 状态机

⸻

6️⃣ Tool Registry / 工具调用

* 功能描述：统一封装所有可调用工具（SPC、BOM查询、FP-Growth规则、RAG、人工输入等）。
* 输入/输出：视工具而定，例如：

SPC 工具：

输入: {"metric":"门锁扭矩","station":"P12","batch":"BATCH001"}
输出: {"anomaly_detected":true,"value":18,"evidence_strength":0.82}

FP-Growth 规则工具：

输入: {"phenomenon":"底盘异响","fault_type":"悬架"}
输出: {"rule":"底盘异响->更换下摆臂衬套","support":0.12,"confidence":0.68}

* 技术：
    * LangChain Tools
    * Python API / SQL 查询 / function calling
* 学习/练习：
    * 工具封装
    * function calling
    * 输入输出 schema 设计

⸻

7️⃣ DiagnosticState / 状态管理

* 功能描述：维护 Agent 状态，包括候选根因、已执行测试、证据、Gate结果。
* 输入/输出：

class DiagnosticState(TypedDict):
    case_id: str
    candidate_causes: list
    executed_tests: list
    evidence_chain: list
    gate_status: str

* 技术：
    * LangGraph + Python dict / Pydantic
    * 状态读写接口
* 学习/练习：
    * 状态机设计
    * 多步骤数据更新

⸻

8️⃣ Gate / 风险门禁

* 功能描述：检查当前诊断输出是否满足风险控制要求。
* 输入：DiagnosticState
* 输出：

{"status":"GRAY","required_actions":["补充门锁扭矩检测"]}

* 技术：
    * Python rule engine / LLM judge
    * Threshold checks / schema validation
* 学习/练习：
    * 风险门禁逻辑
    * PASS/GRAY/FAIL 状态管理

⸻

9️⃣ HITL / 人工交互模块

* 功能描述：Agent 证据不足或 Gate 阻塞时，请求人工输入并回填状态。
* 输入：

{"required_tests":["门锁扭矩检测"]}

* 输出：

{"human_results":[{"test_id":"T001","value":18}]}

* 技术：
    * Streamlit 表单 / FastAPI POST
    * State 回填
* 学习/练习：
    * 人机交互设计
    * 数据回填机制

⸻

🔟 Report Generator / 诊断报告

* 功能描述：整合候选根因、检测结果、证据链、处置建议，生成标准化报告。
* 输入：

{"DiagnosticState": {...}}

* 输出：

{
  "root_cause":"门锁装配间隙异常",
  "candidate_causes":["铰链间隙异常"],
  "evidence":["历史案例A","SPC异常"],
  "recommended_actions":["调整门锁扭矩","复测路试"],
  "gate_status":"PASS"
}

* 技术：
    * LLM structured generation / template filling
    * JSON → Markdown / PDF
* 学习/练习：
    * LLM 输出规范化
    * 可执行报告模板

⸻

1️⃣1️⃣ SFT / Preference / RL-style Policy

* 功能描述：优化 LLM 输出和 Planner 动作选择策略，使诊断计划更稳定、可执行、低风险。
* 输入/输出：
    * 输入：历史案例 + 诊断计划生成结果
    * 输出：微调后的模型权重或 scoring 函数
* 技术：
    * LoRA / QLoRA SFT
    * Chosen/rejected 偏好数据
    * Offline replay eval
* 学习/练习：
    * LoRA 微调
    * DPO / reward scoring
    * Offline RL / policy evaluation

⸻

🔹 技术栈映射（可直接开发）

模块	技术	说明
Planner	Python / heuristics / LangGraph	动态生成诊断计划
LangGraph	Python	管理节点流转和状态机
LangChain	Python	封装工具调用、LLM prompt、function calling
RAG	Chroma / FAISS + LLM	历史工单/SOP/FMEA检索
Tool Calling	Python API / SQL / Function	调用 SPC、BOM、FP-Growth、人工输入
DiagnosticState	Pydantic / dict	状态存储
Gate	Python rules / LLM judge	风险门禁
HITL	Streamlit / FastAPI	人工输入回填
Report	LLM / template	生成报告、建议
SFT / Preference	LoRA / QLoRA / offline replay	优化生成计划和报告
Eval	Python scripts / pandas	offline eval, KPI统计

⸻
