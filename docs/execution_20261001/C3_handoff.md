# C3 空间与品质交接

状态：独立代码、空间原型数据及专项测试完成；共享核心接入由 C0 负责。基线 11.11.1；工作日期 2026-10-01。未修改 C1 数据或共享核心，未运行完整供暖季/年度重求解，未向其他聊天发送消息。

## 已交付

| 文件 | 内容 |
| --- | --- |
| `src/heatplan/spatial_quality.py` | 唯一归属与物性保留、逐层送达功率聚合、候选筛选与实际投资改写、温级/COP/转换账本校验 |
| `tests/test_spatial_quality.py` | 46 项专项测试，含微型 MILP 可行域及电耗/成本接入验证 |
| `data/competition_spatial/s3_spatial.yaml` | S3 三节点、片区和系统映射；明确米制原型坐标；候选距离和接入规则；按米造价 |
| `data/competition_spatial/parameter_manifest.json` | 合成参数身份、单位、假设与敏感性范围 |
| `data/competition_spatial/geometry_provenance.md` | 几何、成本、物理边界与不能据此宣称的能力 |
| `data/competition_spatial/network_candidate_register.csv` | 由实际预处理生成的候选长度、筛选结果与管径总价 |
| `data/competition_spatial/validation_report.json` | C1 8784 行输入只读预处理、4 栋楼主表一致性、逐层账本示例、源温级报告、配置与文件哈希 |
| `data/competition_spatial/test_results.xml`、`test_output.txt` | 最终 46 项测试原始 JUnit 与 UTF-8 输出；0 失败、0 跳过 |
| `data/competition_spatial/manifest.json` | 专项完成标记与交付文件哈希；不代表总控集成或季节/年度认证完成 |

数据只含透明原型几何，非真实 GIS、地下管线或武汉实测数据。楼宇主表没有在 C3 复制成另一份权威数据。

## 最小接入

在配置继承和所有规划覆盖完成之后、`HeatPlanningModel(...)` 之前调用；规划和固定投资回放使用相同版本空间包。必须把返回的 `config` 传给模型，不能仅存报告后继续使用原配置。

```python
from pathlib import Path
import pandas as pd
import yaml
from heatplan.io import load_config
from heatplan.spatial_quality import preprocess_spatial_quality
from heatplan.model import HeatPlanningModel

root = Path.cwd()  # 工程根目录
config = load_config(root / 'data/competition_20261001/wuhan_rc_candidate.yaml')
frame = pd.read_csv(root / 'data/competition_20261001/wuhan_service_year_2023_2024_utc.csv')
spatial = yaml.safe_load((root / 'data/competition_spatial/s3_spatial.yaml').read_text(encoding='utf-8'))
prepared = preprocess_spatial_quality(config, spatial, timeseries=frame)
model = HeatPlanningModel(prepared.config, frame)
# 此处仅展示接入位置，不要求在专项内构建/求解全年模型。
# C0 在正式输出中保存 prepared.candidate_report / source_grade_report 和空间包哈希。
```

核心无需理解新的空间字段即可获得候选排除和造价作用：预处理直接改写现有 `pipes` 列表与每个 `diameter_options[].capex_total_yuan`。现有管流仍为静态 kW，`length_m` 不自动改变效率或泵耗。若候选被排除，与其对应的 `pipe_choice` 和 `pipe_flow_*` 都不会创建。代码对已安装容量或明确 N−1 管道的排除冲突报错，交由总控修正输入，避免悄悄删除要求。

建筑侧继续把原 `building_thermal.buildings` 交给 C2，不用汇总 H/C 替代每栋独立状态。实际结果导出后可调用：

```python
from heatplan.spatial_quality import aggregate_delivered_power
totals = aggregate_delivered_power(
    prepared.hierarchy,
    building_power_kw={name: delivered_heat_by_building[name] for name in prepared.hierarchy.buildings},
    # 如存在独立 DHW 服务，可传 fixed_service_power_kw={(node, grade): dhw_series}。
)
# totals.node_grade_kw / district_grade_kw / system_grade_kw
# 每个 key=(区域名, heat_grade)，value=同长度 NumPy kW 数组。
# 三层是同一送达热量的不同视图；不得相加计量。
```

`aggregate_delivered_power` 的输入必须恰好覆盖所有已登记楼宇，有限、非负、同长度。可以对建筑正冷量另作相同分组账本，但 heat_grade 仍是该建筑的供热元数据，不能将该账本声称为冷温级兼容验证。C0 如需要正式冷服务报表，应单列 node 冷量口径。

## API 与字段

| API | 返回/失败方式 |
| --- | --- |
| `build_hierarchy(config, spatial, buildings=None)` | `Hierarchy`；默认读 `config.building_thermal.buildings`。显式传 buildings 可检查 C1 CSV 记录。重复名称、重复可选 building_id、未知归属和矛盾 district_id/system_id 均报 `SpatialQualityError(ValueError)` |
| `aggregate_delivered_power(hierarchy, building_power_kw, fixed_service_power_kw=None)` | `PowerAggregation`，逐级送达 kW 数组。固定服务是独立服务，禁止再次传已覆盖楼宇的旧参考负荷 |
| `preprocess_spatial_quality(config, spatial, buildings=None, timeseries=None)` | `SpatialQualityResult(config,hierarchy,candidate_report,source_grade_report)`；不修改输入对象；在原始完整配置上调用一次 |
| `validate_source_grades(config, timeseries=None)` | 每个源/温级报告。低温直供或缺少温度报错；配置未显式列 allowed_grades 时按核心含义检查全部温级 |
| `heat_pump_conversion(source, supply_temperature_c, source_temperature_c=None, relative_humidity=None)` | 现有公式的 COP、单位供热电耗、环境取热份额。需 `type=heat_pump`、有限且 `1<COP_min<=COP_max` |
| `validate_conversion_dispatch(Q,W,COP,resource_input_kw=None,absolute_tolerance_kw=1e-6)` | 核验 `W=Q/COP`、`Q=Q_resource+W`。零出力允许零电耗；正热量无电耗会拒绝 |

以上可选尾参数中，`preprocess_spatial_quality` 的 buildings/timeseries、聚合的 fixed_service_power_kw、转换的资源温度/湿度，以及 dispatch 的 resource_input_kw/tolerance，均应使用关键字传入；以函数签名为准。

楼宇字段直接兼容 C1/C2：`name,node,heat_grade,area_m2,heat_loss_kw_per_c,thermal_capacity_kwh_per_c`。面积/H/C 必须正且有限；所有额外字段原样保留。`district_id`/`system_id` 若提供必须与映射一致。当前层级不要求每栋有坐标，因此不宣称接户支线长度已校验。

空间包字段：

- `schema_version: 1`、`coordinate_system: local_cartesian_m`。
- `systems: [{name}]`；`districts: [{name,system}]`；`nodes: [{name,district,x_m,y_m}]`。节点必须恰好覆盖配置节点，不能遗漏 plant。
- `candidate_rules` 必须恰好覆盖原配置管道，每项包含 `pipe,route_factor>=1,max_route_length_m>=0,connection_allowed:boolean`。`allow_cross_system` 默认 false。
- `cost_mode` 取 preserve 或 per_m；per_m 另需 `option_costs:[{name,yuan_per_m,fixed_yuan}]` 恰好覆盖管径选项，金额非负。per_m 替换原总价，重复对已保留边计价不会叠加；有边被移除后不要用原规则对裁剪配置二次预处理，应从原始配置重跑。
- 路线长度是 `hypot(dx,dy)*route_factor`。大于上限剔除，等于上限保留；接入禁止和未允许的跨系统连接同样剔除。

## 实际验证

专项命令：`python -m pytest tests/test_spatial_quality.py -q`。首轮 40 项通过；补充后最终 46 项通过（0.93 秒，另输出 JUnit）。最终测试的 C3、model/io/linear/building_thermal 文件哈希前后一致，身份与输出见 `validation_report.json`。这些是专项短例，不是正式投资推荐；没有生成需要季节/年度认证的新投资方案。

已测试的实质效果：

1. 原型 300/400 米直角边、绕行系数 1.2，路线 600 米。最大距离 600 时一小时 MILP 供热 10 kW 成功；改为 599 时管道变量消失，同一服务约束下 HiGHS status=2 不可行。这是预期负例，不是遗留失败。
2. 合成价格 `100+600*2=1300` 元进入管径选择的实际年化目标系数 `1300*CRF`。
3. 50℃直接热源不允许 75℃服务。10℃资源经热泵供 75℃可行，现有核心电力负荷系数实测等于 1/COP；一小时 10 kW 出力对应正电耗。缺电/环境取热双计的 dispatch 负例拒绝。
4. S3 的低温、潮湿除霜及较暖工况下，新校验器 COP 与既有 `_cop` 相等；S1/S2 既有源温级检查通过。
5. 重复/未知/矛盾归属拒绝；多栋楼→节点→片区→系统守恒，多节点同片区有效，高低温级不混合；面积、H、C、占用和舒适差异保留。
6. C1 完成标记存在后，以真实候选配置和 8784 行全年输入做只读预处理，未构建或求解全年模型。4 栋楼与 C1 主表逐节点物性一致；系统总面积 44000 m²、H=43.9 kW/℃、C=2730 kWh/℃。所有热泵动态输入列均逐行检查；该步骤本次约 0.056 秒，不能当全年建模/求解耗时。

S3 候选路线 1452.991397 m，小于 1600 m 上限。DN80 造价 290598.279417 元、DN100 440897.419125 元，均为合成规划假设，非优化结果或真实报价。报告内另有两时段任意送达功率守恒样例，已标记不是优化/观测轨迹。

## 失败、限制与待接入点

专项当前无未通过测试。曾有一次证据包装脚本在测试已通过后，向 Windows GBK 控制台打印中文路径失败；已显式采用 UTF-8 捕获、ASCII 安全摘要重跑，最终包装命令和测试均 exit=0，该诊断保留在报告中。未启动全项目回归；共享核心由 C0/C2 并行更新，最终系统验收应在总控冻结版本后执行。没有声称完成完整季/年度方案认证。

需要 C0 执行的最小事项：

- 在所有 S3 正式建模入口与投资回放入口统一应用返回配置，并把空间包、预处理前后配置身份与报告纳入 run manifest。已固定的管道投资必须与候选空间一致，不能在回放时悄悄换距离或成本。
- 与 C2 联合验证建筑物性改变确实传到节点热量和上层电力/源容量约束。C3 已保留各楼宇物性并提供送达聚合；没有仅凭本专项账本宣称完整多尺度动态验收通过。
- 保存实际 building_heat 轨迹并调用聚合；独立 DHW 必须单列，旧参考负荷不能叠加。
- 现有核心按每条管道的同一 grade 输送，不支持逐管 `allowed_grades` 子集或 `from_grade/to_grade`。C3 对这些字段报错；需要新增能力时由 C0 修改核心并补测试。
- 未实现串联增温器从另一个低温节点扣取热量。当前热泵为原有源设备，环境资源取热单独核算；不得把直接热源和热泵简单重命名为串联系统。
- 真 GIS、真实地下管网、逐栋支线、水力约束、变流迟滞和现场验证均未实现，合成成本尚需真实报价/工程分项核对。

独立专项到此停止，等待总控读取交接；不会自行扩大范围或重求解。
