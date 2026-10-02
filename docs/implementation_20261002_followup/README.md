# 11.12.0rc2 跟进与上传验收

2026-10-02，Asia/Shanghai。按用户要求继续原工程、上传优化完成部分；本版为研究候选。论文、PDF与PPT未重写。前批功能、实验和七项评分对应见[完整实施记录](../implementation_20261002/README.md)。

用户提供的参考审计基于另一份材料快照，其中分数属于模拟评估。本轮逐项核对其问题是否仍存在，未将该分数当作官方成绩，也未将新增成果自动折算为得分。

## 上传前需要用户提供的数据

优先选乌鲁木齐、固原或武汉**一个实际项目**，提供同对象、同步的热输入/室温/设备运行/能耗及基本建筑、设备和账单边界。不是要求三城全部现场实测才可继续，也不是再收集一批宏观统计。

具体单位、最小记录、替代测量方式和建议时长见[一页数据清单](DATA_REQUEST.md)；已有资料与适用范围见[14类来源状态索引](DATA_SOURCE_INDEX.csv)。索引保留原本机审计定位，发布侧另给相对映射/外部附件获取说明，不能将本机路径写成克隆后自动存在。来源类别、适用性核实和敏感性状态分开，不编造统计置信概率。

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
