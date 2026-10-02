# C2 楼宇 RC 接入交接

状态（2026-10-01）：C2 独立模块完成并停止扩展，63项测试通过；当前 C1 候选年度/供暖季输入校验通过，另完成真实输入首24小时的楼宇供需适配器求解。API已可接入，全文为最终交接。没有启动完整季或年度求解，没有修改共享核心。全系统接入与认证由总控后续确认。

## 已定 API

```python
from heatplan.building_thermal import (
    add_building_thermal_constraints,
    validate_building_thermal_inputs,
)

# IO 验证：返回 BuildingThermalInputs；禁用/缺失节返回 None
prepared = validate_building_thermal_inputs(config, frame)

# HeatPlanningModel.build 内：应在冷热供需平衡创建之前调用一次。
# 依赖 model.config / ts / dt / problem / variables，核对可选 boundary_groups。
self.building_thermal_block = add_building_thermal_constraints(self)
block = self.building_thermal_block  # 禁用返回 None
```

`BuildingThermalBlock` 的可用返回字段：

| 字段 | 类型/语义 |
| --- | --- |
| `temperature[(name,t)]` | 变量索引；℃，时段末室温 |
| `heat[(name,t)]` / `cooling[(name,t)]` | 变量索引；实际到楼宇热/冷 kW |
| `mode[(name,t)]` | exclusive 且冷热上界均正时才生成的二元索引 |
| `node_heat[(node,heat_grade,t)]` | 实际热量总和变量索引，可直接在热平衡用系数 -1 |
| `node_cooling[(node,t)]` | 实际冷量总和变量索引，可直接在冷平衡用系数 -1 |
| `covered_node_grades` | `frozenset[(node,grade)]`，只按此集合替换原热负荷 |
| `covered_cooling_nodes` | `frozenset[node]`，用于替换相应冷负荷 |
| `inputs` | 已校验的物性/得热/温度数组与边界输入 |

注册到 `model.variables` 的键依次为 `building_temperature, building_heat, building_cooling, building_heat_mode, building_heat_by_node, building_cooling_by_node`。单楼组的 node 变量直接别名楼宇变量，多楼组增加求和等式。别名不应重复计费用或服务量。

## 总控必须接入的物理边界

1. 在旧冷平衡之前注册 block；冷设备创建可前后，但冷平衡必须有实际设备输出项。
2. 只对 `covered_node_grades` 跳过旧固定需求、`unserved`、DR守恒、虚拟 `building_thermal_state`、计量减热与高低温需求转移。未覆盖温级（如DHW）保留原固定服务。若DHW与RC同温级，必须显式单列服务，不能因覆盖而自动保留旧总负荷。
3. 热平衡改为供应/网络/储热净值减 `block.node_heat[...] == 0`（另加独立固定服务）；冷平衡同理。不得把 `unserved` 加到 RC 实际送达量中。
4. 本模块不创建热源、冷机、费用、碳、缺供松弛或建筑可靠容量信用；源网储仍由核心负责。
5. `export_trajectory(values)` 导出楼宇宽表；`comfort_summary(values)` 返回按楼宇/边界组的指标表。旧缺供率为零不能替代新舒适/物理审计。节点热量与楼宇热量是同一服务，汇总只能选一层。

## 当前边界与配置要点

- 时钟必须带时区，转换为UTC；`time` 为时段起点标签，温度为该时段末状态。组内步长必须等于 dt，拒绝缺时、重复、交织组、跨情景组。代表日之间允许时间跳跃，但必须使用不同 boundary_group。
- `boundary_mode` 支持楼宇值优先于模块值，默认 `fixed`。fixed 首时段读取 `initial_temperature_c`；默认期末下限为初温。可显式设 `final_temperature_c` 等值，或 `final_min_temperature_c/final_max_temperature_c` 区间；等值不能和区间同时给出。
- 楼宇 `boundary_conditions: {group: {initial_temperature_c: ..., final_min_temperature_c: ...}}` 可覆盖各组固定初末态。cyclic 首时段承接本组最后温度，不固定必填初温参考值，拒绝 fixed 末态/分组覆盖混用。
- `internal_gain_kw` 与 `internal_gain_column` 二选一；`solar_gain_column` 为已经转换为kW的太阳得热。逐时舒适列替代标量界，但标量仍须合法。
- `max_heat_kw/max_cooling_kw` 可选，0表示禁用该服务；默认上界由RC、起末舒适区间推导。`simultaneous_mode: exclusive` 默认创建必要的冷热互斥二元变量，`relaxed` 为显式放松，不能自动视为物理认证。
- 楼宇 `heating_enabled/cooling_enabled` 可选布尔，默认 true；false 会把相应实际功率上界置0，不能用缺失服务维持不可能的舒适。C1当前 `cooling_enabled: true` 已兼容。
- 可设楼宇或模块 `heating_service_column`（0/1）用于供暖服务指标分母；不提供则全时段都属于指标范围。此字段不取消温度约束或禁止冷热供应。
- 舒适约束和 degree-hours 是时段末采样口径，不能声称小时内连续满足变化的占用界限。初温预热来源需由数据/总控另行说明；模块输出初末热库存差，未计价初温。

## 已补设计峰值接口（总控可接入）

```python
from heatplan.building_thermal import building_heat_reference

# 预筛查仅校验和计算数组，不创建优化模型。
prepared = validate_building_thermal_inputs(config, frame)
reference = building_heat_reference(prepared, design_outdoor_temperature_c=-5.0)
# 已注册的规划模型使用同一函数的便捷封装。
reference = block.heat_reference(design_outdoor_temperature_c=-5.0)
```

`-5.0` 仅为调用示例，非武汉正式设计温度。总控必须将冻结的同一设计气温传给两处。

- `reference.node_grade_reference_kw[(node,grade,t)]`：各楼 `H*max(0,Tmin[t]-Tout[t])` 之和，用作逐时保守参考/容量筛查。没有重复固定到 RC 平衡。
- `reference.node_grade_design_peak_kw[(node,grade)]`：各楼 `H*max(0,max_t(Tmin[t])-design_outdoor)` 之和。将设计所需最低舒适温度的逐时最大值用作设计室温。没有把可选预热的最高舒适界当必需峰值。
- `building_design_peak_kw[name]` 与 `design_temperature_c[name]` 保留逐楼依据，`design_outdoor_temperature_c` 保留实际使用气温。
- 不传设计气温则使用当前 frame 最低气温，短例/代表日缺少极端天气时不可宣称气候设计峰值；正式应显式传值。参考/设计均保守不抵扣内部或太阳得热，不给建筑蓄热可靠容量信用；各楼峰值直接相加，不给未经证明的同时率优惠。
- 这是稳态筛查；初温恢复、爬坡、网络损失、供冷和设备降额仍需核心约束/连续求解验证。DHW及未覆盖温级的峰值由核心单独相加。

`comfort_summary` 已增加 `no_simultaneous_heat_cooling_passed`。`building_physics_passed` 无论 exclusive/relaxed 都必须无冷热同开；`relaxed_equations_passed` 仅表示RC等式、温度/末态、功率界通过。relaxed 解若无同开，可通过楼宇物理检查，但此模块不自动证明全模型MILP gap，不实现冲突回补求解器。

## 方程、规模与合理简化

```text
a = exp(-H*dt/C)
b = -expm1(-H*dt/C)/H
T_end = a*T_start + b*(H*Tout + actual_heat - actual_cooling + internal_gain + solar_gain)
```

对数值输入要求面积/H/C/dt为有限正值，室温与室外温度超过绝对零度，引用列存在、数值有限、冷热得热非负、逐时上下界有序。初态可以低于舒适界，以表达恢复过程；舒适仅约束时段末。非常小的b若会被当前LinearMILP的系数清理丢掉（b<=1e-12），显式拒绝并提示重定单位，避免悄悄丢失冷热耦合。

变量基本规模为每楼每时段3个连续变量；当两种功率上界都正且选exclusive时加1个二元及2行。每楼每时段1条RC等式，每固定边界组1条末态约束；多楼同节点同温级才增加热量求和变量/行，同节点多楼增加冷量求和变量/行。代表日权重仅进统计，绝不乘到dt、热容或状态转移上。

H/C都是楼宇总量，不再乘面积；面积用于归属和单位面积指标。输入阵列独立保存，不随后续原frame变更而静默改变状态方程。模块采用单个显热状态、固定H/C和区间常值天气/功率/得热；未实现湿度、潜热、复杂建筑围护多节点或温控器分时开关。字段合法不代表参数经过当地辨识，来源身份仍由C1元数据负责。

## 测试、实际输入兼容性与求解证据

执行环境：Windows、`D:/app/anaconda3/python.exe`，Python 3.12.7、SciPy 1.15.3、pandas 2.2.2、NumPy 1.26.4。

```powershell
python -m pytest tests/test_building_thermal.py -q --durations=3
```

最终结果：`63 passed in 1.07s`。代码改动后的首轮57项、补参考/互斥审计后59项、补冷热禁用标志与输入校验后63项，均无非预期失败。求解测试逐条重算约束行和上下界；正例要求HiGHS status=0且存在向量，反例明确要求status=2（不可行），不是把无解当成功运行。

| 验证 | 实际覆盖 |
| --- | --- |
| 断热解析降温 | -5℃环境，H=2、C=10，8个半小时步，与连续解析解逐点比较 |
| 恢复与初末态 | 断热3h后恢复20℃、每组独立初温、每组末态约束、单时段和多时段cyclic |
| 热冷物理 | 正负符号、同轨迹冷热切换、H/C差异、内部/太阳得热、逐时舒适列 |
| 真实供热不足 | 外部供热上界10kW，维持20℃需20kW，即使另有旧unserved变量仍不可行 |
| 聚合与DHW | 多楼热冷求和、跨node/grade隔离、单楼变量别名、覆盖集合不吞未覆盖温级 |
| 冷热互斥 | exclusive禁止同时5kW热/冷；relaxed允许数学解但物理标志失败；无同开的relaxed解物理标志通过 |
| 指标与反作弊 | dt/period_weight不污染状态；删掉实际热量、保留虚假舒适温度时RC残差失败 |
| 输入与拒绝组合 | 面积/H/C/初温/容量、缺列/NaN/Inf/负得热、重复楼名、矛盾末态、naive/gap/重复时间、跨情景/交织边界组 |
| 参考与规模 | 统一预筛查/规划参考，无得热/蓄热信用；固定舒适单向服务省去模式二元变量 |

C1已写入 `complete:true, status:data_prepared_not_solver_certified` 的数据manifest。本次只读兼容性检查：8784行服务年度与2928行供暖季均通过 `validate_building_thermal_inputs`；4栋聚合楼宇，总面积44000m²，分别为单个连续boundary_group。默认采用本帧最低气温-8.12℃时，保守设计热峰值district_a/low=624.268kW、district_b/low=522.400kW；这是输入情景筛查值，不冒称规范设计气温或已认证可靠容量。

另用候选供暖季CSV首24行（北京时间2023-11-15全天），运行 `LinearMILP` 最小供需适配器：保留C1四楼完整物性和末温20℃，每节点热源固定上限为上述参考设计峰值2倍，冷源固定上限1500kW；建立 `source - block.node_heat/cooling == 0`，目标为总冷热量。没有接入设备效率、投资、管网或经济目标，因此本结果仅证明模块与真实输入的短窗可求解。

| 项 | 实测 |
| --- | --- |
| 模型规模 | 576变量、484行、96楼宇模式二元 |
| 求解状态/gap | status=0；mip_gap=8.644341292948889e-15 |
| 建模/求解墙钟 | 0.00705s / 0.01686s，单次小例，不外推全年性能 |
| 实际送达 | 热4313.721718kWh；冷1.8243e-11kWh（未在导出中裁零的数值残差） |
| 检查 | 最大RC残差3.55e-15℃；楼宇物理通过；LinearMILP全部约束/界/整数与目标重算通过 |
| 投资身份 | 无投资决策，仅上述固定供能上限的测试夹具 |

上述短窗复现逻辑：`frame=pd.read_csv(season_csv).head(24)`，`config=load_config(candidate_yaml)`，用含config/ts/dt=1/problem=LinearMILP()/variables={}的适配器调用 `add_building_thermal_constraints`；逐个node_heat索引添加热源变量（ub=2×reference峰值，obj=1），逐个node_cooling索引添加冷源变量（ub=1500，obj=1），各自用等式连到实际楼宇量；`solve(mip_gap=1e-8,time_limit=10)`。供暖季/年度均未重求解。

本次身份（SHA256，后续任一数值依赖变更应重新验证）：

```text
src/heatplan/building_thermal.py
1a1ce76fa1ba9aa151e25d036591a01e1e6d54dfbfd9b0619a385a5a4ff38c40
tests/test_building_thermal.py（也定义全部合成测试数据/容量）
3dbe19c91d0b1e0790075b5bf4a810167a01bb6e8ca0b820dd0b603c7a785b7c
data/competition_20261001/wuhan_rc_candidate.yaml
cc26ee2c7d8a7db56b9347012c7d1b122dd8e651096433061d8f2a0d94dd5d3b
上述yaml继承后的effective_config（json.dumps(sort_keys=True,ensure_ascii=False) UTF-8）
0228ee9749098fc51b031b9b84ac12de48a61984248a10eb807d28e102252a5a
data/competition_20261001/wuhan_heating_season_2023_2024_utc.csv
7abf01f92fb68174245b0a63bf8ef1796a53125dd2069c71eb0dc8cee5e3fec2
```

## 失败、限制与待接入点

没有非预期测试失败；故意构造的供热不足、禁止冷热服务但要求舒适、冷热同开、未恢复每组初温等反例正确判不可行。年度/季节输入校验通过不等于全季优化可行或舒适认证。

1. 总控负责核心热/冷平衡、费用/碳/实际服务分母、导出字段和容量预筛查统一接入；只调用本模块而未消费返回的节点热冷量，会产生没有能源来源的温控变量。需要全模型数值/物理审核，不能只看本模块 `building_physics_passed`。
2. 正式连续回放必须fixed初态并交代初始20℃预热能量；C1目前把预热定义在比较边界外。period_weight不能替代时序连续性，cyclic代表日不证明跨日或跨季蓄热。
3. C1全年CSV若用于供暖季专项指标，应显式设置 `heating_service_column` 为其供暖季标记列；否则默认统计全年而非自行推断供热期。既有末态20℃在本次两组全输入均与末时舒适界兼容。
4. relaxed可以减少全年整数规模，但任何实际冷热同开都使严格物理标志失败。松弛下界、补冲突模式变量和回退exclusive的流程由总控实现与验证，本模块未宣称已实现该寻优算法。
5. 未执行完整HeatPlanningModel联合求解、完整季/年度重求解、当地室温实测辨识或独立外部物理认证。短窗供能夹具不代表候选投资能满足实际网络或全部年度约束。

只写入本模块、专属测试、本C2交接；共享 `model.py/io.py/linear.py/chronology.py` 与C1/C3产物均未修改。
