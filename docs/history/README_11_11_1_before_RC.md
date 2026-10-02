# CP-202611 城镇供热源荷匹配与规划寻优（V11.11.1）

这是一个可直接运行的 Python 基线工程，用于验证“源-网-荷-储”联合规划与逐时运行逻辑。核心模型是混合整数线性规划（MILP），默认调用 `scipy.optimize.milp`，因此不依赖商业求解器。

当前代码版本为 `11.11.1`（2026-09-30）。本次修复净成本CVaR、单行循环状态、联合计量投资和费用漏项、残值及财务终点；强化年度缓存身份、风险投资差异比较、数值及储热轨迹验收；修复MPC漏时并明确状态支持范围；新增独立跨年供暖季入口与证书。详细变更、兼容性和可运行的四小时演示见 [11.11.1 完善说明与使用指南](docs/11.11.1_完善说明与使用指南.md)。本轮没有重跑三城市全年或真实供暖季，因此历史输出和交付ZIP不是本补丁的重新认证结果。

以下是11.11.0历史版本的性能与政策说明，数值沿用原材料：机组启停由“三类整数变量+单侧连接”收紧为“开机状态二元+启停连续辅助量+三行精确转移凸包”，固原规划与全年回放的机组组合整数变量减少66.7%，同时消除用虚假启停放松爬坡约束的可能；稀疏约束矩阵改为直接CSR/CSC编译并缓存求解器静态输入，新增矩阵准备时间、节点数、对偶界和启停残差诊断。以重新优化的固原方案计，规划加两个极端年度回放的同批次实测总耗时由约930秒降至约442秒，两个年度仍为零失供并通过内部验收。政策画像同步纳入《美丽中国建设“十五五”规划》，现覆盖3份中央正式文件、19条场景化条款，新增清洁取暖运行保障、气候韧性和项目碳核算证据门。

## 目前实现

- 多节点、两级热能品位（高温/低温）热平衡；
- 工业余热、燃气锅炉、空气源/余热源热泵等热源；
- 空气源热泵 COP、可用容量随严寒温度下降，并考虑低温停机与除霜折减；
- 热源扩容、候选管径、储热容量与逐时调度联合优化；
- 管网热损失与循环泵耗随室外温度变化，储热支持循环或固定季节首末 SOC；
- 候选环网双向流动与同一时段方向互斥；
- 可配置设备启停、最小出力、启停成本和爬坡约束；启停辅助量采用精确状态转移凸包，仅保留开机状态二元变量；
- 约束矩阵直接编译为稀疏CSR/CSC并安全缓存，重复求解复用矩阵、变量边界和整数标记；
- 求解结果输出MIP节点数、对偶界、矩阵准备时间、启停次数、转移残差和指标分数性；
- 响应窗口内的可平移热负荷、建筑热惯性虚拟储热与舒适度约束；
- 按设计温度校核可靠容量裕度，并设置设备容量信用与最不利可用率；
- 温位不匹配的火用损失惩罚；
- epsilon-constraint 成本-碳排 Pareto 前沿；
- 固定投资方案下的“降温 + 负荷增长 + 余热衰减”复合寒潮压力测试；
- 用户末端高温转低温改造的二进制投资决策；
- 热泵、电锅炉与循环泵共享的配电接入容量投资约束；
- 储热按“功率—能量—寒潮持续时间”折算可靠容量；
- 关键热源 N−1 容量校核与关键管线 N−1 场景网络流校核；
- 冻结投资后的常态、复合寒潮、热源故障与管线故障四场景韧性测试；
- 多场景共享投资、场景独立运行与场景独立储热/需求响应边界；
- 场景概率、代表周期权重和逐场景缺供率硬约束；
- 期望成本 + CVaR 尾部运行成本的风险厌恶规划；
- 2026—2035 分阶段滚动规划、政策指标约束和既有资产结转；
- 清洁/余热/化石供热份额、碳强度、改造覆盖率、投资与补贴预算约束；
- 计入历史资产年化成本的供热成本上限与补贴后社会—企业成本分账；
- 真正的联合多阶段 MILP：一次求解 2026—2035 建设与运行，并处理建设期、寿命、退役和残值；
- 六道工程准入门、证据登记和不合格候选资产自动剔除；
- 项目/股权现金流、NPV、IRR、回收期、DSCR 与盈亏平衡热价；
- Darcy-Weisbach 水力与稳态热损失后校核；
- 连续全年时序、季节储热边界和 BTES 十年筛查；
- 规划方案冻结后的 MPC 滚动调度与 Monte Carlo 样本外回放；MPC当前支持连续单场景热源/管网/储热，不支持的跨窗动态模块会明确拒绝；
- 强制保留最低温、最大热负荷、最低余热和最大爬坡日的代表日生成器；
- 成本、碳排、余热利用率、失供率、火用效率、温度偏差度时等指标输出；
- 典型日聚类、压力场景生成、权重扫描的扩展接口。
- 夏热冬冷地区供暖—供冷逐时联合规划，可逆热泵共用容量、零冷失供、逐时EER与配电耦合；
- 大型地源热泵年度吸热—释热比约束，以及联合系统、监管供热和透明分摊三套成本口径；
- 自定义电价时序列，区分居民清洁供暖优惠电价与一般总表代理；
- 现金口径平准供热成本完整计入生物质采购、固定运维和启停费用。
- 清洁热、余热、非化石热和化石热四套独立政策账本；
- 经审计基线驱动的供热碳强度和建筑面积直接化石排放相对减排约束；
- 国家宏观目标、条件代理、项目KPI、定性映射和实现缺口五级政策评价；
- 政策来源、适用期、模型字段、实际值、差距和口径限制的CSV/JSON审计输出。
- 2026年官方文件来源域名、发布日期、文件号、场景适用性和证据就绪度校验；
- 《美丽中国建设“十五五”规划》的清洁取暖运行保障、极端天气气候韧性和项目碳核算映射；
- 政策模型能力、证据待补、战略参考、不适用和实施缺口的分层汇总与行动清单；
- ≤10蒸吨/小时存量燃煤锅炉逐台容量清单、完整退役能力和强制退役可选硬约束。
- 逐气象年独立聚类、逐年能量校准和等概率/近期加权气象年场景束；
- 投资变量跨年度共享、运行变量按年度独立的两阶段随机规划；
- 期望年成本加CVaR尾部成本目标，显式惩罚最昂贵气象年；
- `base`政策过滤器向`weather_year_*`场景的受控映射与映射审计；
- V11.6平均年快速预筛—V11.8五年度风险厌恶定稿—V11.9极端年逐时认证的三级求解体系；
- 固定投资方案检查点保存、加载和跨进程复用，避免多年度回放前重复求解规划层；
- 检查点文件SHA-256、源配置指纹、代表时序指纹、场景集合和冻结投资语义的多层校验；
- 七个极端年度验收证书的哈希汇总、累计验证小时、最大求解间隙和零失供总体验收。
- CVaR风险项延迟到全部场景运行成本与本地能源抵扣入账后构建，统一优化与报告口径；
- `0—0.10`四档风险权重的断点续跑扫描、逐档检查点和60个年度容量准入；
- 按binary、fraction、技术选项和kW/kWh/kg分别识别投资结构与容量变化，避免用容量容差忽略离散选择；
- 风险溢价/权重与经验CVaR的自动一致性门禁，以及12个案例文件哈希总验收。
- 节点级供热计量覆盖率改造、线性节热响应、计量覆盖率政策约束；
- 可配置计量热价、固定热费、居民户数和平均热费可负担性上限；
- 代表时段先投资、连续时序冻结投资回放，并自动审计全部候选投资余量是否归零。
- 求解前检查时间戳、重复/缺失时段、配置步长、空值、无穷值、负荷负值和全部模型引用列；
- 对代表时段与连续年度的年能量、峰值、最低温、最低资源可用率和最大爬坡进行逐列偏差审计；
- 仅执行数据预检、不启动MILP的快速入口，以及短时序必须显式声明的软件测试保护门。
- 基于清单的三场景隔离全年回放、输入/参数指纹与相同成功结果断点续跑；
- 连续年度、代表时段、求解状态、投资冻结、可靠容量、失供率、储热边界、地源热平衡和清洁热约束的机器可读验收证书；
- 储热调度显式输出`boundary_group`，后检查按“时段结束状态”语义重建循环/固定边界残差。

代码只是一套“可信基线”，不是最终竞赛模型。正式参赛时应补充官方题面数据、城市 GIS、真实设备参数和不确定性边界。

## 快速运行

```bash
cd north_heat_challenge
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

PYTHONPATH=src python -m heatplan.cli \
  --config data/example_city.yaml \
  --timeseries data/example_timeseries.csv \
  --output outputs/baseline
```

运行后生成：

- `summary.json`：总成本、碳排、火用效率、余热占比、求解状态；
- `investments.csv`：热源、储热、管径投资方案；
- `dispatch.csv`：各时段供热、储热、管网流量与需求响应；
- `dispatch.png`：供需与储热调度图；
- `network.png`：候选管网方案示意图。

运行测试：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

V11.11全量回归结果为`87 passed`。

复测三个公共代表日模型的建模耗时与结构规模：

```bash
python scripts/benchmark_model_build.py --repeat 5 --warmup 1
python scripts/summarize_v11_11_performance.py \
  --output outputs/v11_11_performance
```

`HeatPlanningModel` 会在构造时深拷贝 `config`，隔离调用方后续对原配置的修改；同时将求解所用的时序列复制为只读 NumPy 快照。因此，实例创建后再修改原始 `config`、原始 DataFrame 或用于报告的 `model.ts`，均不会改变该实例已冻结的优化输入。如需调整输入，请重新创建模型实例。

一键运行 Pareto 前沿、消融实验、北方复合寒潮与 N−1 韧性压力测试：

```bash
PYTHONPATH=src python -m heatplan.experiment_cli \
  --config data/example_city.yaml \
  --timeseries data/example_timeseries.csv \
  --output outputs/experiments \
  --pareto-points 7
```

运行 V5 相关场景与 CVaR 联合规划：

```bash
PYTHONPATH=src python -m heatplan.scenario_cli \
  --config data/example_city.yaml \
  --timeseries data/example_timeseries.csv \
  --scenario-specs data/scenario1_scenario_specs.yaml \
  --output outputs/v5_scenario
```

该命令会输出场景数据包、风险中性/风险厌恶投资比较、逐场景指标，以及 V4 冻结投资在新场景下的规划外回放结果。

运行 V6 政策约束与分阶段实施路径：

```bash
PYTHONPATH=src python -m heatplan.policy_cli \
  --config data/example_city.yaml \
  --timeseries data/example_timeseries.csv \
  --scenario-specs data/example_scenario_specs.yaml \
  --policy-pathway data/example_policy_pathway.yaml \
  --output outputs/v6_policy_pathway
```

示例路径中的份额、预算和补贴比例是用于展示模型能力的情景参数，不代表任何城市的正式政策目标；正式参赛时须用赛题城市规划、审计数据和主管部门口径校准。

运行 V7 联合多阶段规划（取代仅逐阶段滚动的近视决策）：

```bash
PYTHONPATH=src python -m heatplan.multistage_cli \
  --config data/example_city.yaml \
  --timeseries data/example_timeseries.csv \
  --scenario-specs data/example_scenario_specs.yaml \
  --policy-pathway data/example_policy_pathway.yaml \
  --feasibility data/example_feasibility.yaml \
  --output outputs/v7_joint_multistage
```

运行工程复核、MPC、样本外验证和 BTES 多年筛查：

```bash
PYTHONPATH=src python -m heatplan.operations_cli \
  --config data/example_city.yaml \
  --timeseries data/example_timeseries.csv \
  --feasibility data/example_feasibility.yaml \
  --btes-spec data/example_btes.yaml \
  --output outputs/v7_operations
```

准备连续全年时序并校核季节储热：

```bash
PYTHONPATH=src python -m heatplan.chronology_cli \
  --config data/example_city.yaml \
  --timeseries your_hourly_year.csv \
  --output outputs/v7_chronology \
  --solve
```

先用代表时段规划，再冻结全部投资执行 8760 h 连续回放：

```bash
PYTHONPATH=src python -m heatplan.annual_replay_cli \
  --config data/example_city.yaml \
  --representative-timeseries data/example_timeseries.csv \
  --annual-timeseries your_hourly_year.csv \
  --output outputs/v11_annual_replay \
  --planning-time-limit 180 \
  --replay-time-limit 1800
```

建议先只做预检，不启动大型求解：

```bash
PYTHONPATH=src python -m heatplan.annual_replay_cli \
  --config data/example_city.yaml \
  --representative-timeseries data/example_timeseries.csv \
  --annual-timeseries your_hourly_year.csv \
  --output outputs/v11_3_preflight \
  --preflight-only \
  --estimate-model-size \
  --pilot-hours 168
```

预检输出`chronology_quality_audit.json`、`representative_coverage_audit.csv/png`、`representative_coverage_recommendations.csv`、`annual_replay_model_size_estimate.json`和`annual_replay_preflight_summary.json`。默认要求连续8760/8784小时；短时序软件测试必须显式增加`--allow-short-horizon`。修正建议仅用于诊断，程序不会静默改写代表日权重。

批量预检三类8760小时数据：

```bash
PYTHONPATH=src python -m heatplan.annual_preflight_batch_cli \
  --manifest data/public_scenario_inputs/three_scenario_annual_preflight_manifest.yaml \
  --output outputs/v11_4_batch_preflight_baseline \
  --pilot-hours 168
```

生成逐负荷极端日、最坏COP锚点和受约束权重候选，并执行投资稳定性比较：

```bash
PYTHONPATH=src python scripts/build_v11_4_representative_days.py
PYTHONPATH=src python scripts/run_v11_4_investment_stability.py \
  --mip-gap 0.01 --time-limit 180
```

批量运行三类场景的正式固定投资全年回放；相同输入和参数的成功结果默认断点续跑：

```bash
PYTHONPATH=src python -m heatplan.annual_replay_batch_cli \
  --manifest data/public_scenario_inputs/three_scenario_annual_replay_v11_5_manifest.yaml \
  --output outputs/v11_5_three_scenario_annual_replay \
  --planning-time-limit 180 \
  --replay-time-limit 600 \
  --planning-mip-gap 0.005 \
  --replay-mip-gap 0.01
```

对既有成功回放重建储热边界后检查与验收证书，无需重新求解：

```bash
PYTHONPATH=src python -m heatplan.annual_replay_certify_cli \
  --output outputs/v11_5_three_scenario_annual_replay/scenario1_urumqi
```

V11.5实测结果位于`outputs/v11_5_three_scenario_annual_replay/`：S1为449,335个变量、求解83.74秒；S2为423,898个变量、求解45.89秒；S3为313,579个变量、求解55.33秒。三者均为连续8760小时、状态0、零热失供并通过全部适用验收门。

构建五年联合代表日、执行同一投资方案跨年审计并正式回放自动选出的极端年份：

```powershell
$env:PYTHONPATH='src'
python scripts\build_v11_6_multiyear_representative_days.py --clusters 8
python -m heatplan.multiyear_readiness_cli `
  --manifest outputs\v11_6_multiyear_representative_days\robust_multiyear_manifest.yaml `
  --output outputs\v11_6_multiyear_readiness_robust
python -m heatplan.multiyear_replay_cli `
  --manifest outputs\v11_6_multiyear_representative_days\robust_multiyear_manifest.yaml `
  --output outputs\v11_6_multiyear_formal_replay
```

V11.6结果位于`outputs/v11_6_multiyear_representative_days/`、`outputs/v11_6_multiyear_readiness_robust/`和`outputs/v11_6_multiyear_formal_replay/`。三场景五年容量审计均为5/5通过；7个极端年完整回放全部取得验收证书。方法、数值结果和现实参数边界见`docs/V11.6多气象年鲁棒规划与极端年复算说明_20260720.md`。

正式回放还会输出投资冻结审计、代表时段—全年KPI差异、按边界组计算的储热状态方程残差、完整逐时调度、`model_execution_profile.json`和`annual_replay_acceptance_certificate.json`。只有`chronology_ready_for_formal_replay=true`时，数据才通过年度时序门；`representative_coverage_requires_review=true`表示代表时段仍需复核，但不会阻止回放用于量化偏差。

供热计量与热费模块的可运行示例：

```bash
PYTHONPATH=src python -m heatplan.cli \
  --config data/v11_heat_metering_demo.yaml \
  --timeseries data/example_timeseries.csv \
  --output outputs/v11_1_heat_metering_demo
```

示例中的覆盖率、节热率、户数和热价均为功能演示假设，不能直接作为城市政策参数。

第一类场景（既有大型集中供热系统低碳重构）专用运行：

```bash
PYTHONPATH=src python -m heatplan.multistage_cli \
  --config data/scenario1_existing_network.yaml \
  --timeseries data/scenario1_existing_network_timeseries.csv \
  --scenario-specs data/example_scenario_specs.yaml \
  --policy-pathway data/scenario1_policy_pathway.yaml \
  --feasibility data/example_feasibility.yaml \
  --output outputs/scenario1_v9_joint
```

V9配置增加既有骨干管网节能改造、CHP可变热电运行域、化石热源内生退役与拆除费用、
最低备用容量、常规低碳目标与应急保供分离、可再生电力转热，以及工业余热完全中断和
长持续寒潮压力测试。完整解释见
[docs/V9第一类选题内生退役与热电解耦模型说明书.md](docs/V9第一类选题内生退役与热电解耦模型说明书.md)。

三类场景公用总框架一键运行：

```bash
PYTHONPATH=src python -m heatplan.unified_cli \
  --manifest data/three_scenario_manifest.yaml \
  --output outputs/unified_v10 \
  --time-limit 180 \
  --mip-gap 0.01
```

`--mip-gap 0.01`表示按1%相对间隙作为规划级停止准则，并在`run_configuration.json`中留痕；若比赛要求更高精度，应缩小该值并相应增加时间上限。省略时使用各场景配置中的`planning.mip_gap`。

统一框架在第一类能力之上增加：

- 第二类：生物质热值—锅炉效率—采购—运输—逐时到货—库存—年度资源上限，以及可再生电源/电池/备用电源组成的区域微网逐时电力平衡、孤网运行和电池SOC；
- 第三类：湿度耦合除霜COP、占用日程与供暖回落、占用加权用户行为、有人/无人分级舒适带、建筑热惯性可靠削峰及多能共享资产成本分摊；
- 公用入口：一份manifest依次运行三类专用配置并输出统一KPI对比表。

## 推荐迭代顺序

1. 用题面数据替换示例 YAML/CSV，跑通确定性 MILP。
2. 加入三种场景配置和对照组，完成消融实验。
3. 用保序典型日压缩全年 8760 小时，并保留跨季节 SOC 连续性。
4. 加入预算不确定集或 C&CG，扫描鲁棒预算 `Gamma`。
5. 用已实现的 epsilon-constraint 得到成本-碳排 Pareto 前沿。
6. 最后再接负荷预测和 MPC，不要让机器学习替代规划主模型。

## 数据约定

配置文件中的功率单位为 kW，能量单位为 kWh，温度为摄氏度，费用为人民币元，碳排为 kgCO2。示例时间步长为 1 小时；`representative_day_weight` 表示该典型日代表的年运行天数。

北方增强版的完整公式、参数、寒潮实验、优势对比和答辩表述见
[docs/北方城镇供热源荷匹配与规划寻优模型说明书.md](docs/北方城镇供热源荷匹配与规划寻优模型说明书.md)，
基于更多文献和工程问题形成的 V4 深度报告见
[docs/北方城镇供热深度背景调研与V4韧性转型模型报告.md](docs/北方城镇供热深度背景调研与V4韧性转型模型报告.md)，
多场景共享投资、CVaR、场景边界和极端代表日的 V5 详细说明见
[docs/北方城镇供热V5多场景韧性规划模型详细说明书.md](docs/北方城镇供热V5多场景韧性规划模型详细说明书.md)，
政策约束、分阶段投资、补贴分账和工程可行性准入的 V6 说明见
[docs/北方城镇供热V6政策约束与工程可行性模型说明书.md](docs/北方城镇供热V6政策约束与工程可行性模型说明书.md)，
可直接用于论文“模型建立”章节的严谨公式见
[docs/北方供热优化数学模型与公式附录.md](docs/北方供热优化数学模型与公式附录.md)，
简版建模路线见 [docs/建模与创新方案.md](docs/建模与创新方案.md)。

## 最新阶段成果

- V11.11启停凸包、稀疏矩阵编译、真实提速与中央政策协同升级：`docs/V11.11求解性能与中央政策协同升级说明_20260720.md`
- V11.10净成本CVaR修正、风险权重稳定性扫描与修正方案年度认证：`docs/V11.10净成本CVaR修正与风险权重稳定性说明_20260720.md`
- V11.9固定投资检查点、7个极端年度完整回放与正式认证：`docs/V11.9固定投资检查点与极端年正式回放说明_20260720.md`
- V11.8多气象年两阶段随机规划与CVaR尾部风险：`docs/V11.8多气象年两阶段CVaR规划说明_20260720.md`
- V11.7官方时政核验、场景化政策治理与小燃煤锅炉资产门：`docs/V11.7官方时政对齐与政策约束升级说明_20260720.md`
- V11.6五气象年鲁棒规划与极端年复算：`docs/V11.6多气象年鲁棒规划与极端年复算说明_20260720.md`
- V11.4代表日修复、可靠容量预审与武汉全年闭环：[docs/V11.4代表时段修复与全年回放闭环说明_20260720.md](docs/V11.4代表时段修复与全年回放闭环说明_20260720.md)
- V11.3全年模型规模先验、运行画像与安全修正建议：[docs/V11.3全年模型规模预估与运行画像说明_20260720.md](docs/V11.3全年模型规模预估与运行画像说明_20260720.md)
- V11.2全年数据准入与代表时段覆盖审计：[docs/V11.2全年数据准入与代表时段偏差审计说明_20260720.md](docs/V11.2全年数据准入与代表时段偏差审计说明_20260720.md)
- V11.1供热计量、按热收费与固定投资连续回放：[docs/V11.1供热计量与全年回放升级说明_20260720.md](docs/V11.1供热计量与全年回放升级说明_20260720.md)
- V11十五五战略对齐、政策画像、新KPI和三场景回归：[docs/V11十五五战略对齐与技术升级说明_20260720.md](docs/V11十五五战略对齐与技术升级说明_20260720.md)
- 阶段总结：[docs/阶段性优化总结_20260712.md](docs/阶段性优化总结_20260712.md)
- 现实证据校准与全年多能服务总结：[docs/阶段性优化总结_20260712_现实校准.md](docs/阶段性优化总结_20260712_现实校准.md)
- 工程稳健性、财务加速、建模加速与三场景回归：[docs/阶段性优化总结_20260715_工程稳健性.md](docs/阶段性优化总结_20260715_工程稳健性.md)
- 10组答辩级PNG/SVG图表：`outputs/stage_summary_figures/`
- 武汉模型纠偏、补贴与多能共享敏感性：`outputs/stage_analysis/`
- 扩展公开数据：67个来源、312条量化事实，见`data/public_sources/`和`data/public_source_extensions/`；
- 9组现实校准结果：`outputs/reality_calibration/`；
- 7组现实校准PNG/SVG图：`outputs/reality_calibration_figures/`。

V11.4最终回归为`61 passed`。三类公共全年数据均通过8760小时准入；原武汉9日方案被构模前可靠容量审计识别为配电缺口−0.917 kW，V11.4的14日方案将爬坡捕获率提高到100%、最大热负荷年能量偏差降至0.020%，并以313,579变量、26,280整数变量完成8760小时固定投资回放：规划与回放状态均为0，热/冷失供率均为0，储热首末SOC漂移为0，回放求解44.11 s。

复现阶段分析和图表：

```bash
PYTHONPATH=src python scripts/run_stage_sensitivity.py --time-limit 90
PYTHONPATH=src python scripts/make_stage_summary_figures.py
PYTHONPATH=src python scripts/run_reality_calibrations.py --compile-existing
PYTHONPATH=src python scripts/make_reality_calibration_figures.py
PYTHONPATH=src python scripts/run_v11_policy_alignment.py \
  --output outputs/v11_11_policy_alignment --as-of-date 2026-07-20
PYTHONPATH=src python scripts/build_v11_8_multiyear_stochastic_bundle.py
PYTHONPATH=src python -m heatplan.multiyear_readiness_cli \
  --manifest outputs/v11_8_multiyear_stochastic_bundle/stochastic_multiyear_manifest.yaml \
  --output outputs/v11_8_multiyear_stochastic_readiness \
  --planning-time-limit 600 --planning-mip-gap 0.005
PYTHONPATH=src python scripts/compare_v11_8_stochastic_planning.py
PYTHONPATH=src python scripts/index_fixed_plan_checkpoints.py \
  --manifest outputs/v11_8_multiyear_stochastic_bundle/stochastic_multiyear_manifest.yaml \
  --checkpoint-root outputs/v11_8_multiyear_stochastic_readiness
PYTHONPATH=src python -m heatplan.multiyear_replay_cli \
  --manifest outputs/v11_8_multiyear_stochastic_bundle/stochastic_multiyear_manifest.yaml \
  --checkpoint-root outputs/v11_8_multiyear_stochastic_readiness \
  --output outputs/v11_9_multiyear_formal_replay \
  --replay-time-limit 1800 --replay-mip-gap 0.005
PYTHONPATH=src python scripts/plot_v11_9_formal_replay.py \
  --summary outputs/v11_9_multiyear_formal_replay/multiyear_replay_summary.csv \
  --output outputs/v11_9_multiyear_formal_replay/figures
PYTHONPATH=src python -m heatplan.risk_scan_cli \
  --manifest outputs/v11_8_multiyear_stochastic_bundle/stochastic_multiyear_manifest.yaml \
  --output outputs/v11_10_cvar_weight_scan \
  --weights 0 0.02 0.05 0.10 --cvar-alpha 0.8 \
  --time-limit 900 --mip-gap 0.005
PYTHONPATH=src python -m heatplan.multiyear_replay_cli \
  --manifest outputs/v11_8_multiyear_stochastic_bundle/stochastic_multiyear_manifest.yaml \
  --checkpoint-root outputs/v11_10_cvar_weight_scan \
  --checkpoint-case-subdir weight_0p05 \
  --output outputs/v11_10_corrected_cvar_formal_replay \
  --replay-time-limit 1800 --replay-mip-gap 0.005
PYTHONPATH=src python scripts/plot_v11_10_risk_scan.py \
  --scan-output outputs/v11_10_cvar_weight_scan \
  --old-planning-results outputs/v11_8_multiyear_stochastic_readiness \
  --old-replay-summary outputs/v11_9_multiyear_formal_replay/multiyear_replay_summary.csv \
  --new-replay-summary outputs/v11_10_corrected_cvar_formal_replay/multiyear_replay_summary.csv \
  --output outputs/v11_10_cvar_weight_scan/figures
```

单独生成政策对齐审计表：

```bash
PYTHONPATH=src python -m heatplan.policy_alignment_cli \
  --summary outputs/v11_11_policy_alignment/scenario1_urumqi/summary.json \
  --profile data/policy_profiles/national_15th_fyp_reference_2026.yaml \
  --scenario-id scenario1_existing_network \
  --as-of-date 2026-07-20 \
  --output outputs/v11_11_policy_alignment/scenario1_urumqi/audit
```
