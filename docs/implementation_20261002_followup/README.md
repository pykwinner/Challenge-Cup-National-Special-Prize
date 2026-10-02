# 11.12.0rc2 跟进与上传验收

2026-10-02，Asia/Shanghai。按用户要求继续原工程、上传优化完成部分；本版为研究候选。论文、PDF与PPT未重写。前批功能、实验和七项评分对应见[完整实施记录](../implementation_20261002/README.md)。

用户提供的参考审计基于另一份材料快照，其中分数属于模拟评估。本轮逐项核对其问题是否仍存在，未将该分数当作官方成绩，也未将新增成果自动折算为得分。

## 数据条件与当前推进范围

用户随后确认三城现场逐时负荷和设备/管网SCADA属于企业内部，无法取得。当前按公开规划、标准原型、可追溯气象与外部公开计量基准推进，**不再将这些内部记录作为用户待办、实验或上传的前置条件**。一个同对象现场项目的热输入、室温、设备和账单边界仅作为未来合作验证的可选扩展。

原[一页数据清单](DATA_REQUEST.md)保留为未来现场合作接口；当前接入决定和七项评分主线见[三城规划与标准参数接入决定](../source_intake_20261002/planning_user_update/THREE_CITY_DECISIONS.md)。已有资料与适用范围见[14类来源状态索引](DATA_SOURCE_INDEX.csv)。索引保留原本机审计定位，发布侧另给相对映射/外部附件获取说明，不能将本机路径写成克隆后自动存在。来源类别、适用性核实和敏感性状态分开，不编造统计置信概率。本节为rc2发布后的工作前提更新，不改写当时实验结果。

## 对参考报告三条建议的处理

| 建议 | 本次处理 | 验收边界 |
|---|---|---|
| 醒目区分最优与可行方案 | 增加求解范围、结果属性、逐阶段状态/gap/目标上下界；历史结果增加独立侧表，不改旧summary | 求解器在登记容差内的结论只针对实际求解模型。固定投资最优不能升级为投资规划最优；一级仅可行时仍为可行值锚定 |
| gap给出指标不确定区间 | 仅报告有直接对偶证据、同一目标和单位的界 | 求解间隙不是统计置信区间，也不能用gap乘碳排/投资额生成误差条。独立碳排范围需要另行求解，当前不伪造 |
| 温差软约束及最远站反馈切割 | 本次保留已有静态后校核，明确真实水温/管路/泵输入需求；不把它写成已经实现的新水力模型 | 当前主模型按热量/温位与固定输配系数建模，没有逐管供回水温度及质量流量状态。软化硬工程底线或无有效性证明的切割可能接受违规方案或删掉可行方案 |
| 溯源置信表 | 保留132/89行字段溯源，新增14类来源/适用性/敏感性入口与待接入字段 | 来源存在、计算数值通过、当地观测验证分别记录。规范构造推导是可用参考，不冒充实测 |
| 一个真实项目样本外对标 | 现有Fraunhofer N2实测外部基准继续使用，修复采样单位兼容并复算原有两批指标；本地项目按清单接入 | 已有外部计量不等于三城实际工程对标。保留不利的长时开环结果，没有通过改数据或更换测试集掩盖它 |

后续水力增强的合理顺序是：确认同一回路的几何、内径/保温、泵和温度边界；建立与热量守恒一致的质量流量/温度关系；在固定拓扑小系统验证；再将有有效性依据的保守约束或反馈加入主流程。固定温差下的容量预筛可以先做，但不能声称它已解决温差漂移或闭环水力平衡。

## 本次实际新增的复核

- `external_rc_units.xml`：22项外部RC相关测试通过。对秒/毫秒/微秒/纳秒存储精度，均检验正常600秒间隔以及缺一个采样点时拒绝。
- [外部两批最终复算](external_rc_reproduction_final/reproduction_report.json)：144个原有标量指标通过；没有重新拟合。首批脚本/测试、次批脚本共三份历史快照与原manifest的SHA一致，维护版计算代码另记身份。早一轮复算仍保留在`external_rc_reproduction/`。
- [软权重确认便携复算](bdg2_soft_portable.json)：5份已锁原CSV、9栋、36条方法记录通过。
- [硬权重确认便携复算](bdg2_hard_portable.json)：5份已锁原CSV、6栋、30条方法记录与6个LP见证通过。两入口从原CSV重建日历、缺测、能量、峰值、实际相邻爬坡和CDF积分W1；不是只检查hash，也没有重复运行KMeans。

便携数值复核命令（工程根目录，数据目录由用户显式指定，输出必须是新文件）：

```text
python scripts/verify_bdg2_soft_confirmation.py --data-root <BDG2目录> --output-file <新目录>/soft.json
python scripts/verify_bdg2_hard_confirmation.py --data-root <BDG2目录> --output-file <新目录>/hard.json
python scripts/reproduce_external_measurement_studies.py --output-dir <新的外部复算目录>
```

BDG2大表需按发布清单取得并符合冻结SHA；标准/论文全文不因公开可下载就默认有再分发许可。空间原生OSM证据、旧初跑原CSV身份、新共享图离线重建分别核对。完整依赖与实际上传目录测试由GitHub发布记录给出，不把本机隔离依赖目录整体作为源码发布。

旧 `test_approval_and_first_round_entire_tree_unchanged` 与 `test_final_manifest_hashes_and_approved_identities` 绑定旧探索整树、当时源码和审批快照，本次维护后不再声称当前树与旧时间点完全相同。保留旧记录，实际回归明确排除此两项；它们不计入通过。新的原结果/源码快照身份复核保留。

## 最终主工程回归

最终完整回归 **882 passed、另26 subtests passed、2 deselected、2 warnings**，82.74秒，无失败、无跳过。日志[final_tests_02.log](final_tests_02.log)，机器记录[final_tests_02.xml](final_tests_02.xml)。两条warning来自检验旧算法重复样本空簇失败被保留的专门反例。

第一轮为881通过、1失败，记录`final_tests.log/.xml`保留；失败原因是旧后续实验校验仍引用当前首批源码，而当前源码已作时间精度修复。已将这两项显式解析为已归档原源码，次批脚本本身也另留旧快照。相关33项通过后重跑完整回归，没有通过删除失败测试解决问题。

```powershell
$env:PYTHONPATH='src;docs/execution_20261002/spatial_reference/_runtime_packages'
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
python -m pytest -q -k 'not test_approval_and_first_round_entire_tree_unchanged and not test_final_manifest_hashes_and_approved_identities' --basetemp .test_tmp/rc2_final_02 --junitxml docs/implementation_20261002_followup/final_tests_02.xml
```

这是原工程本机测试。实际使用`D:/app/anaconda3/python.exe`及已记录的可选空间依赖；GitHub发布会在独立依赖环境中测试实际提交副本，结果另记，不把二者混为一次验证。

新求解属性、3组完整季示例、192条历史旁表、读取失败清单及中文决策表见[结果属性交付](solution_quality/README.md)。原源码冻结身份见[release_source_freeze.json](release_source_freeze.json)；远端SHA、tag及发布副本测试以GitHub发布登记为准。

## 三城模型特色与便携复现新增结果

本轮已完成三城各2880小时、同原型同方法学价格的连续运行对照，保留每城反馈基准、费用端点、碳端点和中点碳帽解。把两端之间额外碳排空间减半，仍保留80.9%–89.4%的最大电量费节省，能够具体展示建筑热惯性与费用—碳约束的联动作用。峰值功率随费用优化升高亦同步报告；不将共同湖北参考价称为三城当地账单。

- [官方七项评分对应特色、数值结果与尚待补强项](../source_intake_20261002/planning_user_update/THREE_CITY_DECISIONS.md)。
- [12行决策表](parallel_stage_02/climate_dispatch_analysis_02/decision_table.csv)、[三城费用碳图](parallel_stage_02/climate_dispatch_analysis_02/cost_carbon_tradeoff.png)、[160项导出复核及9份源出力篡改反例](parallel_stage_02/climate_dispatch_export_audit_02.json)。
- [独立审查的四项特色证据](parallel_stage_02/review/feature_evidence.json)，将机制试验、公开原型调度与真实工程证据分别标识。
- [价格配对权重候选](parallel_stage_02/algorithm/handoff.json)及[三城连续输入](parallel_stage_02/data/manifest.json)已经实现；新权重尚未做城市效果确认，未据此声称策略优于旧法。
- [便携数值复现入口](../../scripts/reproduce_admitted_reference.py)默认只接受原审核锁定的处理后输入；原件重查为单独strict模式。已在没有原件PDF的隔离输入根完成一次真实2880h求解和导出复核，不以缺少原电脑附件阻断数值复算。
- [设备采购与性能字段连接表](parallel_stage_02/data/main_case_source_join.json)：成套含税报价与安装范围已找到原盖章合同；同型号低温性能、末端/备源和未定费用仍逐项记录。该输入缺口不阻止上述公开原型运行成果交付，但还不能产出完整经济依据的自由投资主案例。

收敛复核保留首轮1e-4 gap结果，九个优化案例另版统一请求gap0，每阶段60秒；三城共12行均通过，15个求解阶段正常收敛。该调整发生在发现原乌鲁木齐主目标容差问题之后，明确是数值改进，非新的盲测。原冻结研究脚本若城市准备/反馈基准失败会中断且不生成统一最终失败清单，此局限未触发本轮已通过样本；后续通用入口应改善失败归档，当前原脚本与实验身份保持不变。

新增内容在原工程已完成，上传状态以相应GitHub候选版本交接记录为准。仍按用户要求暂不制作最终论文/PPT。

## 发布后的代表日运行诊断

在原工程完成了武汉公开原型的有限温控策略确认：8条策略分别连续回放2880小时，以固定8个真实代表日比较3种权重和4档预算，保留96条候选及12组选择。本轮新增脚本和证据归属11.12.0rc3研究候选增量；发布状态与实际副本验收以[rc3交接记录](../github_release_11.12.0rc3/README.md)为准，未重跑上述整套历史回归。

全部8条策略的费用估计平均绝对百分比误差，频数/软校准/硬能量W1分别为0.763980%/0.346432%/0.117145%；耗电估计对应0.329620%/0.114721%/0.237849%。硬权重在该案例中费用估计较好，软校准耗电估计较好。三种方法四档预算均选零预热，未证明策略选择改善；固定谷段增加预热在本库中反而增加费用。完整结论及评分对应见[三城决策文档的本轮结果](../source_intake_20261002/planning_user_update/THREE_CITY_DECISIONS.md)。

24项专项测试通过；[独立核查](representative_season_confirmation/independent_audit_01.json)的567项来源身份与数值检查通过，并通过[功率故障注入对照](representative_season_confirmation/negative_control_01.json)确认审核器能识别数值错误。[全部8策略误差图](representative_season_confirmation/analysis_01/policy_estimation_errors.png)保留费用与耗电的不同排序；[协议措辞勘误](representative_season_confirmation/protocol_semantics_erratum.json)解释旧键名，不改锁定协议和原结果。
