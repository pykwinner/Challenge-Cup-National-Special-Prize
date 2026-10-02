# 第13页｜求解结构：稀疏MILP与程序验证计算记录

## 正文建议

- 以下规模、耗时和gap仅来自输入尚未准入的历史假设原型求解，作为程序运行证据；不作为本版真实工程成果。
- min cᵀx，l≤Ax≤u；投资与运行联立。采用稀疏矩阵，记录状态、gap、界和分段耗时。
- 代表期816h：37,559变量，38,584约束，143,718非零系数；端到端416.20s。
- 固定投资全年8784h：404,076变量，412,881约束，1,361,548非零系数。
- 全年solver_elapsed_seconds＝133.065s；求解与提取＝139.223s；profile端到端＝162.500s；实际gap＝0.13437%。

## 图表建议

建议以本页计时与规模表为主。若使用先前代表期计时图，图旁注明气象NASA POWER、建筑/价格尚未溯源、仅假设原型程序验证；不同问题的耗时不能当速度对照。

## 讲稿要点

代表期包含投资决策，全年是在固定投资下回放，两次问题不同，不能据耗时直接称算法加速。全部数字来自实际字段；solver、求解与提取、端到端的范围不同。原始记录有小幅计时差异时保留字段身份，不拼接多处数字。

## 对应评分与证据

多目标与收敛15分、工程10分：正式单目标代表期与连续年有状态/gap/规模/耗时记录；多目标端点与效率对照仍待证据。

[scipy.optimize.milp — SciPy 1.15.3 Reference](https://docs.scipy.org/doc/scipy-1.15.3/reference/generated/scipy.optimize.milp.html)；[HiGHS Parallelism — Development Documentation](https://ergo-code.github.io/HiGHS/dev/parallel/)。阅读范围与限制见研究来源登记。

[C0精简程序执行证据](<../../C0/program_execution_receipt_01.json>)；[C0精简程序执行证据](<../../C0/program_execution_receipt_01.json>)；[C0精简程序执行证据](<../../C0/program_execution_receipt_01.json>)

[C0精简程序执行证据](<../../C0/program_execution_receipt_01.json>)；[C0精简程序执行证据](<../../C0/program_execution_receipt_01.json>)。同一投资，全18项复核通过；楼宇审核限于时段末和声明范围，全矩阵依赖已保存数值审计，并非独立重建全部设备/网络方程。
