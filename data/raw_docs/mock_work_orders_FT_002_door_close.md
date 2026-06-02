# 模拟质量工单数据集：FT_002 车门无法关闭

> 用途：诊断 Agent 开发、树选择、Planner 动态取证、根因候选排序、门禁评测。  
> 数据性质：全量模拟，不含真实 VIN / 人员 / 供应商信息。  
> 关联故障树：`FT_002`  
> 建议字段：`order_id` 可作为 case_id；`expected_leaf_symptom_id` 可作为离线评测标签。

## WO-DR-260520-001｜左后门二道锁不上

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车门无法关闭 |
| vehicle_project | L9 |
| source | 总装EOL |
| station_or_scene | 四门关闭力检查 |
| severity | HIGH |
| expected_root_cause | 门锁执行器损坏 |
| expected_leaf_symptom_id | S105 |

### 现象描述
左后门可推至关闭位置，但门锁不能进入二道锁，仪表提示车门未关。

### 已执行检查/证据
T101: 关闭后无法锁止；T102: 门锁无完整锁止声；T105: 外部驱动执行器动作无力。

### 处理措施与闭环
更换左后门锁执行器，复测关闭10次均锁止。

### 诊断 Agent 测试点
- 是否能选择 `FT_002` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-DR-260520-002｜右前门需大力关

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车门无法关闭 |
| vehicle_project | L8 |
| source | 总装EOL |
| station_or_scene | 间隙面差检查 |
| severity | HIGH |
| expected_root_cause | 铰链变形 |
| expected_leaf_symptom_id | S107 |

### 现象描述
右前门关闭阻力大，轻关回弹，门缝前窄后宽。

### 已执行检查/证据
T103: 门间隙异常；T107: 上铰链有轻微变形，关闭轨迹偏下。

### 处理措施与闭环
校正铰链并调整门体姿态，关闭力恢复至标准范围。

### 诊断 Agent 测试点
- 是否能选择 `FT_002` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-DR-260520-003｜雨后车门关不上

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车门无法关闭 |
| vehicle_project | L8 |
| source | 售后 |
| station_or_scene | 用户进店 |
| severity | HIGH |
| expected_root_cause | 密封条干涉 |
| expected_leaf_symptom_id | S108 |

### 现象描述
用户反馈洗车后右后门关不上，检查密封条局部隆起。

### 已执行检查/证据
T108: 密封条B柱段未完全入槽，压缩后明显干涉；T106: 锁扣位置合格。

### 处理措施与闭环
重新装配密封条并更换变形段，复测关闭力合格。

### 诊断 Agent 测试点
- 是否能选择 `FT_002` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-DR-260520-004｜左前门撞锁偏位

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车门无法关闭 |
| vehicle_project | L9 |
| source | 总装EOL |
| station_or_scene | 锁扣啮合检查 |
| severity | HIGH |
| expected_root_cause | 锁扣位置偏移 |
| expected_leaf_symptom_id | S106 |

### 现象描述
左前门关闭时锁舌擦碰锁扣外侧，需二次用力。

### 已执行检查/证据
T103: 锁扣相对位置偏外；T106: 锁扣Y向偏差+2.8mm，超出标准。

### 处理措施与闭环
按基准调整锁扣位置，涂色验证啮合居中。

### 诊断 Agent 测试点
- 是否能选择 `FT_002` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-DR-260520-005｜门已关但仪表仍提示未关

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车门无法关闭 |
| vehicle_project | L7 |
| source | 售后 |
| station_or_scene | 故障灯提示 |
| severity | HIGH |
| expected_root_cause | 闭合传感器失效 |
| expected_leaf_symptom_id | S109 |

### 现象描述
车门机械上已闭合，但仪表持续提示左后门未关，自动落锁失败。

### 已执行检查/证据
T109: 传感器反馈OPEN，实际门锁已锁止；T111: 线束导通正常。

### 处理措施与闭环
更换闭合传感器，读取状态CLOSED，告警消失。

### 诊断 Agent 测试点
- 是否能选择 `FT_002` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-DR-260520-006｜电动关闭中途反弹

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车门无法关闭 |
| vehicle_project | L8 |
| source | PDI |
| station_or_scene | 电动门功能检查 |
| severity | HIGH |
| expected_root_cause | 车门控制模块软件异常 |
| expected_leaf_symptom_id | S110 |

### 现象描述
电动门执行关闭到约70%位置后反弹，手动关闭可锁止。

### 已执行检查/证据
T104: 电动门自检报防夹误触发；T110: 车门控制模块存在标定版本不匹配DTC。

### 处理措施与闭环
升级控制模块软件并执行初始化学习，电动关闭正常。

### 诊断 Agent 测试点
- 是否能选择 `FT_002` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-DR-260520-007｜偶发关门不上锁

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车门无法关闭 |
| vehicle_project | L8 |
| source | 售后 |
| station_or_scene | 用户抱怨 |
| severity | HIGH |
| expected_root_cause | 线束接触不良 |
| expected_leaf_symptom_id | S111 |

### 现象描述
用户反馈偶发关门后不落锁，轻拍门内饰板后恢复。

### 已执行检查/证据
T102: 门锁动作间歇；T111: 门锁连接器端子松旷，摇摆测试反馈丢失。

### 处理措施与闭环
修复门锁线束端子并增加固定，路试振动不复现。

### 诊断 Agent 测试点
- 是否能选择 `FT_002` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-DR-260520-008｜右后门关闭声音异常

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车门无法关闭 |
| vehicle_project | L6 |
| source | 总装EOL |
| station_or_scene | 门线终检 |
| severity | LOW |
| expected_root_cause | 车门姿态异常-待复测 |
| expected_leaf_symptom_id | S103 |

### 现象描述
关闭时有摩擦声，偶尔需二次关闭。

### 已执行检查/证据
T103: 关闭轨迹略偏；T106: 锁扣偏差接近上限但未超；T108: 密封条无明显干涉。

### 处理措施与闭环
建议复测间隙面差和锁扣涂色，暂不确认根因。

### 诊断 Agent 测试点
- 是否能选择 `FT_002` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-DR-260520-009｜维修后门难关

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车门无法关闭 |
| vehicle_project | L8 |
| source | 售后 |
| station_or_scene | 事故维修返修 |
| severity | HIGH |
| expected_root_cause | 锁扣位置偏移 |
| expected_leaf_symptom_id | S106 |

### 现象描述
钣喷维修后左前门难关闭，锁扣有明显擦痕。

### 已执行检查/证据
T106: 锁扣X向偏差-2.1mm；T107: 铰链无变形。

### 处理措施与闭环
调整锁扣并复测密封压力，关闭力合格。

### 诊断 Agent 测试点
- 是否能选择 `FT_002` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

## WO-DR-260520-010｜电动门无法自动吸合

| 字段 | 内容 |
|---|---|
| failure_phenomenon | 车门无法关闭 |
| vehicle_project | L7 |
| source | PDI |
| station_or_scene | OTA后功能检查 |
| severity | MEDIUM |
| expected_root_cause | 车门控制模块软件异常 |
| expected_leaf_symptom_id | S110 |

### 现象描述
OTA后电动门关闭到位但未执行吸合，手动可锁止。

### 已执行检查/证据
T104: 自检提示吸合逻辑条件不满足；T110: BCM/门控版本组合不匹配。

### 处理措施与闭环
回刷兼容版本后执行学习，计划纳入版本组合校验。

### 诊断 Agent 测试点
- 是否能选择 `FT_002` 作为主树。
- 是否能根据证据跳过无关分支，优先补齐关键检查项。
- 是否能在证据不足时输出 `GRAY/NEED_MORE_EVIDENCE`，而不是强行给根因。

