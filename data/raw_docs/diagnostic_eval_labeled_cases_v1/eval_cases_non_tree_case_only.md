# 非故障树覆盖：case-only诊断评测工单

> 全量模拟数据，不含真实VIN、人员或供应商信息。用于诊断评测平台 v1/v2 的离线批量评测。

## EV-NT-001｜充电系统故障｜充电口CC2信号线接触不良

| 字段 | 标注 |
|---|---|
| case_id | EV-NT-001 |
| eval_group | NON_TREE_CASE_ONLY |
| vehicle_project | L8 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 充电系统故障 |
| domain | 充电系统 |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 充电口CC2信号线接触不良 |
| actual_repair_action | 更换充电口低压信号线束，快充/慢充复测通过 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 识别为充电系统; 生成充电握手检查计划 |
| human_review_conclusion | 人工复核：非黑屏、非车门，必须进入case-only诊断 |

### 工单描述
快充无法启动，桩端握手失败，车辆提示充电连接异常。

### 已有证据/检查记录
BMS无绝缘故障；CC2信号不稳定；更换充电口线束后恢复。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-NT-002｜空调热管理故障｜冷凝器接口冷媒泄漏

| 字段 | 标注 |
|---|---|
| case_id | EV-NT-002 |
| eval_group | NON_TREE_CASE_ONLY |
| vehicle_project | L9 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 空调热管理故障 |
| domain | 空调热管理 |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 冷凝器接口冷媒泄漏 |
| actual_repair_action | 更换密封圈并抽真空加注冷媒，制冷恢复 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 检查冷媒压力; 执行检漏 |
| human_review_conclusion | 人工复核：热管理问题，非树覆盖 |

### 工单描述
空调制冷弱，出风温度高，压缩机请求正常。

### 已有证据/检查记录
冷媒压力偏低；荧光检漏发现冷凝器接口微漏。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-NT-003｜制动系统故障｜制动盘轻微锈蚀/摩擦片磨合不足

| 字段 | 标注 |
|---|---|
| case_id | EV-NT-003 |
| eval_group | NON_TREE_CASE_ONLY |
| vehicle_project | MEGA |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 制动系统故障 |
| domain | 制动系统 |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 制动盘轻微锈蚀/摩擦片磨合不足 |
| actual_repair_action | 清洁制动盘并执行磨合流程，异响消失 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 检查制动盘片状态; 执行路试复现 |
| human_review_conclusion | 人工复核：底盘制动噪声，case-only命中即可 |

### 工单描述
低速制动异响，雨后更明显，无制动故障灯。

### 已有证据/检查记录
制动盘表面锈蚀；片盘磨合不足；清洁后改善。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-NT-004｜ADAS故障｜前视摄像头视野污染

| 字段 | 标注 |
|---|---|
| case_id | EV-NT-004 |
| eval_group | NON_TREE_CASE_ONLY |
| vehicle_project | L7 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | ADAS故障 |
| domain | ADAS |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 前视摄像头视野污染 |
| actual_repair_action | 清洁风挡摄像头区域，复测NOA可开启 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 检查摄像头视野; 读取标定状态 |
| human_review_conclusion | 人工复核：ADAS感知受限 |

### 工单描述
NOA无法开启，仪表提示前摄像头受限。

### 已有证据/检查记录
前风挡摄像头区域有油膜；标定状态正常；清洁后恢复。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-NT-005｜底盘悬架故障｜稳定杆连杆球头松旷

| 字段 | 标注 |
|---|---|
| case_id | EV-NT-005 |
| eval_group | NON_TREE_CASE_ONLY |
| vehicle_project | L8 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 底盘悬架故障 |
| domain | 底盘悬架 |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 稳定杆连杆球头松旷 |
| actual_repair_action | 更换左前稳定杆连杆，路试异响消失 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 举升检查球头间隙; 路试定位声源 |
| human_review_conclusion | 人工复核：底盘问题，应拒绝树匹配 |

### 工单描述
行驶中左前悬挂异响，过减速带咯噔声。

### 已有证据/检查记录
稳定杆连杆球头间隙大；举升晃动可复现。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-NT-006｜座椅系统故障｜座椅通风风扇堵转

| 字段 | 标注 |
|---|---|
| case_id | EV-NT-006 |
| eval_group | NON_TREE_CASE_ONLY |
| vehicle_project | L9 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 座椅系统故障 |
| domain | 座椅系统 |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 座椅通风风扇堵转 |
| actual_repair_action | 更换座椅通风风扇，三档风量正常 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 测风扇供电; 检查风道异物 |
| human_review_conclusion | 人工复核：座椅舒适系统 |

### 工单描述
座椅通风无风量，座椅加热正常。

### 已有证据/检查记录
座椅通风风扇不转；供电正常；风扇堵转。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-NT-007｜灯光系统故障｜左前大灯LED驱动板失效

| 字段 | 标注 |
|---|---|
| case_id | EV-NT-007 |
| eval_group | NON_TREE_CASE_ONLY |
| vehicle_project | MEGA |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 灯光系统故障 |
| domain | 灯光系统 |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 左前大灯LED驱动板失效 |
| actual_repair_action | 更换左前大灯驱动板，灯光检测通过 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 测灯具供电; 检查驱动板输出 |
| human_review_conclusion | 人工复核：外饰灯光问题 |

### 工单描述
近光灯左侧不亮，其他灯光正常。

### 已有证据/检查记录
灯具供电正常；左近光LED驱动板无输出。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-NT-008｜车联网故障｜TBOX通信模块异常

| 字段 | 标注 |
|---|---|
| case_id | EV-NT-008 |
| eval_group | NON_TREE_CASE_ONLY |
| vehicle_project | L7 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 车联网故障 |
| domain | 车联网 |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | TBOX通信模块异常 |
| actual_repair_action | 更换TBOX模块并重新入网，远程控车恢复 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 检查TBOX在线状态; 区分车机显示与车联网 |
| human_review_conclusion | 人工复核：注意不是车机黑屏，不应误召回FT_001 |

### 工单描述
车辆远程控车失败，手机App显示车辆离线，但车机屏幕正常。

### 已有证据/检查记录
TBOX在线状态异常；SIM信号弱；重启TBOX后短时恢复。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-NT-009｜车身电器故障｜雨量传感器通信异常

| 字段 | 标注 |
|---|---|
| case_id | EV-NT-009 |
| eval_group | NON_TREE_CASE_ONLY |
| vehicle_project | L8 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 车身电器故障 |
| domain | 车身电器 |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 雨量传感器通信异常 |
| actual_repair_action | 更换雨量传感器，自动雨刮恢复 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 读取雨量传感器通信; 检查传感器供电 |
| human_review_conclusion | 人工复核：车身电器问题 |

### 工单描述
雨刮自动模式不工作，手动档正常。

### 已有证据/检查记录
雨量传感器LIN无响应；供电正常。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-NT-010｜尾门系统故障｜右侧电动撑杆卡滞

| 字段 | 标注 |
|---|---|
| case_id | EV-NT-010 |
| eval_group | NON_TREE_CASE_ONLY |
| vehicle_project | L9 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 尾门系统故障 |
| domain | 尾门系统 |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 右侧电动撑杆卡滞 |
| actual_repair_action | 更换右侧电撑杆，开闭循环20次通过 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 采集撑杆电流; 检查尾门阻力 |
| human_review_conclusion | 人工复核：非侧门关闭树，应进入case-only |

### 工单描述
尾门电动开启到一半停止，手动可关闭。

### 已有证据/检查记录
撑杆电流超限；右侧电撑杆阻力异常。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-NT-011｜电驱系统故障｜逆变器温度传感器异常

| 字段 | 标注 |
|---|---|
| case_id | EV-NT-011 |
| eval_group | NON_TREE_CASE_ONLY |
| vehicle_project | MEGA |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 电驱系统故障 |
| domain | 电驱系统 |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 逆变器温度传感器异常 |
| actual_repair_action | 更换传感器线束，故障码清除后路试正常 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 读取电驱故障码; 检查温度传感器线束 |
| human_review_conclusion | 人工复核：高压电驱，不得进入FT_001/FT_002 |

### 工单描述
动力受限，仪表提示驱动系统故障。

### 已有证据/检查记录
逆变器温度传感器读数跳变；冷却正常。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-NT-012｜充电系统故障｜慢充口温度传感器端子接触不良

| 字段 | 标注 |
|---|---|
| case_id | EV-NT-012 |
| eval_group | NON_TREE_CASE_ONLY |
| vehicle_project | L7 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 充电系统故障 |
| domain | 充电系统 |
| expected_route | CASE_ONLY_DIAGNOSIS |
| expected_tree_id | NONE |
| expected_final_leaf_id | NONE |
| expected_final_root_cause | 慢充口温度传感器端子接触不良 |
| actual_repair_action | 清洁端子并更换传感器线束，低温复测通过 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | True |
| expected_next_action_hit | 读取慢充温度信号; 检查端子水汽 |
| human_review_conclusion | 人工复核：充电系统case-only |

### 工单描述
低温环境慢充中断，重新插枪可恢复。

### 已有证据/检查记录
慢充口温度传感器偶发开路；端子有水汽。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。
