# E4后续：能量硬匹配下最小一维W1权重

日期：2026-10-02。在前9栋门槛确认之后实施的增量，独立输出目录，未上传GitHub；原9栋及原24案例年度结果不覆盖。前9栋已被用于暴露后续设计问题，对**本次新硬权重方法**只能作开发参照，不再作为确认数据。本次另冻结6个未见建筑，每楼2017一年，先完成12项解析测试再冻结样本、实际运行。

## 已实现的具体改进

`src/heatplan/hard_weight_calibration.py::minimum_w1_energy_weights` 固定现有真实代表日，联合保持能量、总天数与极端权重下界，并直接最小化负荷边际分布的一维W1。采用标准线性规划和绝对值epigraph，是成熟方法的任务适配，不声称新理论或新求解器。

设保留观测有N个完整日，每日H个等长步，真实代表日负荷为`x[i,h]`。`e[i]=sum_h x[i,h]`；支持点`s[j]`是原观测与代表值的排序并集。在每个相邻支持区间：

- 原CDF `F[j] = count(observed <= s[j])/(H*N)`。
- 代表CDF对权重线性：`P[j,i] = count(x[i,:] <= s[j])/(H*N)`，故为`P*w`。
- 最小化 `sum_j (s[j+1]-s[j])*z[j]/mean(observed)`，约束`-z <= P*w-F <= z`。
- 硬等式为`e*w=sum(observed)`、`sum(w)=N`，并有`w>=l`，极端日`l=1`、普通日`l=1e-6`。

LP用平移`w=l+v`避免下界歧义，缩放硬等式，返回后独立复算原单位能量、总天数、下界及CDF目标。不可行、求解/残差失败及零能量分别明示，没有改成软目标、填缺测或改负荷。结果不自动采用，仍经过前轮完全相同的研究门槛：能量0.1%、W1不劣于未校准极端法、守恒/下界、保留峰/真实相邻爬坡及全部原有效小时峰完整性。阈值没有因结果调整。

只优化负荷一维边际CDF，未优化多变量联合分布或温度CDF；虽然原日内负荷—气温配对不改，重新赋权可能改变气温统计。该LP没有设备投资、服务舒适、储能状态联系、运行成本/排放或固定投资连续回放，不能从成功/采用推出工程规划可用。

当前硬LP只通过独立统计API与本benchmark显式调用。没有替换多负荷、多情景、多年的既有代表期默认流程；原规划模型不会静默自动使用这些权重。

## 冻结确认集与身份

6栋全部未出现在原12个建筑和后续9个建筑中，热水、蒸汽、冷水各2栋。沿用95%表计和气温联合覆盖、无负计量、稳定ID与站点轮转规则，保留全零/不可行/拒绝样本，不因表现换楼。此次5个站点都已出现，故是新建筑确认，不是新站点确认。

| 类别 | 建筑 | 站点 | 完整日N | 实际代表日M |
|---|---|---|---:|---:|
| 热水 | Bobcat_education_Alissa | Bobcat | 320 | 8 |
| 热水 | Eagle_assembly_Ian | Eagle | 345 | 10 |
| 蒸汽 | Bull_assembly_Brandon | Bull | 304 | 9 |
| 蒸汽 | Cockatoo_assembly_Genoveva | Cockatoo | 276 | 9 |
| 冷水 | Bobcat_education_Angela | Bobcat | 338 | 9 |
| 冷水 | Bull_assembly_Dorethea | Bull | 338 | 9 |

初始K=6；先生成极端法固定实际日集合，再对软/硬权重候选使用完全相同的M和未改观测。3次独立调用检查确定性，保持既有seed42和线程限制1，无加速。各权重方法均复用同一选日，不把校准调用的耗时当完整算法耗时。

来源是已核验的BDG2官方LFS CSV，commit `9b97ccbe90096aff42ed4fd6493bf7ae692d7118`。协议锁SHA256 `65c80f389c374a5c2bffb2bd77804417e27e7fadca7a9b780bc68534612b9cc0`；[protocol.json](protocol.json)、[selection.json](selection.json)、[protocol_lock.json](protocol_lock.json) 和 [version](version/) 保存新样本、完整选取/拒绝决策、代码、环境与原5份实际使用CSV字节/SHA256。运行前后原始哈希一致。

BDG2是海外表计压缩基准，不能充当中国三城现场验证。

## 实测收益、采用与代价

下表为固定6例的中位数，包含不通过采用门槛的候选和回退；没有筛选成功案例重算有利中位数。年度内部压缩/拟合并非未知未来预测。

| 同一固定实际日集合上的方法 | 绝对能量偏差 | 归一W1 | 保留范围峰值/最大真实爬坡 |
|---|---:|---:|---:|
| 未校准极端法 | 8.29228% | 15.4450% | 100%/100% |
| 原软正则权重候选 | 0.00138513% | 12.3150% | 100%/100% |
| 硬能量最小W1候选 | 约6.98×10⁻¹⁴% | 4.47753% | 100%/100% |
| 软候选采用规则输出（含回退） | 0.604569% | 15.4450% | 100%/100% |
| 硬候选采用规则输出（含回退） | 约1.08×10⁻¹³% | 4.47753% | 100%/100% |

硬候选6/6例W1优于软候选，归一W1案例中位数下降63.64%；最大相对能量残差约3.57×10⁻¹⁵（无量纲），属于数值级匹配。该结论限于这6个固定案例、固定选日和硬约束可行范围，不声称所有数据必然优于不满足硬约束的软候选。

门槛采用从软候选3/6增至硬候选4/6；新增通过的是`Bull_assembly_Brandon`：原软权重W1为20.9090%，比未校准的17.6675%更差，硬候选降为4.14533%，能量也硬匹配。它展示了目标与采用规则一致的实际作用，没有移动阈值。

| 建筑 | 软候选采用 | 硬候选采用 | 硬候选保留的拒绝/限制 |
|---|---|---|---|
| Bobcat_education_Alissa | 是 | 是 | 只通过统计门槛；原有效观测能量34.7855%退出完整日 |
| Eagle_assembly_Ian | 否 | 否 | 所选峰值为全部原有效观测峰97.9006% |
| Bull_assembly_Brandon | 否 | 是 | 只通过统计门槛；软候选原因是W1变差 |
| Cockatoo_assembly_Genoveva | 是 | 是 | 只通过统计门槛；原有效观测能量29.6328%退出完整日 |
| Bobcat_education_Angela | 否 | 否 | 所选峰值为全部原有效观测峰99.9682%，仍按原阈值拒绝 |
| Bull_assembly_Dorethea | 是 | 是 | 只通过统计门槛 |

两种门槛拒绝的2例均回退未校准极端权重，且回退也不满足原观测峰门槛。硬规则输出中位能量残差近零是本6例分布的中位数现象，不意味着6例全部通过。汇总JSON的`candidate_adopted_cases`字段只在`adoption_soft`和`adoption_hard`行表示规则采用计数，原始候选行不作该计数。

所有6例完整日筛选都退出有效表计小时：358—2023小时；退出原有效观测能量4.5866%—34.7855%。保留范围统计100%峰/爬坡不能掩盖这些损失。逐日退出、未填补原日历和原有效小时峰/真实相邻爬坡/容量诊断均在各例 `cases/<case_id>/2017/`。没有删尖峰、补成完整年或用模型输出冒充观测。

30条方法记录、0条求解/运行失败，硬LP6/6成功。实际端到端14.4548秒，含加载、失败路径、3次调用、审计和导出；调用耗时案例中位数为极端0.03678秒、软权重0.004399秒、硬权重0.17001秒。硬权重单次案例耗时范围约0.00273—1.34127秒，付出更高统计求解成本；不作加速声明。[run_manifest.json](run_manifest.json) 和逐例结果保留每次原始计时。

## 方法身份字段勘误与未来API

主线复核发现已冻结共用门槛v1的`selected_method`在采用候选时固定写成`extreme_calibrated`，因此4条已采用硬候选的记录该字段错名。原记录、原锁和原权重/数值全部保留；[method_identity_erratum.csv](method_identity_erratum.csv) 与 [JSON](method_identity_erratum.json) 给出逐例旧字段、正确字段、原记录哈希及权重一致验证，另由 [correct_method_identity.py](correct_method_identity.py) 可复算。

机器消费本冻结批次时必须使用：`candidate_method if statistical_candidate_adopted else "extreme_aware_fallback"`，不直接用旧`selected_method`。独立复算已验证采用时权重确实来自`candidate_method`对应候选，因此该问题只影响身份字段，没有更改W1/能量或采用计数。

未来活动API增加显式`candidate_method`参数；硬候选调用须传`candidate_method="hard_energy_minimum_W1"`。省略仍保留原软候选默认，回退标签保持兼容。[api_method_identity_tests.xml](api_method_identity_tests.xml)为7项新增兼容/身份测试和25项原采用/选择边界联合32项通过，不重复加总。冻结门槛v1的SHA256为`81f16ae1b7ac68e0203abb44c3064a1421037579159c3e36d8d4c38392608501`；修改后活动API及测试SHA256在勘误JSON另记，**不声称旧实验锁涵盖该活动API修改**。

## 验证与复现

- `tests/test_hard_weight_calibration.py`的12项解析测试先于样本冻结通过：唯一硬能量匹配、多个硬匹配解中W1最优、极端下界导致实际分布代价、硬不可行、常数序列、零能量、非法值/维度/日权重及原输入不变。相应初次XML见前目录 [core_review_and_hard_lp_tests.xml](../algorithm/core_review_and_hard_lp_tests.xml)，其中另外8项为独立物理反例，不重复加总为LP测试。
- [independent_verification.json](independent_verification.json)：直接从5份原CSV重构完整日，独立CDF积分复算全部30记录、6个硬LP见证与目标，全部一致；未调用实验适配器或其W1函数。它验证数值与输入映射，不作工程运行认证。
- [final_focused_tests.xml](final_focused_tests.xml)：25项采用/确认边界、12项硬LP解析测试与8项独立物理反例联合45项通过；这些分组有既有XML，不重复加总。物理KPI后续断言另在前目录复验8项通过，见 [core_review](../algorithm/core_review.md)。

项目根目录命令：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONPATH='src'
& 'D:\app\anaconda3\python.exe' -m pytest tests/test_hard_weight_calibration.py -q
& 'D:\app\anaconda3\python.exe' scripts/bdg2_hard_weight_confirmation.py verify
& 'D:\app\anaconda3\python.exe' docs/implementation_20261002/algorithm_hard_weights/verify_confirmation.py
& 'D:\app\anaconda3\python.exe' docs/implementation_20261002/algorithm_hard_weights/correct_method_identity.py
```

首次在无既有输出的干净工程副本中依次执行 `scripts/bdg2_hard_weight_confirmation.py freeze`、`run`、`report`。已有输出时freeze只核验、run拒绝覆盖。来源绝对路径迁移时，应保留旧证据再按同规则新冻结本机路径，不改原锁。初跑Windows物理核探测警告保留，所有调用正常完成，未启用新并行/加速。

本次取得的是可复算的统计权重改进与新建筑小确认；确认规模仍小、站点共享、完整日丢失及未验证连续运行的边界均保留。下一工程验收需对获准物理案例冻结资产、连续回放并逐项核验舒适、状态、资源、费用及碳帽，不能用此LP替代。
