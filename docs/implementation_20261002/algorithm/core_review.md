# 新增物理扩展独立复审

日期：2026-10-02。审查 `physical_extensions.py`、`model.py` 接线、`accounting.py`费用归属及`experiments.freeze_investment_plan`可靠性默认值。审查者仅新增反例与本文，没有修改上述统筹所有权代码。所有反例是解析机制测试，不能当实际城市数据或完整季验收。

## 找到并重现的问题

| 编号 | 实际问题与影响 | 重现与修复状态 |
|---|---|---|
| PR-01 | 固定燃料/电池事件只检查权重及场景，未检查实际时间连续。库存逐行按dt递推，可将24h缺口当1h，逆序/重复时标也通过。 | 初库存1kg、每小时保留0.5、两行时标隔24h、需求0/0.2、零配送仍求得末库存0.05；电池同样可通过缺口。初次4项应拒绝反例全部失败。统筹已加实际datetime、组连续、不交错及dt检查；最终4项通过。 |
| PR-02 | 需量/容量基本费整个放入供热现金白名单，虽然电表包含冷服务。纯供冷也产生供热监管费用。 | 纯冷6kWh/EER3、月需量价10元/kW，系统基本费20元正确，供热现金却20元。统筹改为供热与共享非供热两个组件，含冷服务必须显式声明`tariff.heating_cost_allocation_fraction`。纯冷填0后系统目标22元、供热现金0；混合服务40%分摊保持系统/CVaR账33元。最终3项通过。 |
| PR-03 | 共享余热池约束只作用实际调度，原firm reserve/N-1按各设备铭牌相加，可能将同一资源作为两份保障能力。 | 两台直供各60kW、同池75kW、峰负荷50kW、reserve_margin=1：要求100kW保障，却按120kW铭牌通过，物理最多75kW。已实跑重现。统筹增加资源受限静态保障组合，在系统reserve/源N-1、管故障组合、电网portfolio接线；重新读取并复验后反例通过，关闭。 |

初次证据保留：[5项失败XML](core_review_initial_counterexamples.xml)、[初次测试源码](core_review_initial_counterexamples.py)、[reserve反例失败XML](core_review_reserve_initial.xml)。PR-01/02修复后 [7项通过XML](core_review_final_counterexamples.xml)；全部修复后 [联合验证XML](core_review_and_hard_lp_tests.xml) 为8项独立物理反例和12项硬权重LP解析测试均通过，共20项。独立正式物理测试为 `tests/test_physical_extensions_independent_review.py`。

## 已核对的正确接线和边界

共享资源以直供输出/换热效率及热泵输出×`(1-1/COP)`/效率计上游取热；压缩机电力不占余热资源。多等级热出力逐项汇总；同一源不能归属两个资源池；受冷服务/热排放影响的池显式拒绝。资源可用量仅作为显式标量或时序列，非有限/负数和缺少身份均拒绝。该式是标准能量余额，不代表现场资源量已验证。

新增保障组合使用声明时序中最小资源可用量，热泵取所有声明时段/等级最大的`1-1/COP`占资源比例；与最坏电耗分开保守处理。源N-1将故障源组合出力置零；资源额度同时作用于现有管故障组合与电网portfolio。重现的75kW池不能再凭两台铭牌形成100kW保障。它是保守静态容量检查，没有运行完整故障过程，不能宣称动态故障认证。

固定事件首时步使用保留率×显式初态，末时步单独加终态界；没有将单时步首末变量字典覆盖。库存/电池初态须位于已装/可投资容量内，组标量或逐组映射必须完整；权重不为1时拒绝。无固定模式则保留旧循环机制，不能把循环初态写成已备资源。

月需量使用逐月最大净进口的epigraph，容量制使用固定kVA及功率因子硬进口限额；峰值不乘代表权重。完整月份严格核验，部分月份只能显式标`modeled_hours_only`诊断；电表范围为所建能源系统进口，未包含其他楼宇电负荷。基本费按场景概率加总到系统目标、无概率场景费用进入CVaR。零需量费或其他目标求解时epigraph可以有松弛，输出保留其松弛，不能用变量值自动当实测峰。

资产冻结默认保留原服务/可靠性；显式诊断放松另标`replay_service_policy`。本复审没有新运行完整供暖季、费用/碳前沿或各原始工程案例，因此不扩张成工程验收。

## 本阶段复验身份

以下SHA256对应PR-01/02/03全部修复、8项独立物理反例通过后的读取版本；后续修改应另登记，不能直接沿用：

| 文件 | SHA256 |
|---|---|
| physical_extensions.py | `4c2604fb7448a08888ae13e2a8fe869b948328b89a5a83568a102018eb269e3d` |
| model.py | `b7f7a208da2c12554f5bb4b6080ad03761e188d79ef5e6aaa627b5cdd512cb9c` |
| accounting.py | `27c78a1a6bea854c3e7ed455c400ea06e92a06d17952704a4f6f25617ebe7b21` |
| experiments.py | `036318fac9cd667ba33e1b596f9742f31cf4b9951e0d02079327c74c806d1df4` |

复验命令：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONPATH='src'
& 'D:\app\anaconda3\python.exe' -m pytest tests/test_physical_extensions_independent_review.py -q
```

## 后续制冷KPI归属复验（保留以上快照）

统筹随后将`shared_non_heating_electricity_basic_cost`加入制冷分摊成本KPI，避免系统目标计费正确但制冷单位成本遗漏基本费。独立混合案例的冷服务6kWh、制冷电耗费2元、非供热基本费18元，制冷单位成本复算为`20/6=3.333333`元/kWh；纯冷案例为`22/6=3.666667`元/kWh。已给现有独立反例补实际KPI断言。

该微调后`model.py` SHA256为 `31939e4ea2c577558f1b616d31b3206c0b354cc6ac56ed175ec0198860bce950`；`physical_extensions.py`仍为 `4c2604fb7448a08888ae13e2a8fe869b948328b89a5a83568a102018eb269e3d`，accounting和experiments保持上表身份。最终8项独立反例（含新KPI断言）复验见 [core_review_final_kpi_tests.xml](core_review_final_kpi_tests.xml)。上阶段快照和失败证据保留，未替换成最后源码冒充先前版本。
