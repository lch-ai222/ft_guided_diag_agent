# 模拟质量工单数据集：FT_001 车机黑屏

> 用途：诊断 Agent 开发、树选择、Planner 动态取证、根因候选排序、门禁评测。  
> 数据性质：全量模拟，不含真实 VIN / 人员 / 供应商信息。  
> 关联故障树：`FT_001`  
> 建议字段：`order_id` 可作为 case_id；`expected_leaf_symptom_id` 可作为离线评测标签。

## WO-BS-260520-001｜黑屏/无LOGO

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车机黑屏 |
| vehicle_project | L9 |
| source | 总装EOL |
| station_or_scene | EOL上电检查 |
| severity | HIGH |
| expected_root_cause | 主板短路 |
| expected_leaf_symptom_id | S011 |

### 现象描述
上电后中控屏完全无显示，仪表正常，诊断仪可进入部分车身模块，车机主机无响应。

### 已执行检查/证据
T001: 上电后仍无显示；T002: B+端电压12.4V正常，ACC 12.1V；T010: 主板3V3网络对地阻抗0.7Ω，异常偏低。

### 处理措施与闭环
更换车机主板总成，复测上电显示正常，连续冷启动5次正常。

### 诊断 Agent 测试点
- 是否能选择 `FT_001` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-BS-260520-002｜黑屏但有按键音

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车机黑屏 |
| vehicle_project | L8 |
| source | 总装EOL |
| station_or_scene | 终检 |
| severity | HIGH |
| expected_root_cause | 背光驱动电路失效 |
| expected_leaf_symptom_id | S009 |

### 现象描述
屏幕黑屏，触摸盲操作有提示音，夜间观察无背光。

### 已执行检查/证据
T003: 供电正常但显示不可见；T008: BL_EN有使能，背光驱动输出0V。

### 处理措施与闭环
更换显示模组背光驱动板，亮度恢复，屏幕老化30min无异常。

### 诊断 Agent 测试点
- 是否能选择 `FT_001` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-BS-260520-003｜偶发黑屏/颠簸后恢复

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车机黑屏 |
| vehicle_project | L7 |
| source | 售后返修 |
| station_or_scene | 用户抱怨 |
| severity | MEDIUM |
| expected_root_cause | LVDS屏线接触不良 |
| expected_leaf_symptom_id | S010 |

### 现象描述
用户反馈经过减速带后车机黑屏，重启偶尔恢复。

### 已执行检查/证据
T003: 轻压屏线接口画面闪烁；T009: LVDS端子锁止片未完全扣合，导通摇摆测试不稳定。

### 处理措施与闭环
重新插接并更换屏线卡扣，路试振动复现不再发生。

### 诊断 Agent 测试点
- 是否能选择 `FT_001` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-BS-260520-004｜刷写后黑屏

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车机黑屏 |
| vehicle_project | L8 |
| source | 总装EOL |
| station_or_scene | 软件刷写后检测 |
| severity | HIGH |
| expected_root_cause | 系统镜像损坏 |
| expected_leaf_symptom_id | S012 |

### 现象描述
车辆完成软件刷写后，车机停留黑屏，无启动动画。

### 已执行检查/证据
T004: bootloader日志提示system分区校验失败；T011: 镜像hash不一致。

### 处理措施与闭环
重新刷写完整镜像包并清除启动标志位，冷启动3次通过。

### 诊断 Agent 测试点
- 是否能选择 `FT_001` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-BS-260520-005｜上电无显示/主机掉电

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车机黑屏 |
| vehicle_project | L9 |
| source | PDI |
| station_or_scene | 交付前检查 |
| severity | HIGH |
| expected_root_cause | 电源连接器松脱 |
| expected_leaf_symptom_id | S007 |

### 现象描述
PDI点检时中控屏黑屏，车机风扇不转，USB无供电。

### 已执行检查/证据
T002: 主机B+输入间歇性为0V；T006: 电源连接器二次锁未锁止，端子有轻微退针。

### 处理措施与闭环
修复退针端子并重新插接二次锁，复测供电稳定。

### 诊断 Agent 测试点
- 是否能选择 `FT_001` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-BS-260520-006｜行驶中重启后黑屏

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车机黑屏 |
| vehicle_project | L7 |
| source | 售后 |
| station_or_scene | 用户进店 |
| severity | MEDIUM |
| expected_root_cause | 软件启动异常-需软件分析 |
| expected_leaf_symptom_id | S005 |

### 现象描述
用户称行驶中车机重启，随后黑屏；车辆其他功能可用。

### 已执行检查/证据
T004: 启动日志多次kernel panic；T002: 供电稳定；T011: 镜像完整性通过。

### 处理措施与闭环
导出日志提交软件组，临时重刷版本后恢复；标记为GRAY样例。

### 诊断 Agent 测试点
- 是否能选择 `FT_001` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-BS-260520-007｜黑屏/无触摸反馈

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车机黑屏 |
| vehicle_project | L8 |
| source | 总装EOL |
| station_or_scene | 屏幕点亮检查 |
| severity | HIGH |
| expected_root_cause | 电源管理芯片损坏 |
| expected_leaf_symptom_id | S006 |

### 现象描述
上电后屏幕黑，无触摸音，无主机在线。

### 已执行检查/证据
T002: 12V输入正常；T005: PMIC 1.8V/3.3V输出缺失，EN脚正常。

### 处理措施与闭环
更换PMIC后各路电压恢复，车机正常启动。

### 诊断 Agent 测试点
- 是否能选择 `FT_001` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-BS-260520-008｜换屏后仍黑屏

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车机黑屏 |
| vehicle_project | L9 |
| source | 售后 |
| station_or_scene | 事故维修后复检 |
| severity | HIGH |
| expected_root_cause | 显示屏模组损坏 |
| expected_leaf_symptom_id | S008 |

### 现象描述
维修站更换显示屏后仍黑屏，主机可被诊断。

### 已执行检查/证据
T007: 替换良品显示屏后显示恢复；原屏接入良品车仍黑屏。

### 处理措施与闭环
更换显示屏模组，标定触控参数，复测通过。

### 诊断 Agent 测试点
- 是否能选择 `FT_001` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-BS-260520-009｜黑屏/偶现

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车机黑屏 |
| vehicle_project | L6 |
| source | 总装EOL |
| station_or_scene | 抽检 |
| severity | LOW |
| expected_root_cause | 未复现-候选显示链路异常 |
| expected_leaf_symptom_id | S003 |

### 现象描述
EOL抽检首轮黑屏，二次上电恢复。

### 已执行检查/证据
T001: 首次上电无显示；T002: 供电电压正常；T004: 未读取到明显崩溃日志；T003: 显示链路暂未复现。

### 处理措施与闭环
加入24h老化与振动复测，暂不发布根因，用于agent缺证据判断。

### 诊断 Agent 测试点
- 是否能选择 `FT_001` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-BS-260520-010｜冷车黑屏热车恢复

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车机黑屏 |
| vehicle_project | L8 |
| source | 售后 |
| station_or_scene | 用户抱怨 |
| severity | HIGH |
| expected_root_cause | LVDS屏线接触不良 |
| expected_leaf_symptom_id | S010 |

### 现象描述
低温停放后车机黑屏，车辆升温后恢复显示。

### 已执行检查/证据
T003: 低温箱-10℃复现黑屏；T009: LVDS线束弯折处阻抗波动；T002: 供电正常。

### 处理措施与闭环
更换LVDS屏线并增加固定，低温复测通过。

### 诊断 Agent 测试点
- 是否能选择 `FT_001` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

