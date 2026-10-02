# 求解结论可信度增强与交接

本次新增可复用的结果属性分类层和只读报告入口，保留既有模型、求解器调用、导出格式、冻结数据和旧验证证据。分类只覆盖已登记的有限模型；没有新增现场验证、节费结论、完整 Pareto 前沿或严格数学全局词典序证明。

最终证据为 [report_final/README.md](report_final/README.md)、[机器表](report_final/evidence_table.json) 和 [HANDOFF.json](HANDOFF.json)。开发过程的 report_01–03、tests_01–03 留存以追溯修改；它们不作为最终分类结论。

## 已完成内容

- [solution_evidence.py](../../../src/heatplan/solution_evidence.py) 将实际范围、可行性审核、求解终止状态、主次阶段、同目标界与来源身份分开登记。校核失败、无 incumbent、时间受限可行、固定投资子问题与登记全问题分别处理。
- [report_solution_evidence.py](../../../scripts/report_solution_evidence.py) 仅读取指定 rc5 的代表性 manifest 和原始字段，检查 SHA、路径边界及扫描前后身份；输出新目录，拒绝覆盖及写入被检查副本。清单失败时 CLI 返回 2。
- 同一目标的常数同时加到 primal/dual；缺真实 bound 不补。保留原始 signed dual−primal、冻结残差和字面检查。数值一致性仅沿用 manifest 已登记的原审核容差；未经身份验证的容差无效。倒置原始数值对不排序、不截断，不能输出严格有序界。
- 两阶段分别登记 primary certificate、incumbent anchor、freeze allowance、原数值审核余量和 secondary certificate。完整整数候选枚举只支持主目标范围；次目标始终受选定候选及 incumbent 冻结域限制。
- 有限候选主目标误差预算逐项保留原候选 primary 审核余量、候选池余量、选定候选冻结余量和原冻结核算余量。此预算是既有容差的保守传播，未修改求解约束或登记新的 solver 容差。

## 实际验证

检查副本：`D:\competion\xiaotiao\github_candidate_20261002_rc5`，commit `7a4df3cbfc034d52a16a018eef6d6941858497f0`。覆盖 investment/run_03、investment/reproduce_02、multiscale/run_01、network/run_02、reliability/wuhan_2880h_01，以及 algorithm/release_compatibility_run_01；不扩展为历史全库审计。

最终报告检查 690 个输入文件身份，61 条记录，清单错误 0，扫描前后身份不变，冻结实验重求解 0：

| 分类 | 条数 | 可声明范围 |
|---|---:|---|
| optimal_declared_subproblem | 22 | 实际固定投资、既有资产或受限基准在原声明数值容差内求解最优 |
| primary_full_problem_secondary_candidate_subproblem | 38 | 登记有限候选全域的主目标容差证书；次目标只适用选定候选的冻结子问题 |
| verified_method_reference | 1 | 三城固定策略库及开发诊断证据，不是自由投资优化证书 |

72 个阶段存在正的字面 dual−primal 残差，最大约 `1.280e-9`；9 个端点存在正的字面 freeze 残差，最大约 `6.367e-11`。这些原始数字均保留，并通过来源已验证的原数值容差检查。报告没有把浮点残差等同物理可行性失败，也没有将数值容差证书写成严格数学精确界。

[针对性测试记录](tests_final.log)：`tests/test_solution_evidence.py` 与既有 `tests/test_solution_quality.py` 合计 **78 passed**。新增 44 项覆盖缺失/倒置界、失效容差、真实 MIP 目标界、时间限制、无 incumbent、校核失败、冻结越限、枚举不完整、来源篡改、路径逃逸、禁止覆盖和不改输入；其中含一个可解析的两变量整数规划实际求解。原有 34 项结果分类回归通过。没有重跑 2880 h、投资、多尺度、管网或策略库冻结实验。

执行环境为 Python 3.12.7、SciPy 1.15.3、pandas 2.2.2；没有另建干净环境，也没有将本次检查宣称为干净环境复现。主工程的 model.py、solution_quality.py、run_investment_reference.py、audit_investment_reference.py 与 rc5 相应文件 SHA 一致。

## 使用

```powershell
$env:PYTHONPATH='src'
python -B -m pytest tests/test_solution_evidence.py tests/test_solution_quality.py -q
python -B scripts/report_solution_evidence.py --evidence-root D:\competion\xiaotiao\github_candidate_20261002_rc5 --out docs/final_optimization_20261002/solution_evidence/new_report
```

输出目录必须不存在，且位于被检查副本之外。模块可由其他已建立范围、目标和来源身份的适配器调用；本 runner 当前仅适配所列 rc5 格式。分类信息没有自动接入旧导出，以保持现有 solver 语义和冻结结果不变。

## 文献依据与限制

SciPy 明确区分 optimal、limit、infeasible、unbounded 和 other 状态，MIP gap/dual 字段属于被最小化的线性目标；本实现据此保存状态和同目标数值，空字段保持空。[SciPy 1.15.3 milp](https://docs.scipy.org/doc/scipy-1.15.3/reference/generated/scipy.optimize.milp.html)

最小化的可行 incumbent 给出上界，dual 给出该模型下界；停止容差和浮点计算限制证书的含义。本报告没有独立重验 dual 向量，也不能把某个目标 gap 乘现金费用、碳排或 CAPEX 得到同时扰动区间。[HiGHS terminology](https://ergo-code.github.io/HiGHS/stable/terminology/)

有限 epsilon 网格提供候选点，不能自动覆盖完整 Pareto 集；本报告分别标记目标优先级和冻结域，不从有限扫描或 incumbent anchor 推导严格全局词典序结论。[Mavrotas 作者报告](https://www.gams.com/modlib/adddocs/epscm.pdf)

这是现有工程导出上的证据规则集成，不是未经查新证明的新优化理论。最终发布与版本登记仍由主线处理，本分工没有上传、提交或打 tag。
