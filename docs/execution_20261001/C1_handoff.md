# C1 数据交接（已完成指定交付，2026-10-01）

状态：数据与配置候选已生成；17项测试通过；未启动完整季或年度求解。只写本角色脚本、数据包、测试、此交接及C1测试输出目录，未改原始天气、io.py、model.py或其他角色代码。交付不是模型认证。

## 交付入口

- `scripts/prepare_competition_case.py`：离线可重建，原始天气准入、时钟/单位验证、北京时间派生、窗口选取、YAML与哈希清单生成。命令 `python scripts/prepare_competition_case.py`，成功后最后写manifest；失败先使旧完成标记失效。
- `data/competition_20261001/wuhan_rc_candidate.yaml`：继承 `data/public_scenario_inputs/scenario3_wuhan_joint_public.yaml` 全链，实际保留原冷热设备、网络、电网、地源年度比值和政策。
- `data/competition_20261001/building_archetypes.csv`：楼宇主表已从早期草案固定为本轮候选，4栋、3类合成原型；C2/C3可直接按name/node对接。
- 同目录 `README.md`：参数、单位、服务范围、初末态、价格碳身份、旧指标和政策处理。
- 同目录 `manifest.json`、`source_inventory.json`、`clock_verification.json`、`quality_report.json`、`service_scope.json`、`validation_record.json`、`test_result.txt`及evidence原JSON：可追溯证据。
- `tests/test_competition_data.py`：真实数据质检、负例和C2接口验证，无MILP求解。

## 时序与服务边界

| 角色 | 文件 | 实际UTC时段起点范围 | 小时数 |
| --- | --- | --- | ---: |
| 主完整服务年 | wuhan_service_year_2023_2024_utc.csv | 2023-06-30 16:00Z至2024-06-30 15:00Z | 8784 |
| 对应供暖季 | wuhan_heating_season_2023_2024_utc.csv | 2023-11-14 16:00Z至2024-03-15 15:00Z | 2928 |
| UTC公历年备选 | wuhan_calendar_year_2023_utc.csv | 2023-01-01 00:00Z至2023-12-31 23:00Z | 8760 |

主planning.annual_scope为Asia/Shanghai 2023-07-01T00:00:00+08:00至2024-07-01T00:00:00+08:00，expected_hours=8784。它完整覆盖假定2023-11-15至2024-03-16半开供暖服务期，包含3月15日全天和2月29日24小时。该日期是项目假定，非武汉法定期。全年统一冷热RC；project_heating_season_flag只作报告范围，不控制全年热/冷可用性。

主服务年旧chronology_quality_audit会得到完整年但非公历对齐，需要总控接入明确annual_scope。备选公历年使用 `wuhan_rc_calendar_2023_candidate.yaml`，该配置只改annual_scope为UTC年并注明地源账本范围；末8小时真实接入原2024 LST天气，不循环复制年初。UTC备选未覆盖整个跨年冬季，不能冒称同一服务年轨迹。

time与timestamp均UTC ISO Z；local_time为北京时间；功率为小时平均，温度和SOC为时段末。每个文件单一boundary_group、period_weight=1。年度/季节都是独立固定初态方案，未宣称季初态承接年度轨迹。服务年及季节最低温-8.12℃，已记录实际比继承设计-7℃更冷的情况。

## 关键发现与数据身份

武汉新增仅同点UTC/LST各48小时小样本，缓存原JSON和请求URL：唯一匹配偏移+8h，40小时×4字段=160对完全一致；与既有CSV的48小时×4字段=192对完全一致。不是整年气象精度校准，也不把新API v2.10.2倒填为旧CSV版本。

原 `solar_irradiance_kwh_m2_hour` 名称错误：CSV保存的NASA原数值实际Wh/m²。新包同名kWh列除1000，另给平均W/m²列；source_time_lst/source_weather_file逐行回溯。原文件保持不变。继承solar_heat_availability仍是按各原气象年95%分位归一的太阳热源代理，建筑太阳得热使用合成有效孔口因子0.015×面积×GHI。

三地2020—2024共15文件均查连续性、闰年、填充值和数值范围，并存SHA256；本包仅输出武汉UTC，不冒称S1/S2也完成实证时钟匹配。NASA属于格点/再分析；当地站点/建筑观测均未补造。

电价按北京时间映射原模板，明确为0.30—1.12元/kWh合成情景，并未核定武汉用户类别、价税或历史适用性。碳因子按既有官方资料统一2023湖北平均0.4044kgCO2/kWh，2025-12-31发布；对2023/24天气统一应用属于规划情景，不是2024实测或逐时边际因子。旧碳曲线留在reference列。

## 建筑、状态及主模型对接

4栋为apartments_a、office_a、apartments_b、retail_b，分别18000/7000/14000/5000m²，district_a/b分别25000/19000m²，热级均low。住宅/办公/零售共3类，所有参数标synthetic_assumption_not_calibrated。H强度0.9/1.1/1.0/1.2W/(m²·K)，C强度70/45/70/35Wh/(m²·K)，总H43.9kW/K、总C2730kWh/K。旧虚拟520/430kWh/K未当作实测RC。

18℃室内、-7℃室外、不计得热的设计热需求A597.5kW/B500kW，相对既有候选热泵A1650kW/B1520kW量级合理；这不是完整可行性证明。住宅夜间有人、办公室工作日占用、零售每日营业，均按Asia/Shanghai实际日期生成，未模拟节假日调休。占用舒适18—26℃，非占用16—28℃，字段按INTERFACES；逐时内得热与太阳得热为kW。

每栋initial_temperature_c=final_temperature_c=20，boundary_mode=fixed。相同预调节初态在比较边界外，末态强制恢复，不能提取净免费冷/热库存。两个PCM均初始空库initial_soc_fraction=0、final_soc_min_fraction=0、fixed；末态可留非负库存但不计残值。预充不存在，所有库存来自窗内充热。建筑和储热firm credit均保守关闭；设备原firm参数保留。

旧热/冷参考列完整保留并与原2023年度对应时段核对一致。楼宇RC覆盖处必须由总控替代旧需求等式、冷负荷等式及旧指标分母，不能重复服务；旧冷列=0时仍可能有RC制冷需要。旧thermal_inertia high/low已置空，DR比例置0，间歇因子置1、行为列解绑。C2真实validate_building_thermal_inputs已经接受整8784小时数据，4楼宇冷热上界有限、全年服务标识为1；这只验证接口，不替代主模型冷热守恒和求解验证。

## 政策逐项与待接入点

1. **年度地源平衡**：完整joint配置仍保留0.80—1.25提取/回灌比。独立冬季不能重新硬套年度比值；总控需定义季节适用范围或承接完整年度地源账本。C1没有静默删掉地源约束，季节CSV可加载不等于套用主YAML已物理可行。
2. **正式成本目标**：原S3 0.40→public 0.60→joint 2.00元/kWh继承链不再覆盖，实际主约束仍2.00。0.40是原内部目标且仍作joint_service_heat_affordability_reference，不标成题目强制数值。是否另做诊断场景由总控决定。
3. **旧占用温差≤950**和**清洁热比例≥0.90**保留；前者在虚拟状态取消后可能为空约束，后者需核对新实际热分母。不能用旧约束名称宣称RC室温验证。
4. **冷设备1.10倍峰值上限**保留，但原峰值是代理冷负荷；总控要防止它错误限制新RC实际冷需求。设备/电网/可靠性组合尚未完整求解。
5. **annual_scope接入**、RC实际热冷供给替换、总冷热成本/碳边界以及正式日志/证书均归总控。无需更名数据字段；C2字段已通过实际接口校验。
6. 新太阳单位修正只在本包；其他使用原始误名列的代码须识别其身份。本站点天气、合成H/C和价格的现实校准仍缺失。

## 验证结果与失败记录

最终命令：`python -m pytest tests/test_competition_data.py -q --basetemp=outputs/competition_20261001/c1/pytest_20261001_c` → **17 passed in 3.84s**，exit 0。测试包括原始坏数据反例、丢边缘拒绝、闰日/UTC年末真实补边、星期占用、电价时钟、辐射千倍单位修正、继承一致性、初末态/信用、SHA256、失败重建撤销完成标记和C2真实输入校验。

失败均保留在validation_record.json：首次默认Windows pytest临时目录拒绝访问（7 passed/10 errors）；首次指定角色临时目录父目录尚未创建（7 passed/10 errors），创建目录解决；再运行16 passed/1 failed为全零季节冷列CSV读成整数、年度读成浮点的dtype差异，改为比较数值与标签而不要求推断dtype相同，最终全部通过。无需更改系统临时目录权限或共享测试配置。

随后正式数据包重建成功：8784/2928/8760小时，data_quality_passed=true，manifest complete=true。无求解状态、gap或耗时优势数字可报告；完整MILP未启动。后续代码、参数、投资或服务口径若变更，须重建身份并重新验证，不能复用此数据QA为运行认证。
