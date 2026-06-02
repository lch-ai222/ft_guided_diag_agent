# 诊断 Agent 模拟数据集说明

## 数据来源
基于用户提供的 `corrected_fault_tree_instances.ttl` 中两棵故障树生成：

- FT_001：车机黑屏
- FT_002：车门无法关闭

## 文件清单

| 文件 | 用途 |
|---|---|
| mock_work_orders_FT_001_black_screen.md | 车机黑屏模拟工单，10条 |
| mock_work_orders_FT_002_door_close.md | 车门无法关闭模拟工单，10条 |
| sop_FT_001_black_screen.md | 车机黑屏 SOP，RAG 检索样例 |
| fmea_FT_001_black_screen.md | 车机黑屏 FMEA，RAG 检索样例 |
| repair_manual_FT_001_black_screen.md | 车机黑屏维修手册，RAG 检索样例 |
| sop_FT_002_door_close.md | 车门无法关闭 SOP，RAG 检索样例 |
| fmea_FT_002_door_close.md | 车门无法关闭 FMEA，RAG 检索样例 |
| repair_manual_FT_002_door_close.md | 车门无法关闭维修手册，RAG 检索样例 |

## 建议测试方式

1. 将 6 份 RAG 文档切分入库，metadata 至少包含：
   - `doc_type`: SOP / FMEA / REPAIR_MANUAL
   - `tree_id`: FT_001 / FT_002
   - `phenomenon`: 车机黑屏 / 车门无法关闭
   - `chunk_id`
2. 将工单作为诊断 Agent 输入。
3. 评估：
   - 树选择是否正确
   - 检索到的 SOP/FMEA/维修手册片段是否支持当前分支
   - planner 是否能提出下一步检查项
   - 最终根因是否命中 `expected_leaf_symptom_id`
   - 证据不足样例是否输出 GRAY，而非强行 PASS

## 注意
这些样例是开发测试用模拟数据，不代表真实生产记录。
