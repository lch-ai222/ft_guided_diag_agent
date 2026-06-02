# 分流边界/误放行防护评测工单

> 全量模拟数据，不含真实VIN、人员或供应商信息。用于诊断评测平台 v1/v2 的离线批量评测。

## EV-GR-001｜座舱设置边界样例｜亮度设置过低

| 字段 | 标注 |
|---|---|
| case_id | EV-GR-001 |
| eval_group | ROUTING_GUARDRAIL |
| vehicle_project | L8 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 座舱设置边界样例 |
| domain | 座舱设置 |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 亮度设置过低 |
| actual_repair_action | 调整亮度设置并指导用户，未更换零件 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 确认是否有图像; 检查亮度设置 |
| human_review_conclusion | 人工复核：不可判为FT_001叶子，只能case-only轻故障 |

### 工单描述
用户描述“屏幕暗”，实际是亮度被调到最低；非黑屏故障。

### 已有证据/检查记录
屏幕有图像；亮度设置为最低；调高后恢复。

### 标注说明
边界/反例样本：用于测试误召回、误放行和Gate。


## EV-GR-002｜尾门系统边界样例｜行李物品干涉尾门关闭

| 字段 | 标注 |
|---|---|
| case_id | EV-GR-002 |
| eval_group | ROUTING_GUARDRAIL |
| vehicle_project | L8 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 尾门系统边界样例 |
| domain | 尾门系统 |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 行李物品干涉尾门关闭 |
| actual_repair_action | 移除干涉物并复测，未更换零件 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 确认是侧门还是尾门; 检查是否有异物干涉 |
| human_review_conclusion | 人工复核：非侧门故障树，防止关键词误召回FT_002 |

### 工单描述
用户说“门关不上”，实际是后备箱置物干涉尾门关闭。

### 已有证据/检查记录
尾门锁和撑杆正常；移除行李箱后可关闭。

### 标注说明
边界/反例样本：用于测试误召回、误放行和Gate。


## EV-GR-003｜座舱软件边界样例｜应用缓存异常导致卡顿

| 字段 | 标注 |
|---|---|
| case_id | EV-GR-003 |
| eval_group | ROUTING_GUARDRAIL |
| vehicle_project | L8 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 座舱软件边界样例 |
| domain | 座舱软件 |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 应用缓存异常导致卡顿 |
| actual_repair_action | 清理缓存并升级应用版本，卡顿消失 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 识别否定描述; 采集应用性能日志 |
| human_review_conclusion | 人工复核：包含“无黑屏”否定词，不应进入FT_001 |

### 工单描述
车机无黑屏，只有导航卡顿和语音延迟。

### 已有证据/检查记录
屏幕显示正常；CPU负载高；第三方应用缓存异常。

### 标注说明
边界/反例样本：用于测试误召回、误放行和Gate。


## EV-GR-004｜车身电器边界样例｜门锁状态信号抖动

| 字段 | 标注 |
|---|---|
| case_id | EV-GR-004 |
| eval_group | ROUTING_GUARDRAIL |
| vehicle_project | L8 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 车身电器边界样例 |
| domain | 车身电器 |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | FT_002_OR_CASE_ONLY |
| expected_final_leaf_id | S109_OR_NONE |
| expected_final_root_cause | 门锁状态信号抖动 |
| actual_repair_action | 更换门锁状态开关，提示消失 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 区分机械关闭与状态提示; 读取门锁开关信号 |
| human_review_conclusion | 人工复核：可作为相邻现象，路由需谨慎；若进入FT_002必须落S109而非关闭力叶子 |

### 工单描述
车门关闭正常，但仪表偶发提示右后门未关。

### 已有证据/检查记录
机械锁止正常；门锁状态信号偶发抖动；与FT_002相邻但现象不是无法关闭。

### 标注说明
边界/反例样本：用于测试误召回、误放行和Gate。


## EV-GR-005｜事故维修边界样例｜事故后多系统线束损伤

| 字段 | 标注 |
|---|---|
| case_id | EV-GR-005 |
| eval_group | ROUTING_GUARDRAIL |
| vehicle_project | L8 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 事故维修边界样例 |
| domain | 事故维修 |
| expected_route | REJECT_OR_NEED_MORE_EVIDENCE |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 事故后多系统线束损伤 |
| actual_repair_action | 暂不发布根因，要求事故维修记录和线束分段检查 |
| repair_validation_result | GRAY |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | GRAY |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 要求补充事故维修记录; 分段检查线束 |
| human_review_conclusion | 人工复核：多系统事故车必须FAIL/GRAY，防止误放行 |

### 工单描述
黑屏和车门无法关闭同时出现在事故车，线束多处破损，无法归因。

### 已有证据/检查记录
事故后维修记录缺失；供电、门锁、车身线束均异常；缺少单点证据。

### 标注说明
边界/反例样本：用于测试误召回、误放行和Gate。


## EV-GR-006｜信息不足边界样例｜信息不足无法诊断

| 字段 | 标注 |
|---|---|
| case_id | EV-GR-006 |
| eval_group | ROUTING_GUARDRAIL |
| vehicle_project | L8 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 信息不足边界样例 |
| domain | 信息不足 |
| expected_route | REJECT_OR_NEED_MORE_EVIDENCE |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 信息不足无法诊断 |
| actual_repair_action | 退回补充现象和最小检查集 |
| repair_validation_result | FAIL |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | FAIL |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 补充现象描述; 补充至少一项可验证测试 |
| human_review_conclusion | 人工复核：覆盖判断应失败，不能进入任何诊断流程并放行 |

### 工单描述
EOL记录仅写“功能异常”，无现象、无检查、无维修措施。

### 已有证据/检查记录
无DTC、无照片、无测试记录、无复测结果。

### 标注说明
边界/反例样本：用于测试误召回、误放行和Gate。
