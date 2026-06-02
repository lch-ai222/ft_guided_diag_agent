# 诊断评测标注字段说明与建议指标

> 数据性质：全量模拟。目标是替代早期真实工单脱敏/人工标注成本，用于验证 DiagnosticEngine、路由器、Gate 与 case-only planner。

## 推荐读入文件

- `diagnostic_eval_cases_v1.jsonl`：最适合程序直接读取。
- `diagnostic_eval_cases_v1.csv`：适合人工查看、Excel筛选、Streamlit上传。
- `eval_cases_*.md`：适合做RAG语料、人工审查和调试。

## 核心字段

| 字段 | 用途 |
|---|---|
| case_id | 评测用例ID |
| eval_group | 用例组：树覆盖、非树case-only、边界/误放行防护 |
| failure_type / domain | 分流输入侧的故障现象与业务域 |
| expected_route | 期望路由：TREE_DIAGNOSIS / CASE_ONLY_DIAGNOSIS / REJECT_OR_NEED_MORE_EVIDENCE |
| expected_tree_id | 期望命中的故障树ID；非树样例为NONE |
| expected_final_leaf_id | 期望最终叶子；非树样例为NONE |
| expected_final_root_cause | 人工标注最终根因 |
| actual_repair_action | 真实闭环维修措施模拟值 |
| repair_validation_result | PASS / GRAY / FAIL |
| is_rework | 是否返修 |
| is_prior_misdiagnosis | 是否存在前次误判 |
| expected_gate | 期望Gate输出 |
| expected_case_only_hypothesis_hit | case-only假设是否应命中 |
| expected_next_action_hit | 期望下一动作，支持字符串包含/语义匹配 |
| human_review_conclusion | 人工审核结论 |

## 建议统计指标

1. 覆盖判断准确率：`predicted_route == expected_route`。
2. 树选择准确率：仅在 `expected_route=TREE_DIAGNOSIS` 下统计 `predicted_tree_id == expected_tree_id`。
3. 最终叶子命中率：仅在树覆盖样例下统计 `predicted_leaf_id == expected_final_leaf_id`。
4. Gate一致率：`predicted_gate == expected_gate`。
5. case-only假设命中率：非树样例下，比较生成假设与 `expected_final_root_cause` 的语义相似度或人工关键词命中。
6. 下一动作命中率：生成的next_actions中是否覆盖 `expected_next_action_hit` 的至少一个关键检查。
7. 误放行率：期望为 `GRAY/FAIL/REJECT_OR_NEED_MORE_EVIDENCE` 的样例，被系统输出为 `PASS` 或确定根因。
8. 返修/误判识别率：对 `is_rework=True` 或 `is_prior_misdiagnosis=True` 样例，是否识别出“前次措施无效/需避免重复误判”。

## 注意

- `EV-GR-004` 是一个有意设计的相邻样例：现象不是“无法关闭”，而是“关闭正常但状态提示异常”。你的系统可以将它作为case-only，也可以映射到FT_002中的状态开关异常叶子，但不能落到锁扣、铰链、密封条等机械关闭力叶子。
- `EV-GR-005`、`EV-GR-006` 是误放行防护样例，应触发补证或拒绝发布。
