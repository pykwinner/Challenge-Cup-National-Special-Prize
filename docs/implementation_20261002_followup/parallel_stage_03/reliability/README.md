# 三城批处理失败隔离与真实数据验证

2026-10-02新增 `scripts/run_three_city_climate_dispatch_resilient.py`，解决城市准备和基线在异常处理之外、单城失败阻断整个批次的问题。旧运行脚本、旧精化脚本、核心数值模块和rc4冻结证据原字节保留。这是研究候选的实现补强，本目录没有执行上传、commit或tag，也没有升级为正式工程验收。

最终专项为 **33 passed，0 failed，0 error，0 skipped，0 deselected**。新入口真实新跑武汉完整 **2880h**：1个新反馈基线、3个新优化案例、5个求解阶段；4行全部接受，0失败，整批25.59s。独立导出审计90项通过，3个源流量篡改和4个功率篡改均被拒绝。各求解阶段均为60s、请求相对MIP gap为0，没有启用新加速或代表时段压缩。

## 故障控制

每城分别记录准备、基线、成本端点、碳端点和中点任务，在执行前后原子写入 `checkpoint.json`。JSON使用同目录临时文件、flush/fsync及replace，Windows深目录使用扩展路径。首次启动以独占目录 `run_claim` 取得运行权；已开始、已失败或已中断的目录拒绝重跑覆盖，并发竞争者不进入原运行的失败发布流程。

| 功能性故障注入 | 结果 |
| --- | --- |
| 首城读取准备失败 | 保留 `climate_read/OSError/reason`；该城4案例跳过；其余两城完成8行 |
| 首城基线rollout或独立审核失败 | 保留阶段、真实异常类型及原因；该城3优化跳过；其余两城完成8行 |
| 中间城市成本端点第二阶段失败 | 保留两阶段solver summary；继续该城碳端点和下一城市；中点因缺少接受端点跳过；总10行 |
| 主目标锚点、返回KPI或声明allowance越过known-feasible门限 | 端点拒绝，中点跳过；失败审核JSON保留 |
| 输入、代码、运行环境或协议身份被改 | 整批终止；剩余任务明确跳过；已完成行留作诊断，`comparison_valid=false` |
| 运行中同时替换合法协议和自洽lock | 与最初protocol及lock SHA不符，仍拒绝；最后导出阶段也复核身份 |
| 原子checkpoint replace失败 | 上一个完整可解析检查点保留，停止继续求解，不能发布成功manifest |
| KeyboardInterrupt或SystemExit | 原异常穿透；保留running检查点；不得吞成普通城市失败，不允许覆盖重跑 |
| 并发调用、结果篡改或manifest计数篡改 | 只有一个运行者；逐文件库存及checkpoint/protocol/manifest交叉核验拒绝不一致 |

故障注入使用已准入的三城2880h真实驱动、实际连续RC反馈和独立审核；优化部分为带 `fault_injection_feedback_test_double` 标签的测试替身。它们验证批处理控制，**不计为新的三城优化求解**。最后一轮保留21份失败或中断控制快照，路径和SHA见 [validation_manifest.json](validation_manifest.json)。

## 新的真实运行

武汉此次基线重新rollout，没有复用旧行。端点继续调用旧 `run_one`，每端点独立新建model并保留两阶段状态、gap、bound、quality。每端点还检查主目标incumbent不大于新基线对应KPI加1e-6；最终返回KPI不大于新基线加声明的primary allowance及1e-6。allowance必须等于 `1e-7 + 1e-8*abs(primary)`。中点仅由两个已通过审核的端点实际碳账本推导。

| 新案例 | 电费 元 | 用电 kWh | 碳账本 kg |
| --- | ---: | ---: | ---: |
| baseline_delta0 | 2305.821968 | 3043.010077 | 1230.593275 |
| cost_endpoint | 2265.428738 | 3176.920061 | 1284.746473 |
| carbon_endpoint | 2305.821944 | 3043.010107 | 1230.593287 |
| midpoint_cap | 2269.693275 | 3109.965116 | 1257.669893 |

碳端点返回碳量略高于基线的约0.0000124kg处于明确的第二阶段primary allowance内；其主阶段锚点通过known-feasible门限。该数值容差不能解释成工程效果或其他KPI区间。范围仍是同一公共BESTEST原型、同一方法学湖北电价和EF下已有理想资产的120日电费/调度比较，没有当地设备投资、实测校准或收益验证。

## 原样保留与失败历史

- 旧 `run_three_city_climate_dispatch.py` SHA256：`433fc8f42ebc591b747eca36cfa60bcc47de102319d19c55fd3b12f5e1efc27a`。
- 旧 `refine_three_city_climate_dispatch.py` SHA256：`bdcc0140db9d31cf0dd9f7bb489c0bf2020c1603c01728795ad189360b984d3f`。
- 对旧run_01及refinement_02分别复核原协议、输入、代码和101/89份结果文件身份；全部一致，没有重跑或改日期计新结果。
- 专项第1轮：32项，19通过/13失败。失败揭示深Windows路径下原子临时文件超过路径限制；全部日志和输出保留。
- 第2轮：33项，32通过/1失败。原子写入已成功，额外人工超长路径测试使用普通Win32读取而失败；只修正测试读取端，生产入口未再变化。
- 第3轮：33项全部通过；不把前两轮通过数累计成最终通过数。

最终新入口SHA256：`a30d94a922586eaa55519b470e8ea4489b6de8fb619b9271cfd6e2305429ee12`。最终测试文件SHA256：`a7574088670aeb15b0ee2c0a5962a196358ebe4522d5e7fea03215cbb7e5d082`。真实运行冻结46份来源文件及17份代码依赖，完整SHA绑定见 [真实协议](wuhan_2880h_01/protocol.json)。Python3.12.7、NumPy1.26.4、pandas2.2.2、SciPy1.15.3、PyYAML6.0.3；平台与软件版本也被冻结。

## 实际命令与复现

以下命令在原工程根目录执行；这些目录已存在，重跑须使用新的目录名和新的审计输出文件名。

```powershell
python -m pytest tests/test_three_city_climate_dispatch_resilient.py -q --basetemp docs/implementation_20261002_followup/parallel_stage_03/reliability/pytest_fault_injection_03 --junitxml=docs/implementation_20261002_followup/parallel_stage_03/reliability/tests_03.xml
python scripts/run_three_city_climate_dispatch_resilient.py freeze --cities wuhan --out docs/implementation_20261002_followup/parallel_stage_03/reliability/wuhan_2880h_01
python scripts/run_three_city_climate_dispatch_resilient.py run --out docs/implementation_20261002_followup/parallel_stage_03/reliability/wuhan_2880h_01
python scripts/run_three_city_climate_dispatch_resilient.py verify --out docs/implementation_20261002_followup/parallel_stage_03/reliability/wuhan_2880h_01
python docs/implementation_20261002_followup/parallel_stage_03/reliability/verify_resilient_exports.py --run docs/implementation_20261002_followup/parallel_stage_03/reliability/wuhan_2880h_01 --out docs/implementation_20261002_followup/parallel_stage_03/reliability/wuhan_export_audit_01.json
python docs/implementation_20261002_followup/parallel_stage_03/reliability/build_validation_manifest.py
```

freeze默认三城，也可在运行前冻结指定城市子集；run不能在执行后改变城市集合或预算。freeze验证原准入与准备档案，包括原始来源快照；缺少源材料的分发目录不能以缺失身份继续冒充有效运行。该入口没有提供恢复已中断任务或重用历史baseline的接口。

本阶段未新跑乌鲁木齐和固原2880h优化：新入口数值路径由武汉新跑覆盖，两城由故障控制测试覆盖，rc4原数值证据单独原样保留。本阶段未重复旧882全回归；旧核心没有变化，新批处理由上述专项及新真实运行核验。

结果入口为 [最终测试XML](tests_03.xml)、[真实manifest](wuhan_2880h_01/manifest.json)、[独立导出审计](wuhan_export_audit_01.json) 和 [验证总清单](validation_manifest.json)。本阶段没有PPT/PDF生成。
