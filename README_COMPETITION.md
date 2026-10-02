# 评分标准对应的本轮运行入口

本轮在原 11.11.1 工程上增加实际室温驱动的建筑冷热模型、空间候选、独立物理审计和可追溯的正式实验链。历史结果保持原来的版本和范围，不自动作为本轮新模型的结果。

当前实施状态与七项证据见 [评分逐项验收与算法优势](docs/execution_20261001/评分逐项验收与算法优势.md)，代码变更见 [总控记录](docs/execution_20261001/C0_handoff.md)。最终推荐方案必须以总控选定的成功 manifest 为依据。

## 准备环境

本轮已验证 Python 3.12.7、NumPy 1.26.4、SciPy 1.15.3 等组合。依赖均为公开 Python 包，优化器使用 SciPy 内置 HiGHS，不需要商业求解器许可。`requirements-validated.txt` 固定了实际验证版本；其它操作系统的运行不宣称已经测试。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-validated.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-external-validation.txt
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp .test_run_unique
```

每次测试使用新的临时目录名，避免 Windows 既有临时目录权限造成清理失败。已有 Conda 环境也可使用相同 Python 命令，不要求原机器的绝对路径。Linux/macOS 可用 `.venv/bin/python`，并将环境变量设为 `PYTHONPATH=src`；这些是移植用法，非本轮跨平台实测声明。

## 数据与单位

数据入口是 `data/competition_20261001/wuhan_rc_candidate.yaml`，它继承既有 S3 设备和网络，不重建另一套不相关工程。主数据为公开武汉格点气象和透明合成建筑：4 个聚合楼宇、3 类原型，总面积 44000 平方米，不是现场辨识参数。

**真实性准入尚未通过。** 上述原型的面积、H/C、得热、占用以及部分价格、设备和空间参数仍缺少精确出处；已有求解只用作程序/数学验证，不作为武汉真实工程实证。字段级清查见 `docs/execution_20261001/data_truth_audit/`。公开气象、标准/统计参数、设计设定和真实求解输出分别标记；“已声明假设”不等于“真实有据”。

| 对象 | 定义 |
| --- | --- |
| 主服务年 | 北京时间 2023 年 7 月 1 日至 2024 年 7 月 1 日，8784 个连续小时 |
| 供暖季 | 项目假定 2023 年 11 月 15 日至 2024 年 3 月 16 日半开区间，2928 小时 |
| 内部时间 | 带时区 UTC；占用、价格与服务期按北京时间生成后映射 |
| 设备与服务功率 | 区间平均 kW；乘小时长度才得到 kWh |
| 室温和 SOC | 区间末状态，分别为摄氏度与 kWh |
| H 和 C | 每个聚合楼宇的 kW/K 和 kWh/K，不再乘一次建筑面积 |
| 太阳辐射 | 新包已修正原始字段误名对应的 Wh 到 kWh 转换；见数据 README |
| RC 参考负荷 | 仅用于容量/覆盖检查，不能与实际送达热量相加 |

实际天气、单位、来源、假设和逐文件哈希见数据包 README、source_inventory、clock_verification 和 manifest。数据 manifest 只证明数据包完整，不证明优化方案可行。

## 原型程序验证的执行顺序

以下命令对应尚未完成参数来源审核的原型，只用于内部数学/程序验证，不是已批准的真实工程实验。先运行准备阶段。每次使用新的 `--run-id`；程序拒绝覆盖已有结果，写失败记录，成功最后生成 manifest。当前总控已暂停新增原型重求解，优先完成真实来源检验。

```powershell
python scripts/run_competition_case.py prepare --run-id prepare_new
python scripts/run_competition_case.py plan --run-id optimized_plan_new --technology optimized --mode exclusive --time-limit 600 --mip-gap 0.005 --allow-heavy
python scripts/run_competition_case.py annual --run-id optimized_annual_new --technology optimized --mode exclusive --plan-dir outputs/competition_20261001/C4/optimized_plan_new --time-limit 1800 --mip-gap 0.005 --allow-heavy
python scripts/run_competition_case.py season --run-id optimized_season_new --technology optimized --mode exclusive --plan-dir outputs/competition_20261001/C4/optimized_plan_new --time-limit 900 --mip-gap 0.005 --allow-heavy
```

`--allow-heavy` 是本机重求解队列的显式开关，不是放松任何物理约束。程序内有进程锁；不要同时启动多个年度 MILP。先核对实际脚本的 `--help`；后续经审计的执行批次可使用更长求解预算，预算变化必须写入记录。

投资规划采用有权重的完整真实代表日，各代表日状态边界独立；这不是连续一年。年度回放固定全部已选投资，保留初末状态、冷热互斥与年度地源比值。独立冬季使用同一投资，但按明确固定初态开始；它不声称承接年度某日状态，年度约束的延后清单随结果保存。

基准使用相同建筑、天气、舒适范围、初末态和服务时间，仅改变可选技术。不能把降低服务质量得到的低成本解释为算法优势。变化 H/C、温度、得热或价格时，必须记录真实生效的配置和时序字段；旧负荷模板乘数不改变 RC 服务。

## 结果的判断方法

依次核对运行完成标记和哈希、完整时间范围、投资冻结、求解状态与 MIP gap、数值残差、独立室温/冷热审计、储热状态与互斥、年度地源和费用边界。`status=0` 是在设定 gap 容差下结束，不能说数学上零误差全局最优。求解失败也不能写成失供率 100%。

年度回放已在模型内保留所选方案的年化资本费用，不能在报告再加一次。现金运营费、优化惩罚项、供热分摊费用和冷热总费用分开展示。总冷热碳约束包含共享电力；旧供热分量混有共享电力抵扣时不作为净供热碳强度。

首轮 runner 的 `cash` 汇总含情景碳价成本，报告应将能源/运维费用、年化资本与情景碳成本拆开，不能据字段名声称均为企业实际支付现金。复核入口 `scripts/review_competition_evidence.py` 认证指定范围的计算记录，不批准参数真实性。

发布入口 `scripts/package_competition_release.py` 要求总控通过的数据来源审核清单。数据文件及结果身份采用逐项白名单，不再自动复制全部 `data/` 历史示例；没有通过来源审核时拒绝生成提交包。数学测试夹具与工程实测数据严格区分。

正式运行应保存 `model_config.json`、`model_timeseries.csv`、`investments.csv`、`dispatch.csv`、楼宇轨迹、审计、性能记录及 manifest。逐栋、逐节点、逐片区、系统合计是同一能量的不同视图，不能相加形成“总节能”。

## 公开实测外部检验

`data/external_rc_benchmark_20261001/` 保存 Fraunhofer/IEA EBC Annex 71 N2 原发布数据、许可与字段映射，包含 11988 个 10 分钟样本。发布者对短缺测做过处理，不能称为未经处理的全部原始读数。面积加权温度是派生观测，太阳得热是受约束代理；拟合 H/C 属于该外部建筑的估计，不替换武汉参数。

复现前阅读该目录 README，安装上述外部验证依赖，并使用新的输出目录：

```powershell
python scripts/reproduce_external_measurement_studies.py --output-dir outputs/external_validation_reproduction_01 --refit
```

首轮完整结果保存在 `outputs/competition_20261001/C1_external/`。只读复现入口同时复算首轮和唯一一次获批探索试验，原结果不覆盖。检验同时报告一步、连续开环及分段开环和相应持续温度基线，包括开环明显较差、后续试验进一步劣化的结果；不能仅选择一步误差作为全年精度。

发布入口还对文档、补充材料和外部验证输入身份做逐项白名单与哈希核对，旧结论不会因位于 `docs/` 而自动进入交付包。完整工作目录回归包含历史数据兼容性测试；精简公开包的可运行范围以其发布清单为准。

## 已知范围

本轮网络是按温级的静态 kW 输热与损耗/泵耗代理，未实现变流动态水力迟滞或真实地下 GIS。建筑 1R1C 为合成原型，未做本地观测校准。地下取热/回灌年度比值不等于多年土壤热响应模型。火用相关指标仅覆盖现有热量品位口径，不能解释为完整系统火用效率。缺少实际收费或报价时，财务结果只能是明确假设下的情景，不是项目收益承诺。
