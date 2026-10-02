# 本轮物理约束、费用和空间接口说明

2026-10-02。改动直接落在原 `north_heat_challenge` 主工程。新增接口为可选接口，不向城市配置偷偷添加工程参数。下面的数值算例只解释方程，真实参考案例另有来源准入和实验目录。

## 1. 同一余热资源的共同额度

入口：`src/heatplan/physical_extensions.py::add_shared_heat_resources`，由 `HeatPlanningModel.build()` 调用。

若换热直供和热泵共用同一个工业余热源，分别限制两台设备容量仍可能重复使用上游热量。新增资源池在每个时步约束：

\[
\sum_{i\in direct,g}Q_{igt}/\eta +
\sum_{i\in HP,g}Q_{igt}(1-1/COP_{igt})/\eta
\le A_t.
\]

`Q` 为设备输出热功率，`A` 为资源侧可用热功率，单位均为 kW；`eta` 是显式给出的资源到设备换热效率。热泵的压缩机电力形成的热量不占余热额度。各温级共同消耗同一池，而不是每个温级再分配完整额度。

配置 `shared_heat_resources` 为列表，每项包含 `name`、`source_names`、`transfer_efficiency`，并在 `available_heat_kw` 与 `available_heat_column` 中恰选一个。名称与成员必须唯一；同一源不重复归池；只支持 `direct_heat` 和 `heat_pump`，COP 必须大于 1。涉及制冷取排热的源需要另行定义双向资源余额，当前接口明确拒绝混用。

导出调度增加 `shared_heat_resource_use`，摘要增加 `shared_heat_resource_max_overdraw_kw`。供热保障容量也增加同一资源额度：系统基础组合、源 N−1、原管道故障组合与电网保障组合均不能把同一资源的设备铭牌重复当成可用保障能力。静态保障计算采用声明时序内最小资源量、最大的热泵上游取热比例，属于保守容量检验；设备停运过程和逐时恢复需要另做事件仿真。

解析反例：两台各 60 kW 直供设备共用 75 kW 资源，不能形成 100 kW 保障。回归验证无独立补充源时不可行；允许独立备用投资时至少补足 25 kW。数值是单元测试夹具，不是八钢实际能力。

## 2. 寒潮与中断事件的初末库存

原循环库存仍用于声明的周期模型。新增 `EventStateBoundary` 为一次事件提供显式初始库存和终端要求，避免从自由循环初态中获得没有准备的燃料或电量。

燃料链使用 `boundary_mode: fixed`、`initial_inventory_kg`，以及 `terminal_inventory_min_kg`、`terminal_inventory_max_kg` 至少一个。微网电池对应 `battery_boundary_mode`、`battery_initial_soc_kwh`、`battery_terminal_soc_min_kwh`、`battery_terminal_soc_max_kwh`。所有初末值可以是标量，或完整覆盖状态组的映射，不能漏组。

首时步用 `保留率 × 显式初态` 替代循环前驱；后续逐时递推保持原设备方程。初态不能超过安装/投资容量，末时步单独加界。单时步事件也保留损失，不能因首末变量同名抵消。固定事件要求实际时间戳、组内连续的时间步、同一场景、权重为 1；缺小时、重复/倒序时间以及代表日加权均拒绝。

摘要 `event_state_boundaries` 导出初态、终态界和求得终态。这证明计算使用了何种已声明储备，不证明储备量在现场存在。事件输入需有实际库存依据或明确的研究压力设定身份。

## 3. 月需量费与容量费

逐时 `electricity_price` 仍为元/kWh 电量费。新增 `electricity_tariff` 单独表达基本费，避免把元/kW·月、元/kVA·月混入逐时能量价格。

| 字段 | 语义 |
|---|---|
| `method: monthly_demand` | 对每个场景、日历月设置计费需量，约束其不低于各小时净进口；费率 `rate_yuan_per_kw_month` |
| `method: monthly_capacity` | 以显式合同容量 `contract_capacity_kva` 计费；费率 `rate_yuan_per_kva_month`；净进口上界为合同容量×显式 `power_factor` |
| `meter_scope` | 必须为 `modeled_energy_system_import`，明确只核算模型能源系统电表 |
| `coverage_policy` | 默认 `require_complete_months`；部分月只可显式选择 `modeled_hours_only` 并标明局部时窗账本 |
| `heating_cost_allocation_fraction` | 冷热共表时必填，0—1；热服务基本费与其余服务基本费按此分配 |

费率可为标量或恰好覆盖所有月份的映射。要求有序、唯一、有效的日历时间，权重为 1。完整月模式逐项核对从月初至下月初的所有时步。部分月的模型峰不能宣称真实整月计费峰。

净进口包括热泵/电锅炉/制冷/泵等用电和电池充电，减去模型定义的本地可再生电、备用发电和电池放电；按现有核算边界，CHP 外售电另记，不自行抵扣。它不包含未建模的照明、插座和其他楼宇电负荷。

基本费按场景概率进入系统目标；各场景未乘概率的金额进入对应风险费用，用于 CVaR。冷热成本分解增加 `heating_electricity_basic_cost` 和 `shared_non_heating_electricity_basic_cost`，二者之和才是总基本费；制冷平准成本也包含分配给制冷的基本费，避免系统总账正确但冷热 KPI 漏项。

`monthly_electricity_billing` 同时导出逐月实际模型进口峰、计费变量、单价、费用和 epigraph 松弛。零费率或碳主目标时计费变量可能不贴紧峰值，故两者分别保留。此模块通过解析测试，尚未把未核验用户合同写进城市正式投资案例。

## 4. 空间路线与唯一候选边

`spatial_quality.py` 现在优先消费候选规则中的显式 `route_length_m`，要求正值、不短于端点直线距离，并附 `route_length_basis` 和非空 `route_provenance`。如给出 `physical_edge_id`，则同一批候选中不得重复。原 `route_factor` 路径保留给明确使用该简化的配置。

`spatial_adapter.py` 将多栋建筑的道路路径拆为唯一共享走廊；路径按方向引用候选边，费用和容量应按唯一边建模。它保留 OSM 原生节点和版本，不把平面交叉自动当成道路连通。当前三城输出的管径、热损、泵、费用和建设许可保留为空：几何有依据后，仍须按实际工程对象补齐物理参数，才能进入供热规划。

实跑结果及可复现入口见 [空间专项](spatial/README.md)。三城参考路线共 12 条成功、3 条固原吸附失败，失败没有用直线补齐。

## 5. 固定投资、同服务回放与诊断

`freeze_investment_plan` 默认 `relax_reliability=False`，因此资产冻结之后保留原缺供上限和保障要求。摘要标记 `replay_service_policy=original_service_requirements`。

原寒潮、供应压力和 Monte Carlo 短缺诊断入口显式使用 `relax_reliability=True`，另标 `diagnostic_relaxed_reliability`；它们需要允许缺供才能量化脆弱性，不能作为原服务验收通过。Monte Carlo 返回表另标 `generated_research_stress_scenarios`，不把抽样扰动视为历史实测。

全工程回归发现并修复了旧 Monte Carlo 入口依赖原隐式放松默认的问题。RC 碳价扫描的误置拒绝条件也已移到真正不支持 RC 的旧负荷移峰消融入口，使合法 RC 扫描可以使用。

## 6. 核验与复现

核心解析/反例见 `tests/test_physical_extensions.py` 和 `tests/test_physical_extensions_independent_review.py`；空间见 `test_spatial_quality.py` 与 `test_spatial_adapter.py`；RC 入口见 `test_rc_experiment_entry_guards.py`。

独立复审曾发现事件时间缺口、冷热基本费分摊和共享资源保障重复计算三个问题，已保留初次失败证据并修复，见 [复审记录](algorithm/core_review.md)。本轮最后追加制冷 KPI 基本费回归；新旧代码版本分别保存，不把旧实验声称为新版本输出。

完整季参考实验消费来源锁定的公开原型、NASA 格点气象及官方价格/因子。模型内置数值检查之外，`scripts/verify_reference_case_experiments.py` 不导入模型，直接从逐时调度独立重算室温递推、容量、热冷、电量、账单和排放。它的证明范围仅是这个单区理想设备参考系统。
