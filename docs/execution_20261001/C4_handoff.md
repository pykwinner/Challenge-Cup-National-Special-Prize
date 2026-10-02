# C4 实验执行交接（进行中，2026-10-01）

**当前无重求解、来源审计待完成、现有结果非实测。** 用户最新要求全部正式数据真实有据；现有C1面积/H/C/得热、电价及空间含尚无精确来源的假设，12.0143%费用与29.1706%碳下降只作假设原型的程序/数学对照，不能称真实武汉工程效果。原全部历史manifest保持不变。

状态：优化与ASHP+EB基准的规划/8784h全年/2928h冬季/同服务**程序验证链**已完成，strict/status0、gap≤0.005及C6/数值/储热审计通过。当前按C0明确要求暂停新增重MILP，等待来源审计、新epoch及数据准入放行；只继续独立脚本和小测试。C0已解除共享核心写入冻结以修复checkpoint/season入口。代码epoch_01快照保留，后续不得以旧hash硬套新epoch。

已核对原设备：A/B 可逆 ASHP、A 电锅炉应急、A/B GSHP、A/B 太阳热、A/B PCM、A-B 管道及电网容量。基准拟限制为原有 ASHP + A 电锅炉（网络与电网保留），不增造设备或更改建筑/气象/舒适/初末态；限制的候选逐项登记。

必须保持：空间预处理返回 config 真正入模；代表日独立 boundary_group 且日内 UTC 连续；annual_scope 仅对完整年验证；投资只冻结一次并校验；固定回放成本加回规划年化资本。短窗和 relaxed 只能作明确范围的诊断。默认成本上限、清洁热份额和地源年比值不修改。

已接到并适配 C0 修复：冬季逐源 `ground_thermal_balance.enforce=False` 保留账本；冻结计划含 `fixed_investment_accounting`，年度 objective 与 LCOH 已含资本。runner 的 corrected_cost 校验年度资本与规划相等，先拆出运营再展示总额，不重复加资本；冬季不展示年度总成本。所有完整冬季/全年运行当前为未执行，不存在正式年度成功 manifest。

## 当前文件及命令

- `scripts/run_competition_case.py` / `src/heatplan/competition_runner.py`：prepare、smoke、plan、annual、season、frontier 六阶段；新 run_id 独占目录；进程锁防止此 runner 并发求解；重阶段必须显式 `--allow-heavy`（仅 C0 放行后使用）。
- `tests/test_competition_runner.py`：16 passed in 3.26s；JUnit `outputs/competition_20261001/C4/test_results_01.xml`；专属 basetemp `C4/pytest_01`。
- 主命令前置：`$env:PYTHONPATH='src'`，执行器 `D:\app\anaconda3\python.exe`。
- `python scripts/run_competition_case.py prepare --run-id preparation_01` 已执行；该早期准备包是 14 日旧选择，后续代表日已加入得热与舒适边界极值，正式应新建 prepare 目录，不复用早期包。
- `python scripts/run_competition_case.py smoke --run-id smoke_cold_exclusive_01 --hours 24 --time-limit 45 --smoke-weather cold` 已执行。

## 真 S3 短窗证据与明确失败原因

1. `smoke_cold_exclusive_01`：完整继承成本上限2元/kWh、清洁热份额、逐源地源年平衡，取全年最冷日本地完整24小时，exclusive。HiGHS status=2 不可行。1127变量、1162约束、170二元、4278非零；build 0.0164s，solve/extract 0.0167s。无 manifest，failure.json/run_record.json 保存原始报错。
2. `smoke_cold_cost_cap_diagnostic_01`：仅移除短窗的 `policy.maximum_levelized_heat_cost_yuan_per_kwh` 一条年度经济约束，所有其余约束和 exclusive 保留。该变动及原因写入 technology_changes.json，不能作为正式默认配置。获得 status=0、gap=0.00103657；1127变量、1161约束、170二元、3954非零；build 0.0168s，solve/extract 0.5436s，wall 0.7282s。数值审计、原始输入独立RC重算、实际冷热互斥、末态和储热审计均通过。最大RC残差1.78e-14℃。该证据仅说明24h年度资本/短窗热量口径造成已观察到的不可行：资本约297816元/年却仅24h热服务，删除该项后可行。不证明全年可行或允许放松正式成本上限。
3. 正式模型默认保留2元/kWh；尚未跑全年，不从0.54秒短例外推全年耗时。

## 代表时段进度

当前确定性选择17日/408小时、总权重8784小时：每月接近标准化日特征均值的真实日，另保留冷热极端、逐楼内/太阳得热和舒适界极值；每个代表日独立 boundary_group，日内UTC连续、原时钟与占用完整保留。`representative_coverage_01.csv` 已报告：原始冷热气象及得热/舒适极值通过，但部分RC稳态参考冷峰、爬坡及年度能量偏差需review。正在按这些具体原因补足代表日，未称为Benders，不以稳态参考代替实际RC送达量。

正式预定：先一个优化技术组合规划→冻结一次→同投资8784h全年→2928h独立固定初态冬季；再相同服务契约 ASHP+backup；最后5个ε点并如实报告实际非支配点。不同数据/源码/空间/服务契约身份会拒绝复用计划；每次成功最后写manifest，失败保留全部已产出证据。

## 最新安全边界交接（模型切换前）

- 新的 `smoke_cold_hot_policy_intact_01` 已成功：全年最冷日及最热日各24小时，两个独立固定初末态组，分别权重183（合计8784加权小时）。这是48h代表诊断，不是全年连续证据。**2元/kWh成本上限、0.9清洁比例、地源年比值全部保留，exclusive**；HiGHS status=0、gap=0。2231变量、2296约束、338二元、8504非零；build=0.0243s，solve/extract=0.4257s，wall=0.6088s。原始输入独立RC、数值、实际冷热无同时、储热及代码稳定性均通过；成功manifest已最后写入。该证据不包含随后新增的C6审计调用，不能倒填。
- `preparation_02` 为22日/528小时、加权8784小时。已根据17日失败报告加入RC稳态峰值和最大爬坡日期；气象/得热/舒适/稳态峰与爬坡已捕获，但办公室稳态制冷年能量偏差+16.4827%仍review。随后**仅因这项已记录偏差**加入工作日/周末分别选日与分配权重；该最后修订尚未运行prepare或测试，不能声称覆盖已通过。此为第二次有限覆盖修正，不是Benders。
- `pytest_02` / `test_results_02.xml`：16 passed in 3.36s，覆盖最后两项修订之前版本。恢复后新建 `pytest_03` 专属目录复测，不覆盖旧证据。
- 最新 runner 已只读接入 C6 `building_audit_identity` 与 `audit_building_trajectory`：在模型有效 `model.ts` 上冻结输入身份到 `C4/input_anchors/`，求解后审计实际RC轨迹、dispatch和summary，年度/冬季要求连续固定边界。核心summary暂未显式导出 building_heat/cooling_delivered，runner从相应真实metric系数×实际向量补出两字段，供C6独立重算比对；没有更改核心。
- 最新 C6 接入尚未实跑，恢复后首先用新的48h小例验证。runner自身原独立RC重算保留为交叉检查。
- 最新脚本仍不自动修改成本政策，正式重阶段都需 `--allow-heavy`；**C0尚未最终放行，不得因为本轮结束而自行启动重求解**。
- 模型设置：收到总控请求将本聊天切换到 `gpt-6.1-sol`、`ultra`、普通速度；总控随后观察当前turn_context仍为gpt-6-astra，要求本轮结束后明确恢复。C4没有可独立验证实际模型/服务档位的接口，未启用任何fast/priority，也不宣称切换已验证。

恢复顺序：复测代码→`prepare`新目录检查剩余coverage→最多60秒真实48h并验证C6→更新交接；收到C0冻结及重求解放行后，再正式plan/annual/season。现有短例耗时不能可靠外推全年用时，尚无全年性能数字。

## 恢复及正式放行后的当前状态

- 总控提供当前turn_context已核验gpt-6.1-sol/ultra的消息，并明确普通速度；C4未修改service_tier或启用fast/priority。实际服务档位仍没有本角色独立核验接口。
- `pytest_03` 16 passed in3.71s；随后加入比较阶段、失败status提取及与核心一致的独立审计字段，`pytest_04` 16 passed in3.90s，py_compile通过。
- `preparation_03` / `preparation_frozen_04`：**34个代表日、816物理小时、加权8784小时；全部coverage_status=pass**。此前17/22日偏差记录保留，最终工作日/周末分层和RC峰/爬坡补日已验证。
- `smoke_frozen_core_c6_03`：冻结核心重跑真实冷热两日48h严格小例，全部原政策保留；C6独立物理/dispatch/summary审计通过，status=0、gap=0。短例仍不代表连续年。
- `optimized_plan_01` 当前已构建：37559变量、38584约束、5714二元、143718非零；build=0.3764s。求解尚在进行，不能从这些结构数据推断最优性或年/季可行性。
- 正式调用：`python scripts/run_competition_case.py plan --run-id optimized_plan_01 --technology optimized --mode exclusive --time-limit 600 --mip-gap 0.005 --allow-heavy`。
- 成功后次序：`annual --run-id optimized_annual_01 --plan-dir outputs/competition_20261001/C4/optimized_plan_01 --time-limit 1800 --mip-gap 0.005 --allow-heavy`；随后 `season --run-id optimized_season_01` 同plan-dir，time-limit900。
- 基准同样使用technology=ashp_backup，单独plan/annual/season，再 `compare` 传四个完整结果目录；compare检查同服务、同源数据/空间/代码、每个技术年季同投资后才生成费用与碳比较。比较阶段尚未执行，因为完整输入结果未产生。
- 每个成功summary含 `independent_building_audit_passed/metrics/scope`，与核心字段一致；正式run manifest标明完整范围和strict/relaxed求解身份。成功最后写manifest；下游请只读取complete:true并核对哈希。

## 正式规划成功（首轮）

`optimized_plan_01`：816h/34代表日，全部默认政策保留，exclusive。status=0，gap=0.000343765728（0.03438%）；objective=803336.4870元/年，其中年化资本301782.8654、供热运营136136.9883、供热碳11053.9914、制冷运营323897.6067、制冷碳30465.0351，缺供和火用惩罚为0。总冷热排放345991.8877kg/年（代表期加权）；实际RC送热1085628.6541kWh、冷2470743.1348kWh。上述为代表期近似，全年数字必须由完整回放提供。

结构：37559变量、38584约束、5714二元、143718非零；build0.3764s、solve/extract413.4896s、export0.2435s、audit1.6945s、wall416.2044s。所有runner/C6/储热/数值/实际冷热互斥/代码稳定性检查通过。进程资源采样见C4/resource_snapshots.jsonl，观测峰值工作集最高734.375MB（采样，不宣称完整精确峰值）。

投资身份SHA256：`74099da0470559c021f70c78d74d626276066d338349caf198afb8942922639b`。A ASHP359.232kW、B ASHP285.948kW、A EB401.356kW、A GSHP316.069kW、B GSHP272.070kW、电网638.376kW；太阳热/PCM/新管道选择均0。没有强制将技术名称与既定结果相反地改写。计划费用通过fixed_investment_accounting进入年度目标和成本政策。

C0核验完整manifest及其SHA后允许C5只将此结果用作代表期规划证据。manifest中的`independent_external_certificate:pending_C6`指最终外部全系统证书；已保存的模块C6物理报告确实passed=true，两者范围须区分。无需为此描述修改已冻结源代码或成功manifest。

同投资年度已开始：`python scripts/run_competition_case.py annual --run-id optimized_annual_01 --technology optimized --mode exclusive --plan-dir outputs/competition_20261001/C4/optimized_plan_01 --time-limit 1800 --mip-gap 0.005 --allow-heavy`。尚无年度成功结论。

附备用 `C4/run_boundary_diagnostics.py`：7项声明式固定投资48h情景，H+20%、C±20%、室外±2℃、电价±20%；每个MILP≤60s、串行执行、保留政策。不作全年认证或概率解释。该备用脚本尚未执行，先完成主年季及公平基准。

## 完整服务年成功（同首轮投资）

`optimized_annual_01`：8784h真实连续UTC服务年，exact scope通过；exclusive，status=0，gap=0.001343681435（0.13437%）。同投资SHA为74099da0470559c021f70c78d74d626276066d338349caf198afb8942922639b，new_investment_zero=true；默认年度成本及逐源地源比值保留。全年总objective/现金费用均586978.7050元，其中已含资本301782.8654元，运营285195.8396元，**不能再加一次资本**。总冷热排放211634.0789kg；实际楼宇热958337.8244kWh、冷1327093.4729kWh。

独立审计：C6 passed=true，最大RC残差1.10e-13℃，欠温/过温/末态违约/冷热同时行数均0；数值、储热、代码稳定与所有runner门全部通过。404076变量、412881约束、61488二元、1361548非零；build3.6068s、solve/extract139.2229s、export2.5634s、audit16.7331s、wall162.4998s。已核对完整manifest与文件SHA。

代表日的逐日20℃末态恢复导致代表期冷热送达与连续年的动态量差异较大（代表冷247.07万kWh，连续年132.71万kWh）。coverage通过只覆盖输入与稳态参考统计；不能据此宣称动态年度能耗近似精度。正式费用/排放/冷热应优先引用完整回放；此差异本身不构成物理可行性失败，不无理由开启投资反馈。

同投资冬季已开始，初末态与C1固定20℃契约相同，独立于全年轨迹，未宣称冬季初态由年度承接。逐源地源年比值按核心明确延后，账本仍保留，完整年度已另验。

## 冬季入口失败与数学等价适配

- `optimized_season_01` 在求解前失败：prepare_heating_season_timeseries发现遗留user_behavior.scenario_multipliers缺少base，虽然全年/代表RC模型base系数已有隐式默认1。
- `optimized_season_02` 适配器在求解前失败：`_season_config`将thermal_inertia字典的high/low **null** 键当成活动循环状态；实际核心不为null项创建变量。两个目录均有failure.json、无manifest，尚未启动MILP。
- 新独立 `C4/run_season_case.py` 只显式添加base=1、清理null thermal_inertia键；所有真实楼宇fixed20℃、H/C/天气/舒适、设备、网络、资本、政策均保持。用原/适配后config各构建完整2928h数学模型，**变量名、上下界、整数性、目标、全部约束字典及上下界逐项精确相等** 后才允许求解；原比较模型的allow_cyclic=True仅绕过null键误报以建矩阵，实际求解用allow_cyclic=False。
- 等价证明保存于 `C4/input_adapter_proofs/optimized_season_03.json`，其SHA及明确变动登记在正式run的technology_changes.json。正式服务contract和同投资身份保持，因为该适配器未改变任何数学约束或实际RC服务。
- 命令：`python outputs/competition_20261001/C4/run_season_case.py --plan-dir outputs/competition_20261001/C4/optimized_plan_01 --run-id optimized_season_03 --time-limit 900`。未修改冻结CODE_FILES、未重新投资，也未静默放宽成本/清洁/服务政策。
- C0新增后续安排已收到：主链完成后再用独立脚本做真实词典序前沿，以及代表日cyclic边界规划→真实完整年fixed20℃回放的近似消融。该消融必须显式区分planning_boundary_contract与replay_service_contract，不能篡改旧manifest或绕身份校验；当前未启动。

## 完整冬季成功（同首轮投资）

`optimized_season_03`：完整2928h冬季，exclusive，status=0，gap=0.004755373126（0.47554%）。投资与年度同SHA74099da0470559c021f70c78d74d626276066d338349caf198afb8942922639b，new_investment_zero=true。冬季运营费用122974.2004元，碳86602.9685kg，实际楼宇热930728.4758kWh、冷711.2178kWh。冬季不含年度资本，不展示伪年度总成本；资本身份仍保存301782.8654元/年，完整年已一次核算。

独立C6与全部runner门通过：最大RC残差6.75e-14℃，欠温/过温/末态违约/冷热同时行数0。134699变量、137644约束、20496二元、392379非零；build1.1481s、solve/extract50.1158s、export0.5463s、audit5.1351s、wall57.1327s。完整manifest及SHA已核对。

当前基准命令：`python scripts/run_competition_case.py plan --run-id baseline_plan_01 --technology ashp_backup --mode exclusive --time-limit 600 --mip-gap 0.005 --allow-heavy`。其服务contract、源输入、空间身份均已与优化配置核对相同，未改变H/C、舒适、天气或初末态。投资和技术限制另存，待基准年季实际成功后才报告收益百分比。

## 公平基准与正式比较完成（安全点）

基准投资SHA：`10c09e5690b8d7657fe11869c577f1d051cfbe69a602c618022de8f577b1d5c3`。

| 正式结果 | 范围 | status / gap | 整轮秒数 | 成本口径 |
| --- | --- | --- | ---: | --- |
| baseline_plan_01 | 34日/816h代表规划 | 0 / 0 | 8.0403 | 加权规划objective875639.2474元，资本311097.0661元/年 |
| baseline_annual_01 | 8784h全年固定投资 | 0 / 0.000673025472 | 101.8527 | 全年总667129.6374元，已含资本311097.0661；运营356032.5713 |
| baseline_season_01 | 2928h冬季固定投资 | 0 / 0.000719876295 | 43.9525 | 冬季运营166721.0864元，不宣称年度总额 |

基准年度热987464.7766kWh、冷1373125.4038kWh、碳298794.1052kg；冬季热960922.1048kWh、冷706.2696kWh、碳136766.6969kg。基准年季使用同一基准投资，所有C6/数值/储热/真实RC/互斥/末态/零新增投资检查通过。冬季采用同一数学等价适配器并保存独立完整矩阵证明。

`technology_comparison_01` 已完成，manifest验证四源完整范围、服务contract、C1数据/空间/代码身份相同，以及每个技术年季同投资：

- 全年费用667129.6374→586978.7050元，减少80150.9323元，**12.0143%**。
- 全年运营排放298794.1052→211634.0789kg，减少87160.0264kg，**29.1706%**。
- 同服务contract SHA=`486495b521babdd44d9b7830d168c97b74f362ca78cd8a67a8515207587fd396`；热冷量随策略不同，不能强迫相同送达量代替相同舒适与服务。
- 上述是公开格点天气与合成H/C、得热、价格假设下的模型情景结果；模块审计通过，最终外部全系统政策/地源/身份证书仍由C0/C6核验。不是当地实测节能率。

C5可读取 `technology_comparison_01/technology_comparison.csv` 和 comparison.json作正式图表输入；所有六份规划/回放源manifest都存在并完成。`frozen_source_epoch_01/`保存43个Python源码文件及完整哈希manifest，含本链CODE_FILES原身份，可在后续代码epoch变动后复核旧结果。

下一步有授权但未执行：独立 `scripts/run_competition_frontier.py` 真正调用core.solve_lexicographic成本/碳端点→5ε点，报告两阶段状态/gap与实际非支配数；H/C/气温/价格48h边界诊断；代表日cyclic规划近似消融。后者C2支持cyclic，但当前runner independent_rc_audit只接受fixed且load_plan绑定同一service_contract，因此须新脚本明确记录planning_boundary_contract（cyclic）与replay_service_contract（C1 fixed20℃），并在新投资冻结后仍回放完整8784h/2928h固定服务，不能改旧identity强行复用。C0拟接入checkpoint证据补丁形成新epoch，先完成安全点交接再开始新正式前沿。

## 最新来源优先与代码准备

- C0确认六份规划/年季及comparison已完成，要求当前安全点后暂停新增重MILP。C3全参数出处审计、C1真实公开建筑RC数据搜集正在进行；C4不补造出处、不删除或重写历史数字。
- `scripts/run_competition_frontier.py` 已新建：直接调用core.solve_lexicographic真实两阶段端点，保存primary/secondary状态、gap、primary allowance、矩阵和C6物理结果；后续5个ε成本点用原runner审计输出并如实去重/支配过滤。未改旧CODE_FILES，**该新脚本尚未执行实验**。
- 前沿保留原全部政策/费用边界，含碳费/火用偏好的继承总objective会明确标注；代表期点不冒称全年前沿。若端点逆序/点失败，只报告实际完成点数和失败，不伪造5个非支配点。
- 按最新暂停要求，不运行其48h C1原型方法验证；仅做不启动工程MILP的小测试。正式运行仍待新epoch和数据准入。

## C4 证据等级更新与备用脚本已交付

按C0新授权，修改自有runner，不改既有结果：load_case及所有新run_record/summary/manifest/prepare/compare/frontier默认写入 `evidence_purpose=mathematical_scenario_validation`、`parameter_provenance_approved=false`、`empirical_validation=false`。返回的标签副本不能修改默认值；比较输入仅凭布尔或字面标签宣称实测不会提升输出等级。未添加可把结果升级为实证的CLI开关，也未自行设计/签发数据准入schema；真实来源准入由C0后续gate负责。

`formal_scope_semantics=temporal_extent_only_not_empirical_evidence`明确区分时间范围与经验真实性。新summary另给 `assumed_carbon_price_cost_yuan`（热/冷碳价费用之和）、`objective_excluding_carbon_price_yuan`（原目标减该项），旧cash字段保留并明确包含假设碳价；没有修改数学目标、成本政策或资本核算。

新增 `C4/run_cyclic_boundary_ablation.py`：只改代表期楼宇初末边界为cyclic，移除fixed末态字段但保留H/C、得热、舒适及所有非边界物理；独立记录planning_boundary_contract与C1 fixed20℃ replay_service_contract。新投资用distinct `ablation_plan.json`保存，不可冒充普通同边界fixed_plan.json；恢复真实fixed服务后完整8784h回放，投资不被更改。若未来采纳为推荐，仍需该投资完整冬季认证。**脚本当前只完成代码与单元测试，没有新求解结果**。

`scripts/run_competition_frontier.py`直接调用core.solve_lexicographic，两个真实阶段状态/gap/主目标allowance写入端点summary。5ε点都保留原政策，原型证据等级为数学情景；真实前沿、边界诊断与cyclic消融均未运行，待C0新epoch与数据准入。

最终轻测试：`python -m pytest tests/test_competition_runner.py -q -p no:cacheprovider --basetemp=outputs/competition_20261001/C4/pytest_evidence_epoch_05 --junitxml=outputs/competition_20261001/C4/test_results_evidence_epoch_05.xml` → **22 passed in3.76s**。新增测试为证据等级不随标签升级、费用显示不改原目标、cyclic仅改边界、实际调用词典序API且失败留存无完成manifest、旧投资不能跨代码epoch按字面复用。词典序API测试使用故障桩，不启动工程MILP；C1准备仍是只读输入检查。

失败保留：evidence_epoch_02为20pass/1fail（新端点输入锚目录未递归创建）；epoch_03为20pass/1fail（嵌套单元临时路径超过Windows路径限制）。分别用parents=True及隔离短fixture OUTPUT修复；epoch_04为21pass，补代码epoch负例后epoch_05为22pass。旧JUnit与失败目录均未覆盖。

自有文件新SHA登记于 `C4/runner_evidence_update_02.json`；该文件明确C0依赖尚未重新冻结、当前没有新MILP/实测认证。主要runner SHA=`b9ef18519989e0fcb7b012c0b666f6336a9089735096b513fa940ac439b5299f`（与JSON实际哈希一致；C0后续整合前可复算）。旧epoch_01源码快照和六份历史成功manifest未修改。
