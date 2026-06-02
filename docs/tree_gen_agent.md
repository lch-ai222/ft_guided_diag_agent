 
# 故障树生成实现复现指南
本文档用于复现当前项目中故障树生成链路的良好效果。目标不是只复现一个能跑通的 demo，而是复现“多文档证据输入 -> 本体诊断图构建 -> 结构校验 -> 确定性 FaultTree 重建 -> 运行时代码导出”的完整质量闭环。
当前版本效果好的关键点是：不要让 LLM 一次性直接输出最终故障树。LLM/Agent 只负责把原文中的诊断知识建模为本体实体和诊断转移关系；最终 FaultTree 由确定性图算法从 `start` 节点重建。这个拆分必须保留，否则很容易退化成旧版本的“行记录投影树”，生成结果会更依赖抽取字段质量，且更容易出现入口混乱、过程节点误建、层级跳跃、孤立节点和树内不可达。

当前项目解释：
- 本文档是成功经验复现参考，不是运行时 prompt。
- 当前项目的真源抽取规范以 `docs/tree_ontology_schema.md` 为准。
- `root` 统一表示 L3 根因，必须不可再拆分；若还能继续拆分，应建模为 L2 inner。
- 抽取阶段 FieldStatus 只使用 `EXTRACTED_EXPLICIT`、`EXTRACTED_INFERRED`、`MISSING`。
- `SUGGESTED_GROUNDED`、`SUGGESTED_LOW_CONF` 只用于后续补全阶段，由 LLM 基于原文语境、RAG 和领域知识给出候选建议，再由用户确认。
## 1. 总体架构
推荐复现为以下模块：
```text
source documents / evidence chunks
        |
        v
source query service
        |
        v
fix_agent
  - read source
  - query current ontology
  - create/update/delete entities
  - create/update/delete SymptomTransition
  - validate after writes
        |
        v
instances.ttl + fault.owl
        |
        v
ontology_validator
  - SHACL structural validation
  - Python graph rules
        |
        v
rebuild_fault_trees
  - delete existing FaultTree
  - one start node -> one FaultTree
  - BFS collect reachable symptoms
        |
        v
ttl2py
  - export each FaultTree to lads.fault_tree runtime Python file
  - export related test data
```
核心实现文件可参考：
- `model/ft_builder_agent/run.py`：编排入口。
- `model/fix_agent/config.py`：Agent system prompt 和建模边界。
- `model/fix_agent/work_cot_skill.txt`：生成/修改本体前的思考工作流。
- `model/fix_agent/tools/query.py`：原文和本体查询工具。
- `model/fix_agent/tools/mutate.py`：本体写入工具。
- `model/utils/ontology_validator.py`：本体结构校验。
- `model/ft_builder_agent/rebuild_fault_trees.py`：确定性重建 FaultTree。
- `model/utils/ttl2py.py`：TTL 转运行时代码。
- `ontology/ontology_schema.yaml`：语义 schema 和约束真源。
## 2. 数据模型
本体有四类实体和一类关系。
### 2.1 FailureSymptom
`FailureSymptom` 是故障树中的异常现象节点，统一承载 L1、L2、L3。
必需字段：
```yaml
symptom_id: string
symptom_name: string
symptom_name_status: FieldStatus
symptom_level: start | inner | root
symptom_desc_status: FieldStatus
```
可选字段：
```yaml
symptom_desc: string?
symptom_chunk_ids: string[]?
measure_ids: ref(OntologyMeasure)[]
```
建模规则：
- `start`：L1，入口异常。必须是客户、产线、检测系统可以直接观察或报告的最大公约数现象。
- `inner`：L2，中间异常状态。必须是经过检查、观察、分析后得到的更具体异常状态，不能是检查动作本身。
- `root`：L3，终止根因。应是资料中已定位到的、可直接挂接措施或验证闭环的最深层可处置异常。
- 不要把“检查、验证、分析、复现、处理、整改、培训”这类动作建成 `FailureSymptom`。这些应进入 `OntologyTest`、`OntologyMeasure` 或 `SymptomTransition.condition/transition_desc`。
- 同一失效域的多个报告应复用同一个 `start`，不要按触发场景、显示位置、项目名把 start 拆成多个语义重叠节点。
FailureSymptom 的 start/root 节点不得使用 MISSING 作为 symptom_name_status。

root 节点必须是已经明确或可由证据支持定位到的终止原因，因此：
- symptom_name_status 不允许为 MISSING；
- symptom_desc_status 不建议为 MISSING；
- 若 root 描述来自原文，使用 EXTRACTED_EXPLICIT；
- 若 root 名称明确但描述需要弱推断，使用 EXTRACTED_INFERRED；
- 不允许创建“未知原因”“待确认原因”这类 root 作为最终根因节点，除非显式标记为占位并且不作为验收通过的 root。

### 2.2 OntologyTest
`OntologyTest` 表示从上级现象定位到下级现象的检查动作、测试方法、验证手段。
必需字段：
```yaml
test_id: string
test_name_status: FieldStatus
test_unit_status: FieldStatus
test_hilim_status: FieldStatus
test_lolim_status: FieldStatus
test_rule_status: FieldStatus
test_target_status: FieldStatus
test_desc_status: FieldStatus
```
可选字段：
```yaml
test_name: string?
test_unit: string?
test_hilim: number?
test_lolim: number?
test_rule: string?
test_target: string?
test_desc: string?
test_chunk_ids: string[]?
```
建模规则：
- 凡是“测量、读取、观察、拆检、比对、复测、验证、确认”等动作，优先建成 `OntologyTest`。
- 一条 `SymptomTransition` 必须至少关联一个 `OntologyTest`。
- 如果原文没有明确检查项，但结构上确实存在诊断转移，应创建一个占位 test，并把缺失字段状态设为 `MISSING`。这样可以保留诊断链完整性，并让后续补全知道缺口在哪里。
### 2.3 OntologyMeasure
`OntologyMeasure` 表示处置措施、维修动作、工艺改善。
必需字段：
```yaml
measure_id: string
measure_name: string
measure_name_status: FieldStatus
measure_desc_status: FieldStatus
```
可选字段：
```yaml
measure_desc: string?
measure_chunk_ids: string[]?
```
建模规则：
- 凡是“更换、维修、调整、返修、工装修复、参数修正、工艺改善”等动作，优先建成`OntologyMeasure`。
- 措施不是检查项，也不是异常现象。
- 有措施时必须挂到至少一个 `FailureSymptom`，通常挂在 `root` 节点上。
### 2.4 SymptomTransition
`SymptomTransition` 表示从一个异常现象，经检查项和条件，定位到另一个异常现象。
关系字段：
```yaml
source: ref(FailureSymptom)
target: ref(FailureSymptom)
test_id: ref(OntologyTest)[]
condition: string?
condition_status: FieldStatus
transition_desc: string?
transition_desc_status: FieldStatus
transition_chunk_ids: string[]?
```
建模规则：
- `source` 是上级/父现象，`target` 是下级/子现象。
- 方向必须是 `start -> inner -> root`，允许 `start -> root`，不允许反向。
- 关系表达诊断分解，不表达原文叙事顺序。
- 同一对 `source,target` 只能有一条 `SymptomTransition`。
- 如果原文同时描述“通过 XX 检查发现 YY 异常”，则 XX 进入 `OntologyTest`，YY 进入 `FailureSymptom`，二者通过 `SymptomTransition` 连接。
SymptomTransition.test_id 是结构必需字段，不允许缺失、空数组或 MISSING 状态。

允许的缺口表达方式是：
- transition 必须引用至少一个 OntologyTest；
- OntologyTest 实体必须真实存在；
- 如果原文没有明确测试名称，可以创建占位 OntologyTest；
- 占位 OntologyTest 的 test_name 可以为空；
- 但 test_name_status 必须为 MISSING；
- transition.test_id 本身不得为空，也不得用 MISSING 表达。
### 2.5 FaultTree
`FaultTree` 是全局诊断图上的诱导子图，只保存节点集合。
```yaml
tree_id: string
tree_name: string
tree_desc: string?
symptom_ids: ref(FailureSymptom)[]
applicable_scope: string?
version: string?
```
复现时不要让 Agent 主动创建或维护最终 `FaultTree`。最终树由 `rebuild_fault_trees` 统一重建：
- 删除所有已有 `FaultTree`。
- 每个 `start` 节点对应一棵 `FaultTree`。
- 从该 `start` 沿 `SymptomTransition.source -> target` BFS。
- 所有可达 `FailureSymptom` 作为该树的 `symptom_ids`。
## 3. FieldStatus 规则
所有可空字段都要配套 `_status` 字段。
```yaml
MISSING: 字段无值，待补问
EXTRACTED_EXPLICIT: 原文明确出现
EXTRACTED_INFERRED: 原文支持但需要弱推断
SUGGESTED_LOW_CONF: 补全阶段的低置信建议
SUGGESTED_GROUNDED: 补全阶段基于当前原文语境、RAG 和领域知识的建议
CONFIRMED: 用户确认
VERIFIED: 经验证动作确认
```
强制规则：
- 字段为空或 `null` 时，对应 status 必须为 `MISSING`。
- 不要用空字符串冒充已抽取。
- 抽取阶段只使用 `EXTRACTED_EXPLICIT`、`EXTRACTED_INFERRED`、`MISSING`。
- 不确定但需要保留结构时，允许创建占位实体或关系，但状态必须显式标记为 `MISSING` 或 `EXTRACTED_INFERRED`。
- `SUGGESTED_LOW_CONF` 和 `SUGGESTED_GROUNDED` 不表示原文已抽取内容，只能用于后续 HITL 补全建议。
- `EXTRACTED_INFERRED` 和 `MISSING` 不表示节点或关系应被删除。它们表示需要后续补全、确认或验证；repair 阶段不能因为低置信、GRAY、待确认、缺少最终验证就删除这些草案内容。
- 新建数据应尽量填写 `*_chunk_ids`，推荐格式为 `document_id:page_no`，避免跨文档页码歧义。
## 4. Agent 运行机制
### 4.1 System Prompt 必须包含的约束
Agent 的职责不是直接输出树，而是维护本体实例数据。system prompt 至少要包含：
```text
你是故障树本体 Agent，负责查询、修改和从文档中构建 OWL 本体实例数据。
能力：
- 查询、创建、更新、删除 FailureSymptom / OntologyTest / OntologyMeasure / SymptomTransition
- 语义搜索相似实体和关系
- 运行 validate_ontology
- 查询原文来源
重要：
- 不主动构建或修改最终 FaultTree
- 修改前先查询
- 写操作前必须制定计划
- 不编造 ID
- 缺失值使用 MISSING
- 任务完成前运行校验并修复
```
还必须明确建模边界：
```text
FailureSymptom 表达“异常状态是什么”
OntologyTest 表达“做了什么检查/如何判断”
OntologyMeasure 表达“如何处置”
SymptomTransition 表达“如何从上级异常定位到下级异常”
```
### 4.2 增删改工作流
在任何写操作之前，Agent 必须执行一个固定思考流程。推荐实现为 `show_work_skill` 工具返回以下工作流，或直接作为系统消息注入。
必须执行的分析步骤：
1. 从原文提取所有候选失效现象。
2. 对每个候选判断它是异常状态、检查动作、处置措施还是叙事过程。
3. 给真正的 `FailureSymptom` 分级：`start`、`inner`、`root`。
4. 整理诊断链草稿，模式必须是“异常状态 -> 更具体异常状态”，不是文档时间顺序。
5. 提取检查项和措施。
6. 自查颗粒度：每个节点是否对应一次可验证诊断判定。
7. 自查 start：同一失效域是否被拆成多个语义重叠 start。
8. 自查 root：是否是资料中实际定位到的最深层可处置根因。
9. 自查关系：每条边是否有关联检查项；没有则创建 MISSING 占位 test。
10. 执行写入。
11. 运行 `validate_ontology`。
12. 根据校验结果继续修复，直到没有 error 级结构问题。
这个流程是当前生成效果的重要来源。不能省略“先思考再写入”和“写后校验再修复”。
修复阶段只应处理结构问题，例如缺 test、方向非法、引用不存在、root 有出边、start 有入边、孤立节点等。低置信但可追溯的 `EXTRACTED_INFERRED` / `MISSING` 内容应保留为草案并进入 HITL，而不是被删除。
### 4.3 查询工具
复现时至少需要以下只读工具。
```python
query_entities(entity_type: str, filters: dict = None) -> ToolResult
query_relations(relation_type: str, filters: dict = None) -> ToolResult
query_source(mode: "list" | "doc" | "chunk", ids: list = None) -> ToolResult
list_suppliers(supplier_tag: str = None) -> ToolResult
```
用途：
- `query_source(list/doc/chunk)`：读取原始文档或页面片段，支撑 evidence grounding。
- `query_entities`：写入前确认已有实体，避免重复节点。
- `query_relations`：写入前确认已有边，避免重复 `source,target`。
- `list_suppliers`：按供应商或文档集合批量加载资料。
### 4.4 写入工具
复现时至少需要以下写入工具。
```python
create_entity(entity_type: str, properties: dict) -> ToolResult
update_entity(entity_type: str, entity_id: str, properties: dict) -> ToolResult
delete_entity(entity_type: str, entity_id: str) -> ToolResult
create_relation(relation_type: "SymptomTransition", properties: dict) ->ToolResult
update_relation(relation_type: "SymptomTransition", source_id: str, target_id: str, properties: dict) -> ToolResult
delete_relation(relation_type: "SymptomTransition", source_id: str, target_id: str) -> ToolResult
```
写入规则：
- `create_entity` 不允许创建 `FaultTree`。Agent 只创建三类基础实体。
- `create_relation` 必须要求 `source`、`target`、非空 `test_id`。
- `create_relation` 必须拒绝重复 `source,target`。
- 删除 `FailureSymptom` 时，必须级联删除关联 `SymptomTransition`。
- 更新关系的 `test_id` 时必须是非空数组。
## 5. 本体存储
推荐使用 RDF/TTL 保存实例数据：
```text
product/ontology/outputs/ft_builder_agent/fault.owl
product/ontology/outputs/ft_builder_agent/empty.ttl
```
命名空间：
```text
http://lianshan.ai/ontology/qlt_fta#
```
IRI 格式：
```text
FailureSymptom_S001
OntologyTest_T001
OntologyMeasure_M001
FaultTree_FT_001
SymptomTransition_<uuid>
```
ID 分配：
- `FailureSymptom`: `S001`, `S002`, ...
- `OntologyTest`: `T001`, `T002`, ...
- `OntologyMeasure`: `M001`, `M002`, ...
- `FaultTree`: `FT_001`, `FT_002`, ...
新建前必须查询已有最大 ID，从下一个开始。
## 6. 校验器复现
校验器要分两层：SHACL 校验和 Python 图规则校验。
### 6.1 SHACL 校验
SHACL 至少覆盖：
- 所有 `FailureSymptom` 参与至少一条 `SymptomTransition`。
- 所有 `OntologyMeasure` 被至少一个 `FailureSymptom` 引用。
- 层级方向：`start -> inner/root`，`inner -> inner/root`。
- `start` 没有入边。
- `root` 没有出边。
- 空字段与 `MISSING` 状态一致。
- 同一 `source,target` 只允许一条 `SymptomTransition`。
- 节点名称长度建议。
### 6.2 Python 图规则
必须实现以下图规则：
```python
R1  global transition graph is DAG
R5  no convergence under the same start
R13 max consecutive inner depth <= 4
R14 each FaultTree induced subgraph has exactly one entry
R15 non-entry inner/root nodes in a FaultTree must have parent in subgraph
R16 every inner has a forward path to at least one root
R17 every inner/root can trace backward to at least one start
```
推荐伪代码：
```python
def validate(ttl_path):
    graph = load_ttl(ttl_path)
    data = extract_symptoms_transitions_fault_trees(graph)
    issues = []
    issues += run_shacl(graph)
    issues += check_dag(data)
    issues += check_no_convergence_under_same_start(data)
    issues += check_inner_depth(data)
    issues += check_tree_entry_unique(data)
    issues += check_tree_non_entry_has_parent(data)
    issues += check_inner_reach_root(data)
    issues += check_reachable_from_start(data)
    return issues
```
校验器必须返回可操作的错误信息，例如：
```text
S012(inner) 是链路末端但未连接到任何 root。
修复建议：
1. 若它实际是根因，改为 root；
2. 若资料有下游异常，补建 root 和转移；
3. 若无法确定，新建 root 占位实体并标记 MISSING。
```
这种“错误 + 修复建议”的返回对 Agent 自动修复非常关键。
## 7. FaultTree 确定性重建
这是当前版本效果优于旧版本的关键。复现时必须实现。
### 7.1 设计原则
Agent 创建的是全局诊断图，不是最终树。最终树按 `start` 节点自动生成：
- 一个 `start` 对应一棵树。
- 树包含从该 `start` 可达的所有 `FailureSymptom`。
- 边不单独存储在树里，树是全局图上的诱导子图。
- 如果 `start` 语义重叠，必须先合并 start，再重建树。
### 7.2 算法
```python
def rebuild(ttl_path, owl_path=None):
    g = load_graph(ttl_path, owl_path)
    # 1. 删除所有已有 FaultTree
    for ft_uri in g.subjects(RDF.type, QTL.FaultTree):
        remove_all_triples_about(ft_uri)
        remove_all_triples_pointing_to(ft_uri)
    # 2. 收集 start 节点
    starts = []
    for symptom in g.subjects(RDF.type, QTL.FailureSymptom):
        if value(symptom, QTL.symptomLevel) == "start":
            starts.append(symptom)
    starts.sort(key=symptom_id)
    # 3. 构建 source -> target 邻接表
    adjacency = {}
    for transition in g.subjects(RDF.type, QTL.SymptomTransition):
        src = value(transition, QTL.transitionSource)
        tgt = value(transition, QTL.transitionTarget)
        if src and tgt:
            adjacency.setdefault(src, []).append(tgt)
    # 4. 每个 start BFS 收集可达节点
    for i, start in enumerate(starts, 1):
        reachable = bfs(start, adjacency)
        ft = URI(NS + f"FaultTree_FT_{i:03d}")
        add(ft, RDF.type, QTL.FaultTree)
        add(ft, QTL.treeId, f"FT_{i:03d}")
        add(ft, QTL.treeName, f"{symptom_name(start)}故障树")
        for symptom in reachable:
            add(ft, QTL.hasSymptom, symptom)
    save_instances(g, ttl_path, owl_path)
```
### 7.3 为什么这个步骤不能省略
如果让 LLM 直接创建 `FaultTree.symptom_ids`，常见退化包括：
- 一棵树包含多个入口。
- 子图里有不可达 inner/root。
- LLM 忘记把新建节点加入树。
- 多个语义重叠 start 导致树被拆碎。
- 修改 transition 后 FaultTree 没有同步更新。
确定性重建把这些问题收敛为一个简单规则：只要全局诊断图正确，FaultTree 就稳定正确。
## 8. TTL 转运行时代码
导出目标是每棵 `FaultTree` 生成一个 `.py` 文件，兼容 `lads.fault_tree` 风格。
### 8.1 收集树数据
```python
def collect_tree_data(g, ft_uri):
    symptom_uris = set(g.objects(ft_uri, QTL.hasSymptom))
    symptom_info = {
        uri: {
            "id": symptom_id,
            "name": symptom_name,
            "level": symptom_level,
            "measure_ids": measure_ids,
        }
        for uri in symptom_uris
    }
    transitions = []
    for tr in g.subjects(RDF.type, QTL.SymptomTransition):
        src = value(tr, QTL.transitionSource)
        tgt = value(tr, QTL.transitionTarget)
        if src in symptom_uris and tgt in symptom_uris:
            transitions.append({
                "source": src,
                "target": tgt,
                "test_names": test_names_of(tr),
            })
    adjacency = build_adjacency(transitions)
    start_uris = [u for u in symptom_uris if symptom_info[u]["level"] == "start"]
    return symptom_info, adjacency, start_uris
```
### 8.2 代码生成规则
- 从 `start` BFS 排序节点，保证生成文件稳定。
- `start` 节点生成 `SymptomName(name)`。
- `inner/root` 节点生成 `LogicNode(name)`。
- 有措施的节点写入 `measure_ids`。
- 每条边生成 transition 条目：
```python
{
    "next_node_name": target_name,
    "test_names": [{"test_name": test_name, "role_name": ""}],
    "weight": 1.0 / child_count,
}
```
- 最后设置 `next_nodes`。
- 调用 `ft.save()`。
生成示例：
```python
from faulttrees.heiping.def_ontology import FaultTreeOntology
ft = FaultTreeOntology.create()
SymptomName = ft.SymptomName
LogicNode = ft.LogicNode
黑屏 = SymptomName("黑屏")
黑屏.is_multi_next = [False]
黑屏.transition = [
    {
        "next_node_name": "供电异常",
        "test_names": [{"test_name": "测量电源电压", "role_name": ""}],
        "weight": 0.5,
    },
]
供电异常 = LogicNode("供电异常")
供电异常.transition = [
    {
        "next_node_name": "电源芯片损坏",
        "test_names": [{"test_name": "更换电源芯片验证", "role_name": ""}],
        "weight": 1.0,
    },
]
电源芯片损坏 = LogicNode("电源芯片损坏")
电源芯片损坏.measure_ids = ["M001"]
黑屏.next_nodes = [供电异常]
供电异常.next_nodes = [电源芯片损坏]
ft.save()
```
## 9. 编排流程
推荐主流程：
```python
def run_ft_builder(args):
    ttl_path = "product/ontology/outputs/ft_builder_agent/empty.ttl"
    owl_path = "product/ontology/outputs/ft_builder_agent/fault.owl"
    py_output_dir = "product/ontology/outputs/ft_builder_agent/py"
    clear_file(ttl_path)
    run_fix_agent(
        ttl=ttl_path,
        owl=owl_path,
        instruction=(
            "严格按照 skill 流程：加载目标文档，分析诊断链路，"
            "在空 ontology 中构建失效现象、检查项、诊断转移关系、措施，"
            "并使用校验/子 agent 验证语义逻辑。"
        ),
        temperature=0.05,
    )
    run_fix_agent(
        ttl=ttl_path,
        owl=owl_path,
        instruction=(
            "在已有 ontology 中补充遗漏实体和关系，重点检查所有 start 层级，"
            "严禁 start 语义重叠；如有重叠必须合并并重构转移关系。"
        ),
        temperature=0.05,
    )
    issues = validate_ontology(ttl_path)
    if has_error(issues):
        run_fix_agent(
            ttl=ttl_path,
            owl=owl_path,
            instruction="根据 validate_ontology 结果修复结构问题，直到通过。"
        )
    rebuild_fault_trees(ttl_path, owl_path)
    issues = validate_ontology(ttl_path)
    if has_error(issues):
        raise RuntimeError(issues)
    convert_ttl_to_py(ttl_path, owl_path, py_output_dir)
```
当前项目中第一轮构建和第二轮补充是刻意设计的。第一轮解决从零构建，第二轮专门处理遗漏、start 语义重叠、诊断链补全。不要只跑一轮。
## 10. 质量机制
### 10.1 start 合并
start 节点质量决定最终树数量和入口质量。必须执行：
- 查询所有 `symptom_level=start`。
- 对名称、描述、来源 chunk 做语义比较。
- 同一失效域只保留最大公约数入口异常。
- 被合并的 start 降级为 `inner` 或删除。
- 重接其下游 transition。
- 重新运行校验和 FaultTree 重建。
错误示例：
```text
S001: 中控黑屏
S002: 屏幕不亮
S003: 上电黑屏
```
更好建模：
```text
S001 start: 黑屏/无显示
S002 inner: 上电后黑屏
S003 inner: 中控屏无显示
```
### 10.2 检查动作和异常结果拆分
原文：
```text
通过读取日志发现 IIC 通信超时。
```
错误建模：
```text
FailureSymptom: 读取日志发现 IIC 通信超时
```
正确建模：
```text
OntologyTest: 读取日志
FailureSymptom: IIC 通信超时
SymptomTransition: 上级异常 -> IIC 通信超时, test_id=[读取日志]
```
### 10.3 root 终止性
root 必须是最深层可处置异常。若原文继续定位到更深原因，不能提前截断。
错误：
```text
root: 通信异常
```
如果原文进一步说明“连接器虚焊导致通信异常”，更好：
```text
inner: 通信异常
root: 连接器虚焊
```
### 10.4 缺口显式化
资料不足时不要编造。正确方式是保留结构缺口：
```text
OntologyTest T099:
  test_name: null
  test_name_status: MISSING
SymptomTransition:
  source: S001
  target: S010
  test_id: [T099]
  condition: null
  condition_status: MISSING
```
这样后续推荐/补问模块才能精准补齐。
## 11. 与旧版实现的关键差异
旧版 `hitl_v2` 的生成链路已经有 evidence grounding、row/graph 抽取和 gate，但最终树主要由 `generator_v2.py` 把记录投影为：
```text
TOP_EVENT -> optional L2 -> optional L3 -> BASIC_EVENT -> TEST
```
这类投影方案的优点是快、稳定、容易展示；短板是：
- L2/L3 质量强依赖抽取字段。
- 中间层缺失时只能跳层或 fallback。
- gate 多数发生在生成后，不能强约束写入过程。
- 节点类型更贴近展示树，不如本体模型适合多报告融合和复用。
当前版本必须保留以下差异，才能达到更好的生成效果：
- LLM 不直接输出最终树。
- Agent 写入的是本体实体和 `SymptomTransition`。
- 每次写入后可运行结构校验，并根据错误修复。
- `FaultTree` 由 start BFS 确定性重建。
- `OntologyTest` 与 `FailureSymptom` 严格分离。
- 使用 `FieldStatus` 和 chunk 引用保留缺口与证据来源。
## 12. 最小可复现工程结构
```text
repro_project/
  ontology/
    ontology_schema.yaml
    fault.owl
  model/
    fix_agent/
      config.py
      work_cot_skill.txt
      run.py
      tools/
        query.py
        mutate.py
        validate.py
        search.py
        sparql.py
    ft_builder_agent/
      run.py
      rebuild_fault_trees.py
    utils/
      ontology_validator.py
      ontology_shacl.ttl
      ttl2py.py
      chunk_refs.py
  product/
    ontology/
      outputs/
        ft_builder_agent/
          empty.ttl
          fault.owl
          py/
```
最小命令：
```bash
python -m model.ft_builder_agent.run \
  --db-host <host> \
  --db-port <port> \
  --db-name <db> \
  --db-user <user> \
  --db-password <password> \
  --llm-model <model> \
  --llm-base-url <base_url> \
  --llm-api-key <api_key>
```
推荐默认：
```text
temperature = 0.05
max_tokens >= 32768
```
低温度有助于 ID、字段状态、结构修复策略稳定。不要为了“创造性”提高温度。
## 13. 验收标准
复现实现至少要满足以下验收项：
1. `validate_ontology` 无 error 级结构违规。
2. 全局 `SymptomTransition` 图无环。
3. 每个 `start` 自动生成一棵且仅一棵 `FaultTree`。
4. 每棵 FaultTree 诱导子图入口唯一。
5. 每个 `inner` 有路径到至少一个 `root`。
6. 每个 `inner/root` 可反向回溯到至少一个 `start`。
7. 每条 `SymptomTransition` 至少关联一个 `OntologyTest`。
8. 空字段的 status 为 `MISSING`。
9. 同一失效域没有语义重叠 start。
10. 生成的 `.py` 文件中 start 使用 `SymptomName`，inner/root 使用 `LogicNode`，边上带检查项。
更高质量验收：
- 抽样检查 5 条从 start 到 root 的路径，每个中间节点都应是异常状态，而不是动作。
- 抽样检查 5 条边，每条边的 test 都应回答“如何确认 target”。
- 抽样检查 root，确认其可直接关联措施或验证闭环。
- 多份报告中的同义入口异常应合并到同一 start。
## 14. 常见退化点
复现时最容易失败的地方如下：
- 让 LLM 直接输出整棵 FaultTree。
- 只做 JSON schema 校验，不做图规则校验。
- 把 `FaultTree.symptom_ids` 当作 Agent 写入目标。
- 没有强制每条 transition 关联 test。
- 没有显式 `MISSING` 状态，导致缺口不可追踪。
- 把“分析步骤/检查动作”建成 `FailureSymptom`。
- 把同一失效域拆成多个 start。
- 只跑一轮 Agent，不做补充和 start 重叠检查。
- 只在最后 gate，不在写入过程中持续 validate/fix。
保留这些质量机制，复现出的代码才会接近当前版本的生成效果。
