# FT_001 车机黑屏：带标注诊断评测工单

> 全量模拟数据，不含真实VIN、人员或供应商信息。用于诊断评测平台 v1/v2 的离线批量评测。

## EV-BS-001｜车机黑屏｜电源管理芯片损坏

| 字段 | 标注 |
|---|---|
| case_id | EV-BS-001 |
| eval_group | TREE_COVERED_BLACK_SCREEN |
| vehicle_project | L8 |
| source | 总装EOL |
| severity | MEDIUM |
| failure_type | 车机黑屏 |
| domain | 座舱电子 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_001 |
| expected_final_leaf_id | S006 |
| expected_final_root_cause | 电源管理芯片损坏 |
| actual_repair_action | 更换车机主板电源管理子板，复测冷启动/热启动各5次正常 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 补充PMIC输出电压测量; 隔离软件启动分支 |
| human_review_conclusion | 人工复核确认：供电输入正常，PMIC输出缺失，结论成立 |

### 工单描述
上电后中控黑屏，仪表正常；诊断主机偶发离线。

### 已有证据/检查记录
T002 B+ 12.3V正常；T005 PMIC 3V3/1V8无输出，EN脚正常。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-BS-002｜车机黑屏｜背光驱动电路失效

| 字段 | 标注 |
|---|---|
| case_id | EV-BS-002 |
| eval_group | TREE_COVERED_BLACK_SCREEN |
| vehicle_project | L9 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 车机黑屏 |
| domain | 座舱电子 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_001 |
| expected_final_leaf_id | S009 |
| expected_final_root_cause | 背光驱动电路失效 |
| actual_repair_action | 更换显示模组背光驱动板，老化30分钟无闪灭 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 检查BL_EN与背光驱动输出; 复测亮度调节 |
| human_review_conclusion | 人工复核确认：显示链路可用，仅背光失效 |

### 工单描述
屏幕黑但蓝牙音乐和按键音正常，夜间观察无背光。

### 已有证据/检查记录
T003 主机在线；T008 BL_EN=3.3V，背光驱动输出0V。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-BS-003｜车机黑屏｜LVDS屏线接触不良

| 字段 | 标注 |
|---|---|
| case_id | EV-BS-003 |
| eval_group | TREE_COVERED_BLACK_SCREEN |
| vehicle_project | L7 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 车机黑屏 |
| domain | 座舱电子 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_001 |
| expected_final_leaf_id | S010 |
| expected_final_root_cause | LVDS屏线接触不良 |
| actual_repair_action | 重新压接并更换屏线卡扣，路试20km未复现 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 执行屏线摇摆测试; 检查端子锁止状态 |
| human_review_conclusion | 人工复核确认：机械接触类问题，非软件黑屏 |

### 工单描述
颠簸路段中控黑屏，重启后恢复；用户进店可轻微复现。

### 已有证据/检查记录
T009 LVDS端子摇摆测试画面闪烁；卡扣锁止不到位。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-BS-004｜车机黑屏｜系统镜像损坏

| 字段 | 标注 |
|---|---|
| case_id | EV-BS-004 |
| eval_group | TREE_COVERED_BLACK_SCREEN |
| vehicle_project | L8 |
| source | 总装EOL |
| severity | MEDIUM |
| failure_type | 车机黑屏 |
| domain | 座舱电子 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_001 |
| expected_final_leaf_id | S012 |
| expected_final_root_cause | 系统镜像损坏 |
| actual_repair_action | 重刷完整镜像包并清除异常启动标志，连续升级回归通过 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 读取启动日志; 校验镜像hash |
| human_review_conclusion | 人工复核确认：刷写包损坏导致启动失败 |

### 工单描述
OTA后首次上电黑屏，无启动动画，主机可进bootloader。

### 已有证据/检查记录
T004 bootloader日志system校验失败；T011 镜像hash不一致。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-BS-005｜车机黑屏｜电源连接器松脱

| 字段 | 标注 |
|---|---|
| case_id | EV-BS-005 |
| eval_group | TREE_COVERED_BLACK_SCREEN |
| vehicle_project | L9 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 车机黑屏 |
| domain | 座舱电子 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_001 |
| expected_final_leaf_id | S007 |
| expected_final_root_cause | 电源连接器松脱 |
| actual_repair_action | 修复退针端子并重新锁止连接器，震动复测供电稳定 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 测B+/ACC输入; 检查连接器二次锁 |
| human_review_conclusion | 人工复核确认：线束装配缺陷，不应判为主板故障 |

### 工单描述
PDI上电中控黑屏，USB无供电，主机风扇不转。

### 已有证据/检查记录
T002 主机B+间歇为0V；T006 电源连接器二次锁未扣合，端子轻微退针。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-BS-006｜车机黑屏｜软件启动异常-需软件分析

| 字段 | 标注 |
|---|---|
| case_id | EV-BS-006 |
| eval_group | TREE_COVERED_BLACK_SCREEN |
| vehicle_project | L7 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 车机黑屏 |
| domain | 座舱电子 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_001 |
| expected_final_leaf_id | S005 |
| expected_final_root_cause | 软件启动异常-需软件分析 |
| actual_repair_action | 导出日志提交软件组，临时重刷版本恢复；暂未确认最终软件缺陷单 |
| repair_validation_result | GRAY |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | GRAY |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 导出完整日志; 关联软件版本与崩溃栈 |
| human_review_conclusion | 人工复核：只允许GRAY，不允许发布确定根因 |

### 工单描述
行驶中车机重启后黑屏，供电稳定，镜像校验通过；日志存在kernel panic。

### 已有证据/检查记录
T004 kernel panic多次；T002稳定；T011通过；无明确硬件异常。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-BS-007｜车机黑屏｜主板短路

| 字段 | 标注 |
|---|---|
| case_id | EV-BS-007 |
| eval_group | TREE_COVERED_BLACK_SCREEN |
| vehicle_project | L8 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 车机黑屏 |
| domain | 座舱电子 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_001 |
| expected_final_leaf_id | S011 |
| expected_final_root_cause | 主板短路 |
| actual_repair_action | 更换车机主板总成，复测休眠唤醒与冷启动正常 |
| repair_validation_result | PASS |
| is_rework | True |
| is_prior_misdiagnosis | True |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 测量主板阻抗; 避免仅按黑屏现象更换屏幕 |
| human_review_conclusion | 人工复核：存在前次误判，正确结论为主板短路 |

### 工单描述
车机黑屏，维修站先更换屏幕无效，后发现主机主板短路。

### 已有证据/检查记录
T010 3V3网络对地0.6Ω；替换显示模组无改善；主板替换后恢复。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-BS-008｜车机黑屏｜显示IC焊接不良

| 字段 | 标注 |
|---|---|
| case_id | EV-BS-008 |
| eval_group | TREE_COVERED_BLACK_SCREEN |
| vehicle_project | L9 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 车机黑屏 |
| domain | 座舱电子 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_001 |
| expected_final_leaf_id | S008 |
| expected_final_root_cause | 显示IC焊接不良 |
| actual_repair_action | 更换显示模组，供应商确认IC焊点空洞，复测合格 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 确认背光是否正常; 检查显示IC数据链路 |
| human_review_conclusion | 人工复核确认：显示模组内部焊接异常 |

### 工单描述
屏幕黑屏但触摸盲操作有效；供应商返检显示IC虚焊。

### 已有证据/检查记录
T003触摸音正常；T008背光输出正常；示波显示显示IC数据线异常。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-BS-009｜车机黑屏｜软件启动异常-需软件分析

| 字段 | 标注 |
|---|---|
| case_id | EV-BS-009 |
| eval_group | TREE_COVERED_BLACK_SCREEN |
| vehicle_project | L7 |
| source | 总装EOL |
| severity | MEDIUM |
| failure_type | 车机黑屏 |
| domain | 座舱电子 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_001 |
| expected_final_leaf_id | S005 |
| expected_final_root_cause | 软件启动异常-需软件分析 |
| actual_repair_action | 导出日志后暂缓换件，等待软件组定位；车辆未放行 |
| repair_validation_result | GRAY |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | GRAY |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 补充镜像hash; 导出应用崩溃日志 |
| human_review_conclusion | 人工复核：证据不足，只能要求补证 |

### 工单描述
EOL上电黑屏，主机在线但应用无响应，重启后偶发恢复。

### 已有证据/检查记录
T004应用启动超时；T002供电正常；缺少镜像hash和崩溃日志。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。


## EV-BS-010｜车机黑屏｜电源连接器松脱

| 字段 | 标注 |
|---|---|
| case_id | EV-BS-010 |
| eval_group | TREE_COVERED_BLACK_SCREEN |
| vehicle_project | L8 |
| source | 售后 |
| severity | MEDIUM |
| failure_type | 车机黑屏 |
| domain | 座舱电子 |
| expected_route | TREE_DIAGNOSIS |
| expected_tree_id | FT_001 |
| expected_final_leaf_id | S007 |
| expected_final_root_cause | 电源连接器松脱 |
| actual_repair_action | 重压端子并更换连接器壳体，通断与拉脱力复验合格 |
| repair_validation_result | PASS |
| is_rework | False |
| is_prior_misdiagnosis | False |
| expected_gate | PASS |
| expected_case_only_hypothesis_hit | False |
| expected_next_action_hit | 测量负载下压降; 检查端子拉脱力 |
| human_review_conclusion | 人工复核确认：供电链路问题 |

### 工单描述
车机黑屏同时伴随前排USB掉电，拆检发现主机供电线束针脚压接不良。

### 已有证据/检查记录
T002 ACC正常但B+压降至6.8V；T006针脚拉脱力不合格。

### 标注说明
该样例用于评测覆盖判断、树选择、最终叶子、Gate和下一动作命中。
