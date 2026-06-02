# FT_002 车门无法关闭：带标注诊断评测工单

> 全量模拟数据，不含真实VIN、人员或供应商信息。用于诊断评测平台 v1/v2 的离线批量评测。

## EV-DR-001｜车门无法关闭｜门锁执行器损坏

| 字段 | 标注 |
|---|---|
| case_id | EV-DR-001 |
| eval_group | TREE_COVERED_DOOR_CLOSE |
| vehicle_project | L8 |
| source | 总装EOL |
| severity | MEDIUM |
| failure_type | 车门无法关闭 |
| domain | 车身开闭件 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_002 |
| expected_final_leaf_id | S105 |
| expected_final_root_cause | 门锁执行器损坏 |
| actual_repair_action | 更换左后门锁执行器，开关门10次均锁止 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 外部驱动门锁执行器; 确认二道锁信号 |
| human_review_conclusion | 人工复核确认：执行器失效，非锁扣位置问题 |

### 工单描述
左后门可推至关闭位置但不能进入二道锁，仪表提示未关。

### 已有证据/检查记录
T101无法锁止；T102无完整锁止声；T105外部驱动执行器动作无力。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-DR-002｜车门无法关闭｜铰链变形

| 字段 | 标注 |
|---|---|
| case_id | EV-DR-002 |
| eval_group | TREE_COVERED_DOOR_CLOSE |
| vehicle_project | L9 |
| source | 总装EOL |
| severity | MEDIUM |
| failure_type | 车门无法关闭 |
| domain | 车身开闭件 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_002 |
| expected_final_leaf_id | S107 |
| expected_final_root_cause | 铰链变形 |
| actual_repair_action | 校正铰链并调整门体姿态，关闭力恢复标准范围 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 测量门间隙面差; 检查铰链变形 |
| human_review_conclusion | 人工复核确认：结构件姿态异常 |

### 工单描述
右前门轻关回弹，门缝前窄后宽，关闭轨迹偏下。

### 已有证据/检查记录
T103间隙异常；T107上铰链变形，门体下沉1.8mm。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-DR-003｜车门无法关闭｜密封条干涉

| 字段 | 标注 |
|---|---|
| case_id | EV-DR-003 |
| eval_group | TREE_COVERED_DOOR_CLOSE |
| vehicle_project | L7 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 车门无法关闭 |
| domain | 车身开闭件 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_002 |
| expected_final_leaf_id | S108 |
| expected_final_root_cause | 密封条干涉 |
| actual_repair_action | 重新装配密封条并更换变形段，关闭力合格 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 检查密封条入槽; 排除锁扣偏移 |
| human_review_conclusion | 人工复核确认：密封干涉，不应调整锁扣 |

### 工单描述
洗车后右后门关不上，B柱密封条局部隆起。

### 已有证据/检查记录
T108密封条未完全入槽；T106锁扣位置合格。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-DR-004｜车门无法关闭｜锁扣位置偏移

| 字段 | 标注 |
|---|---|
| case_id | EV-DR-004 |
| eval_group | TREE_COVERED_DOOR_CLOSE |
| vehicle_project | L8 |
| source | 总装EOL |
| severity | MEDIUM |
| failure_type | 车门无法关闭 |
| domain | 车身开闭件 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_002 |
| expected_final_leaf_id | S106 |
| expected_final_root_cause | 锁扣位置偏移 |
| actual_repair_action | 调整锁扣至基准位置，涂色验证啮合居中 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 测量锁扣位置; 做涂色啮合验证 |
| human_review_conclusion | 人工复核确认：锁扣偏移 |

### 工单描述
左前门关闭时锁舌擦碰锁扣外侧，需要二次用力。

### 已有证据/检查记录
T103锁扣相对位置偏外；T106 Y向偏差+2.8mm超差。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-DR-005｜车门无法关闭｜门锁状态开关异常

| 字段 | 标注 |
|---|---|
| case_id | EV-DR-005 |
| eval_group | TREE_COVERED_DOOR_CLOSE |
| vehicle_project | L9 |
| source | 总装EOL |
| severity | MEDIUM |
| failure_type | 车门无法关闭 |
| domain | 车身开闭件 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_002 |
| expected_final_leaf_id | S109 |
| expected_final_root_cause | 门锁状态开关异常 |
| actual_repair_action | 更换门锁总成并校准状态信号，故障提示消失 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 读取门锁状态信号; 区分机械锁止与信号异常 |
| human_review_conclusion | 人工复核确认：电子状态异常，不是关闭力问题 |

### 工单描述
门已物理关闭，但仪表仍提示未关，偶发报警。

### 已有证据/检查记录
T109门锁微动开关信号不稳定；机械锁止正常。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-DR-006｜车门无法关闭｜门框尺寸超差

| 字段 | 标注 |
|---|---|
| case_id | EV-DR-006 |
| eval_group | TREE_COVERED_DOOR_CLOSE |
| vehicle_project | L7 |
| source | 总装EOL |
| severity | MEDIUM |
| failure_type | 车门无法关闭 |
| domain | 车身开闭件 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_002 |
| expected_final_leaf_id | S110 |
| expected_final_root_cause | 门框尺寸超差 |
| actual_repair_action | 返修门框局部尺寸并复测，关闭力合格 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 测量门框基准点; 排除密封条干涉 |
| human_review_conclusion | 人工复核确认：白车身尺寸问题 |

### 工单描述
右后门关闭力偏大，检查发现门框局部变形，密封条正常。

### 已有证据/检查记录
T110门框测量点超差；T108密封条无干涉。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-DR-007｜车门无法关闭｜外把手机构卡滞

| 字段 | 标注 |
|---|---|
| case_id | EV-DR-007 |
| eval_group | TREE_COVERED_DOOR_CLOSE |
| vehicle_project | L8 |
| source | 总装EOL |
| severity | MEDIUM |
| failure_type | 车门无法关闭 |
| domain | 车身开闭件 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_002 |
| expected_final_leaf_id | S111 |
| expected_final_root_cause | 外把手机构卡滞 |
| actual_repair_action | 更换外把手机构并润滑，回位时间合格 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 检查外把手回位; 区分执行器损坏 |
| human_review_conclusion | 人工复核确认：把手机械卡滞 |

### 工单描述
门把手回位迟滞导致门锁保持半开，润滑后短时改善。

### 已有证据/检查记录
T111外把手回位时间超标；门锁执行器正常。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-DR-008｜车门无法关闭｜车门关闭力异常-原因未定位

| 字段 | 标注 |
|---|---|
| case_id | EV-DR-008 |
| eval_group | TREE_COVERED_DOOR_CLOSE |
| vehicle_project | L9 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 车门无法关闭 |
| domain | 车身开闭件 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_002 |
| expected_final_leaf_id | S103 |
| expected_final_root_cause | 车门关闭力异常-原因未定位 |
| actual_repair_action | 未换件，要求补充间隙、锁扣、密封条、铰链检查 |
| repair_validation_result | GRAY |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | GRAY |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 补充门间隙面差; 检查锁扣/密封/铰链 |
| human_review_conclusion | 人工复核：只能输出待补证，不允许落到具体叶子 |

### 工单描述
用户反馈车门关不上，现场只提供视频，未做间隙/锁扣/密封检查。

### 已有证据/检查记录
视频可见右前门轻关回弹；无T103/T106/T108测量数据。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-DR-009｜车门无法关闭｜密封条干涉

| 字段 | 标注 |
|---|---|
| case_id | EV-DR-009 |
| eval_group | TREE_COVERED_DOOR_CLOSE |
| vehicle_project | L7 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 车门无法关闭 |
| domain | 车身开闭件 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_002 |
| expected_final_leaf_id | S108 |
| expected_final_root_cause | 密封条干涉 |
| actual_repair_action | 更换变形密封条，返修后连续关门20次正常 |
| repair_validation_result | PASS |
| is_rework | True |
| is_prior_misdiagnosis | True |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 检查密封条压缩量; 识别前次维修无效 |
| human_review_conclusion | 人工复核：前次误判为锁扣偏移，实际为密封条干涉 |

### 工单描述
售后先调整锁扣后返修，后确认密封条变形段干涉。

### 已有证据/检查记录
T106复测合格；T108密封条C段压缩量异常；调整锁扣后仍复现。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-DR-010｜车门无法关闭｜门锁执行器损坏

| 字段 | 标注 |
|---|---|
| case_id | EV-DR-010 |
| eval_group | TREE_COVERED_DOOR_CLOSE |
| vehicle_project | L8 |
| source | 总装EOL |
| severity | MEDIUM |
| failure_type | 车门无法关闭 |
| domain | 车身开闭件 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_002 |
| expected_final_leaf_id | S105 |
| expected_final_root_cause | 门锁执行器损坏 |
| actual_repair_action | 更换门锁执行器，复测低温/常温关闭均正常 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 采集执行器电流波形; 排除锁扣偏移 |
| human_review_conclusion | 人工复核确认：执行器电流波形支持结论 |

### 工单描述
左前门关闭后偶发弹开，门锁执行器动作声音异常但锁扣位置合格。

### 已有证据/检查记录
T105执行器电流波形异常；T106锁扣偏差在标准内。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。
