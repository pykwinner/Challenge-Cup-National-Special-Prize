# 11.12.0rc2结果属性与优化证据补充

本批把投资范围与求解质量分开登记，新主摘要自动导出；旧冻结摘要、协议及manifest不改写。代码进入新epoch，不能声称2026-10-02前批协议覆盖本轮修改。候选版本的上传、完整回归与远端核对由主线登记，本目录不代替发布记录。

实现文件：`src/heatplan/solution_quality.py`、`model.py`的最小接入、`tests/test_solution_quality.py`、`scripts/audit_solution_quality.py`与限定示例入口`scripts/run_solution_quality_examples.py`。

## 两条属性轴

|轴|取值与依据|解释边界|
|---|---|---|
|投资范围|`joint_investment_dispatch`、`fixed_investment_dispatch`、`existing_assets_dispatch`、`mixed_fixed_and_free_investment_dispatch`、`unknown_investment_scope`|新模型检查实际投资变量上下界。选中新增容量为0，不代表投资已固定。冻结标记与仍自由的投资变量同时存在时单列mixed。历史缺配置时保持unknown。|
|求解质量|`solver_proven_optimal_within_registered_tolerance`|数值审计、目标复算通过，已知目标的求解器最优终止且gap在登记容差内或登记gap闭合；已知连续LP允许null MIP gap，null保留，不造0。只证明本次有限模型及求解器容差范围。|
|求解质量|`time_or_iteration_limited_feasible_approximation`|保留已审核可行解和当次有效目标界；不声明全局最优。|
|求解质量|`feasible_unproven_or_tolerance_unregistered`|有可行解，但gap/容差不足或不一致，不能登记收敛。|
|求解质量|`missing_evidence`、`failed_or_infeasible`|缺必要状态/目标/审核证据，或求解/数值/服务审核失败。不给缺失证据补零。|

固定投资结果即使在运行模型内得到最优终止，`optimality_scope`仍为fixed；`fixed_or_existing_assets_feasible_operation`单列可行运行属性，不升级为联合投资全局最优。unknown也不会自动默认joint。

## 主摘要与阶段证据

新`summary`包含`solution_scope`、`solution_quality`和`solver_stages`，每阶段保存原目标模式/值、目标常数、状态/message、dual bound、gap、实际登记预算、耗时、整数变量数及数值/目标审核状态。两阶段保留一级与二级记录，一级可行值锚点与allowance单列。

两级证据都通过时仅标`two_stage_tolerance_limited_endpoint`；一级未证明时为`incumbent_anchored_secondary_not_global_lexicographic`。整体失败统一为`failed_endpoint`。`strict_global_lexicographic_optimum_claimed`始终为false，避免把数值容差和有限可行值界提升为严格数学词典序证明。

`objective_interval`仅绑定当次最小化目标：上界来自已审核可行incumbent，下界仅使用原始有效dual bound；未返回下界的LP保持lower=null，不根据status/gap造出下界。碳目标用`kg_CO2_solver_linear_expression`，不是元；费用目标用`yuan_model_objective`，不是自动认定的实际现金。目标常数另列，区间不偷偷平移为其他KPI。raw lower高于upper时保留原始字段、拒绝该区间，不裁剪修饰。

实际现金费用与碳KPI的独立扰动区间均标`not_separately_solved`，没有把gap乘碳、CAPEX、现金成本或其他指标。需要这些独立区间时，应另建明确目标与条件求解；本批没有虚构。

无可行解仍抛继承`RuntimeError`的`SolveFailure`并保留原中文message，新增`.summary`/`.solution_quality`供失败导出。二阶段失败也保留已经完成的一级证据。主线runner已接此异常摘要并加入新模块hash登记。

## 实际验证

最终定向回归：`focused_tests_release.xml`，86 passed；其中34项新增质量/旁表测试，52项原多目标、建筑、物理相关测试。覆盖碳界单位、固定投资、0新增但投资仍自由、高gap限时解、LP null gap、一级未证明、二级失败、整体失败端点标签、错误容差/界、旁表manifest身份、原输入不修改等反例。早先30/79/83项XML保留为开发过程记录，不累加为最终独立测试数。

最终新epoch示例在`new_epoch_examples_final/`，源自已准入公开RC原型/武汉天气/条件工业TOU数据，2880h完整时间序列；3组实际求解、4 solver stages，全部数值与独立RC审核通过：

|新运行|结果属性（中文）|优化范围（中文）|机器scope / 端点属性|
|---|---|---|---|
|existing_assets_cost|求解器登记容差内最优运行解|既有设备的逐时运行优化，未联合寻优新增投资|existing_assets_dispatch / not_applicable|
|fixed_plan_replay|固定计划内可行运行，且运行目标在求解器容差内最优|固定投资回放，不证明原投资方案全局最优|fixed_investment_dispatch / not_applicable|
|emission_endpoint|两阶段求解器容差内端点；不是严格全局词典序证明|既有设备运行碳端点，二级费用受一级可行值及allowance约束|existing_assets_dispatch / two_stage_tolerance_limited_endpoint|

该原型0项新技术投资，不能称新工程投资寻优优势。独立、不导入模型的`verify_reference_case_experiments.py`复算3组RC/舒适/源容量/调度/电量费/碳通过，见`independent_verification.json`与`comparison.csv`。`new_epoch_result_properties.csv/.json`只读连接独立数值表与新摘要属性，包含上述中文结果属性/优化范围，可用于后续方案决策表；不改独立复算CSV或实验原件。没有在公开理想原型上宣称现场设备、账户资格、电网可行性或全工程经济性。

`new_epoch_examples/`保留前一次源码阶段的示例快照。最终声明仅绑定`new_epoch_examples_final`协议与manifest；不回改早期稳定标记或哈希。

最终历史只读旁表在`historical_sidecar_final/`，覆盖指定4个根目录的192个结果记录：22条求解器容差内证据、154条缺证据、13条可行未证/容差未登记、3条失败。投资范围20 existing-assets、5 fixed、167 unknown。104个非solver聚合/preflight或重复failure附件明列跳过，27个不可访问目录记入`inventory_errors.json`；所以不是完整工程结果整树认证。

历史容差只在冻结manifest的summary哈希吻合对应protocol时补登；不能拿配置默认值猜测当次CLI参数。旧lex一级缺solver fun/完整阶段审核时保留missing，不把primary KPI anchor代作solver fun。全部已读历史输入前后SHA256一致。旁表不重新求解、不补物理证书、不替换旧summary。`historical_sidecar/`保留早一版属性模块审核快照，最后版本使用`historical_sidecar_final`。

## 可复现命令

工程根目录，使用本机登记的Python；新输出必须选择不存在的目录：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONPATH='src'
D:/app/anaconda3/python.exe -m pytest tests/test_solution_quality.py tests/test_multiobjective_consistency.py tests/test_building_model_integration.py tests/test_model_boundaries.py tests/test_physical_extensions.py tests/test_physical_extensions_independent_review.py -q
D:/app/anaconda3/python.exe scripts/audit_solution_quality.py --out docs/implementation_20261002_followup/solution_quality/historical_reproduction_01
D:/app/anaconda3/python.exe scripts/run_solution_quality_examples.py --out docs/reproductions/rc2_quality_01/examples
D:/app/anaconda3/python.exe scripts/verify_reference_case_experiments.py --base docs/reproductions/rc2_quality_01 --kinds examples --timeseries data/derived/evidence_case_20261002/wuhan_2024_2025_hourly_tou.csv
```

独立checker将核验结果写入其`--base`目录；上述命令显式使用新的复现base，保留本批根目录核验记录。再次复现时更换`rc2_quality_01`名称。发布清单必须携带新module与对应源码hash；旧冻结协议只用于其原有源码/数据身份。
