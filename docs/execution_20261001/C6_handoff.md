# C6 独立楼宇审计交接

**最新接入复核（2026-10-01）**：总控已导出两个RC专属实际送达字段，并在规划、固定回放及年度验收门接入C6。两楼+DHW+实际制冷的4h规划→checkpoint加载→固定回放链通过服务口径核对；删除楼宇轨迹行会使独立审计及回放验收门失败。发现checkpoint没有持久化/加载楼宇轨迹和完整独立报告，具体复现见本文末尾。未重跑已有123项测试，未修改共享核心，未派子代理。

2026-10-01安全保存点：独立审计模块、篡改测试和四小时小例证据已保存。最终轻测试 **123 passed in 2.20s**；四小时全模型导出通过独立审计，六类保存的篡改报告均失败。本轮按总控模型切换指令在此结束，不再启动测试或重求解。总控尚未接入正式验收门；没有完整供暖季或全年认证结果。

## 写入范围与独立性

仅写 `src/heatplan/building_audit.py`、`tests/test_building_audit.py`、本文件、`outputs/competition_20261001/C6/`。未修改 `model.py/io.py/chronology.py/building_thermal.py` 等共享文件。pytest关闭缓存和字节码输出，临时目录均在C6专属目录内。

审计模块只依赖标准库、NumPy与pandas，**不导入或调用C2、模型、IO或求解器计算函数**。H/C、时间、初末态、得热、舒适界与上界从传入可信config/frame独立展开，使用数学exp/expm1重算解析RC。测试正例包含独立闭式常值强迫解；另有一次四小时真实HeatPlanningModel导出兼容性小例，其审计仍完全独立。只读子审查发现的数值消减、summary诊断未复核和有限性标志顺序问题均已修复。

## API

```python
from heatplan.building_audit import (
    audit_building_trajectory,
    building_audit_identity,
)

# 在求解前冻结，并在结果目录以外保留可信副本。
# 全模型使用 model.ts；不要用未经规范化的多情景原始frame替代。
identity = building_audit_identity(effective_config, effective_frame)

report = audit_building_trajectory(
    effective_config,
    effective_frame,        # 完整预定输入，不能从输出轨迹反向生成
    trajectory,             # pd.DataFrame，C2完整导出列
    dispatch=dispatch,      # 可选：模型长表
    summary=summary,        # 可选：dict
    expected_input_identity=identity,
    require_continuous_fixed=True,  # 正式连续回放；代表日使用False
)
assert report["passed"]
json.dumps(report, ensure_ascii=False, allow_nan=False)
```

默认容差：温度1e-6℃，功率1e-6kW，能量绝对1e-5kWh，元数据绝对1e-8，汇总能量相对1e-9。冷热**各自大于功率容差**即严格物理失败，与exclusive/relaxed声明无关；仅净热量RC残差为零不能放行。

报告是JSON可序列化dict；数据错误返回失败报告。`checks`各项包含passed、评估次数、失败次数及最多8条定位示例；`metrics`为独立重算指标；`building_groups`含每楼每组初末态与库存差；`node_service_energy`是楼宇到node/grade的单层聚合。`passed`包含所有请求的检查；`building_physics_passed`只反映可信输入与楼宇轨迹，允许可选dispatch/summary失败时仍显示楼宇本身的结果。`dispatch_check_scope`和`summary_check_scope`明确范围。

`scope`恒为`supplied_trusted_frame_only`，`annual_certification`恒为false：该模块不从文件名、行数、测试数量推断全年认证。正式验收门仍须核对完整服务区间、投资、求解状态/gap、源网储平衡、年度约束和产物manifest。

## 完整性、物理与口径

- 期望键集合为配置楼宇×`range(len(frame))`，每键恰好一行；删行、重复补行、额外楼宇、分数/负/越界索引均拒绝。输出顺序可以变化，时间索引不得重新编号掩盖缺失。
- 核对UTC实际时刻、scenario、boundary_group、node、grade、mode及全部已知数值字段。除有契约含义的空末态界外，NaN/Inf拒绝；末态空值也与原配置逐行比对。改H/C、初温、得热、气象、温度界、功率上界、权重或输出诊断不能重定义物理。
- 每组连续且单情景；组间可跳时或复用同一代表日时间，不能跨组错误承接。fixed使用组初温与独立末态规则；cyclic使用本组最后末温作为首时段起温。连续回放选项要求fixed、每情景单组、period_weight=1；它本身不证明年度覆盖。
- 可信前驱状态重算精确RC、温度界、功率正负与上界、初末态；任何同时正供热/供冷超容差都失败。参考负荷不进入实际送达核算，面积不重复乘H/C。
- dispatch完整核对`building_temperature/building_heat/building_cooling`的值、资产、node/grade、单位与时刻，再做逐节点聚合。可选`building_heat_by_node/building_cooling_by_node`若存在，则完整比对并禁止父子层重复计量。当前核心未导出两个node类别，因此默认使用逐楼dispatch聚合。
- summary全局实际能量权重为`dt*period_weight*scenario_probability`。优先接受`building_heat_delivered_kwh_per_year`和`building_cooling_delivered_kwh_per_year`。全部服务均由RC覆盖时，也核对现有`demand_kwh_per_year/cooling_demand_kwh_per_year`并检查RC无unserved。存在未覆盖DHW/固定服务时，要求专属RC指标，否则`summary_actual_service_available`失败；不会把整个系统需求错误当成楼宇送达。
- 当前核心内部有楼宇实际送达metric，但summary尚未输出两个专属字段。**总控接入时应输出它们**，才能对混合服务准确交叉核验。若summary不传入，则报告明确未检查summary，不伪称已有这项证据。
- 对summary中存在的楼宇数量、RC最大残差、欠温/过温度时、舒适/互斥布尔标志也复核；未审计费用、碳、源网平衡或gap。参考热量字段不充当实际送达指标。
- 全模型必须传实际规范化`model.ts`并基于同一表示冻结指纹：原始多情景frame缺概率时，直接C2默认1，核心可能默认1/情景数，不能从被审计输出猜测哪一种才是原输入。
- 单独改config与导出物性可被元数据比对发现；**config/frame/trajectory协同篡改**须依赖外部保留的求解前identity。指纹不是签名，同放在可修改目录中的指纹不能自证来源。本证据包指纹只展示接口，正式验收需可信保存。CSV类型/时区表示转换会影响指纹，应冻结持久化后相同表示。

## 命令与真实结果

工作目录：`D:/competion/xiaotiao/北方供热三类场景公用总框架V10/north_heat_challenge`。

```powershell
$env:PYTHONPATH='src'
$env:PYTHONDONTWRITEBYTECODE='1'
& D:/app/anaconda3/python.exe -m pytest tests/test_building_audit.py -q -p no:cacheprovider --basetemp=outputs/competition_20261001/C6/pytest_run_03 --durations=3
& D:/app/anaconda3/python.exe outputs/competition_20261001/C6/run_evidence.py
```

复跑时使用新的C6 basetemp目录，避免pytest清理旧目录的Windows权限问题。没有调用大型MILP或完整年度/季节入口。

| 批次 | 实测结果 |
| --- | --- |
| pytest_run_01 | 99 passed in 2.10s |
| pytest_run_02 | 112 passed、1 failed；新增大热容回归揭示相近温度相减导致的功率界精度问题 |
| pytest_run_03 | 修复稳定逆变换并扩展summary/溢出负例后，123 passed in 2.20s |
| 4h真实模型 | 单楼、固定现有热泵100kW、新增容量上限0；status=0，mip_gap=0 |
| 实际楼宇送达 | 热35.2356817111394kWh；冷0kWh；参考曲线999kW没有当作实际供热 |
| 独立物理结果 | 最大RC残差0℃、欠温/过温/末态违约0、冷热同时开启行数0 |
| 时间 | 建模及求解0.0130748s，独立审计0.0121327s；单次4h小例，不外推年度性能 |
| 保存篡改证据 | 删行、删除实际热量、同时加热冷、伪造C、参考冒充实际、冻结输入修改，6项均passed=false |

测试还覆盖独立代表日复用时间、组初温覆盖、cyclic、半小时步长、时区等价表示、非有限数、伪造索引、冷热切换、冷热禁用、舒适列/得热、服务标记不能屏蔽舒适违规、节点别名不双计、混合DHW summary范围、CSV回读及外部身份锚。

运行环境：Python3.12.7、NumPy1.26.4、pandas2.2.2、SciPy1.15.3。所有日志、原始小例CSV、config、summary、投资表、JSON审计结果及复现脚本位于`outputs/competition_20261001/C6/`。`manifest.json`最后写入，`complete:true`仅指本C6小例证据包；status明确`C6_small_fixture_audit_complete_not_annual_certification`。

## 冻结身份（SHA256）

```text
src/heatplan/building_audit.py
54d3b85ae9277c511bedf3051b20d764e3c5034dcfa099213c7a6128b17e96f8
tests/test_building_audit.py
e74df58b554af34bc245b6218cb4e0ac1948f3bf859bca02b2cbbd1c95606e1f
依赖 src/heatplan/building_thermal.py
1a1ce76fa1ba9aa151e25d036591a01e1e6d54dfbfd9b0619a385a5a4ff38c40
依赖 src/heatplan/model.py（仅小例生成依赖；C6未修改）
57b7f44f11c4f2a84f341a990050c5a86fb4dc40d506a6349501644167401796
outputs/competition_20261001/C6/effective_config.json
952151052ce7b93431bf33d321a249fcc39a939e608db878dd3cfd399a6196ef
outputs/competition_20261001/C6/effective_frame.csv
1ed3bf7efb27d7c3fc91488a32a9192cbf85c2f17369beb55382580f6580eef7
outputs/competition_20261001/C6/audit_valid.json
1ebc8e500ff8f996aa1b9020847f4f4fce91895411eb2c87e4008719a39a1d8f
```

完整产物哈希见manifest。API语义化求解前identity为config `ce6e1efeb8832c7ca609b47afb4750922f0c079ecee320fd3798995faeee4fdd`、frame `d5ca1a8c6793ac586460467be9efd743157e67c5289309b689134c678b69f728`；它与磁盘文件字节哈希的定义不同。

## 模型切换观察与停止点

总控先通知用户要求本项目子对话使用`gpt-6.1-sol`、`thinking=ultra`、普通速度，并称本机全局`service_tier=default`；随后总控读取当前`turn_context`仍为`gpt-6-astra`，要求本轮安全保存后结束，待下一轮明确恢复。后一条信息覆盖“新模型已生效”的任何推断。

C6未启用fast或修改服务档位；没有独立的推理引擎运行时检查接口，因此**不宣称已验证Sol/Ultra/default生效**。`evidence_results.json`仅记录接收的目标设置并将runtime_model_verified和runtime_service_tier_verified置false。本交接补记随后总控观察到的当前模型状态。本轮任务产物已保存，未启动新任务或再次求解。

剩余归属：总控接入验收门、输出混合服务专属RC实际能量字段、冻结正式输入与投资并开展完整时序验证。123项测试与本4h小例均不能替代17/46等既有测试以外仍缺的全年证据；C6没有签发正式全年证书。

## 总控接入短复核（恢复轮，2026-10-01）

本节覆盖上文首次交接中“尚未输出专属字段/尚未接入验收门”的历史状态。只读检查与专属scratch完成后停止，正式C4年度结果留给总控读取审计。此次没有新增子代理，没有改变服务速度；收到恢复设置为GPT-6.1-Sol、Ultra、普通速度，仍没有独立运行时模型/档位核验接口，不宣称运行时设置已验证。

### 已确认接入

- `model.py:3128`、`:3129`正确输出`building_heat_delivered_kwh_per_year/building_cooling_delivered_kwh_per_year`，使用内部实际送达metric，不使用参考负荷。
- `chronology.py:2237`的`attach_independent_building_audit`使用`model.config/model.ts`、完整trajectory、dispatch、summary及调用方求解前identity，回写独立passed/metrics，并将完整报告放在artifacts属性。规划调用在`:2362`，固定回放在`:2314`；identity分别在构模/求解前创建，代表期continuous=False、固定连续回放True，契约一致。
- `annual_replay_acceptance_audit`在`:1687`新增规划与回放独立门。真实回放删一条轨迹后，attach返回false，`replay_independent_building_audit`门也变为false。
- checkpoint保持原始summary实际冷热字段及独立通过标志；manifest文件哈希能拒绝修改summary而未同步修改manifest的输入。本轮篡改测试后已恢复原文件字节。

### 微型链实测

复现脚本与全部证据：`outputs/competition_20261001/C6/integration_review_20261001/`。输入两楼（H=1/2kW/K、C=3/7kWh/K），同node/low、室温固定20℃，前2h室外10℃、后2h30℃；high温级DHW恒2kW。热泵COP/EER均2，新容量候选0—100kW，测试设计室外10℃仅为短例容量规则，不是气候设计温度。旧热/冷参考999/777kW保留，用于检查是否错误计入实际服务。

```powershell
$env:PYTHONPATH='src'
$env:PYTHONDONTWRITEBYTECODE='1'
& D:/app/anaconda3/python.exe outputs/competition_20261001/C6/integration_review_20261001/run_micro_chain.py
```

此链使用真实`prepare_fixed_investment_plan`、checkpoint保存/加载、`replay_prepared_fixed_investment_plan(require_full_year=False)`及验收函数，未stub年度门或物理审计。只做4h小例，无年度MILP。

| 核对项 | 规划（period_weight=2） | 固定回放（period_weight=1） |
| --- | --- | --- |
| 求解状态 | 0 | 0 |
| 两楼实际热量 | 120kWh | 60kWh |
| 两楼实际冷量 | 120kWh | 60kWh |
| 实际DHW | 16kWh | 8kWh |
| summary总热服务 | 136kWh | 68kWh |
| summary总冷服务 | 120kWh | 60kWh |
| summary/实际dispatch/C6重算 | 全部一致 | 全部一致 |
| 独立门 | 通过 | 通过 |
| 时间（本次单次） | 0.0381196s | 0.0721401s（包含短窗预筛查与审计） |

选中热泵新增容量约32kW，checkpoint加载后仍32kW，回放`max_new_capacity_kw=0`。结果差异来自声明的代表期权重，未将权重乘进RC时间步。两楼热与冷在不同小时实际贡献；DHW独立供热没有被RC覆盖吞掉，也没有被楼宇层重复计量。

4h验收的`continuous_full_calendar_year`与代表覆盖门为false、`certification_pass=false`，没有因独立门通过误发全年证书。删掉最后一条楼宇轨迹后C6失败，实际回放独立门false；保留原summary的其他舒适/数值真值也不能掩盖该门失败。

### 待总控处理的问题及复现

**P2：固定投资checkpoint缺完整楼宇审计证据。** 定位：`chronology.py:57`的`_FIXED_PLAN_CHECKPOINT_FILES`仅列summary/investments/dispatch/fixed config/freeze/profile；`:127`保存函数没有写楼宇trajectory、comfort、独立报告或C6求解前输入identity；`:271`加载时构造`SolveArtifacts`未传building字段，因此`loaded.planning.building_trajectory is None`且没有`building_independent_audit`属性。summary中`independent_building_audit_passed=True`仍被保留，年度规划独立门也仍通过。报告本轮没有发现能量错计，但恢复后的规划证据不能由当前API独立复算。

直接复现已经包含在上述脚本，结果见`review_results.json`的checkpoint：`trajectory_loaded=false/full_independent_report_loaded=false/fresh_independent_reaudit_possible=false`。`checkpoint_reaudit_attempt.json`记录把loaded轨迹交给C6时的失败（NoneType无len）。完整原规划审计另在C6目录保存，**不是checkpoint自带**；正式checkpoint恢复并调用通用export时不能据此假定原楼宇证据可用。

建议总控将trajectory、完整C6报告、相同表示的规范化可信输入及其求解前identity纳入checkpoint文件manifest，并在加载时恢复/复核；启用RC的旧checkpoint缺这些证据时不要仅凭历史布尔标志宣布规划独立认证已可复现。C6未写共享核心，也未扩大本轮任务做修复。

另一个可移植性观察：本短例规划与固定回放最终均为连续问题，SciPy不提供mip_gap；现有summary字段为NaN。首轮scratch用严格JSON保存因此失败；本轮脚本只将可选不可用gap规范为null后复跑成功，没有改动模型输出或物理数字。`review_results.json`记录`optional_nonfinite_summary_fields=["mip_gap"]`；本小例不能报告MILP gap=0。checkpoint当前json.dumps会保留非标准NaN，建议共享输出层统一不可用gap为null。C6独立物理报告本身保持严格JSON，未把该可选求解字段当物理通过证据。

### 本轮身份与保存状态

完整哈希和完成标志见`integration_review_20261001/review_manifest.json`，status=`micro_integration_review_complete_checkpoint_evidence_gap`，complete只指这次4h接入复核产物保存完成。

```text
src/heatplan/model.py
c1cf15ce2a3bc9b7e8f40362eed9d935f665151cf7b08145b1468fa66f0fe1e5
src/heatplan/chronology.py
af080b4dad4797b42bcae8f192d54127ca27e5d9ee7338d331f73c84ab3b2858
src/heatplan/building_audit.py（本轮未修改）
54d3b85ae9277c511bedf3051b20d764e3c5034dcfa099213c7a6128b17e96f8
integration_review_20261001/run_micro_chain.py
78dbdd0033c46c247c72396123ec2a00ef4f805988de96abd20fc416947f352b
integration_review_20261001/review_results.json
d3e590cda4f05e2cecc3d8699c8276a34e0432ee4ee7b73b3dc02168fe38430f
```

最终只新增C6专属scratch与本交接段，既有123测试没有无故重跑，shared core没有C6写入。本轮完成后停止；正式全年物理结论仍依赖真实完整结果及相同输入、代码、投资身份的审核。
