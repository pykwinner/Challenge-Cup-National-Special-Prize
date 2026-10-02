# 温度—水力校核反馈优化：有限稳态模式参考实现

正式引用本轮 `run_02`。`run_01` 是审计身份校核加固前的探索结果，完整保留，但已被替代；对应原模块与入口保存在 `code_snapshot_run_01`，不把旧测试改日期充当新验收。

## 已落实的方法

新增 `src/heatplan/network_feedback.py` 包装原主模型。适用对象是同一固定热源直接连接多个用户的单向星状双管支路，各支路具有独立循环泵和明确几何。每个目录模式给出瞬时质量流量、热源供水温度和用户回水温度。输入不满足支持范围时拒绝运行。

每条供、回水管分别使用稳态指数降温。以相同单程长度和传热系数为当前边界：

\[
a=\exp[-UL/(1000\dot m c_p)],\quad
T_s^{far}=T_g+(T_s-T_g)a,\quad T_r^{plant}=T_g+(T_r-T_g)a.
\]

\[
Q_{in}=\dot m c_p(T_s-T_r^{plant}),\qquad
Q_{out}=\dot m c_p(T_s^{far}-T_r).
\]

`Qin-Qout` 同时计入供、回水两腿热损。泵功率按两腿 Darcy 压降、明确的局部阻力和泵效率计算；湍流摩擦系数解 Colebrook 方程，层流取 64/Re，过渡区拒绝。模式对应的输热效率及泵电耗系数进入原 MILP 的热平衡、电量、费用与碳账目。

每支路每小时最多一个运行模式，占空比在 0—1 内。另加“所有被选模式同时运行”的热源和电连接容量约束，防止小时平均负荷掩盖瞬时过载。这是保守可实施边界，不声称找到了最优的小时内错峰排程。

供回水温差超出目标带宽的部分，以独立的设计惩罚项进入目标；该项不是现金费用。末端最低供水、最高回水、流速、压降、泵功率和瞬时输热额定上限都是硬校核。使用中的模式不合格时，对该确切模式加入禁用约束，保留其他模式并自动重求解。切割有效性来自该模式的固定物理参数；不任意删除拓扑，也不用降低容量错误地修复低流量温降。

## 本轮实验与结果边界

- 使用已审核乌鲁木齐逐时气象的首个连续 72 小时；冷热时序不拼年、不补造观测。
- 两支路的需求来自已有公开 BESTEST 派生 H 与内得热的稳态计算，一条取其 0.5 倍，一条取其 1 倍。这是设计需求，不是建筑计量或室温实测。
- 管径、长度、传热系数、运行模式、水物性、限值、惩罚系数及有限理想电阻热源额定值均在冻结配置中明确为机制实验设计参数。它们不是八钢工程实参，也不冒称国家标准默认值。
- 费用沿用共同湖北方法学电价。费用范围为 72 小时参考电量费；温差惩罚另列，未计管网投资，不作当地账单、年度财务或工程节省结论。
- 两组实验均经过 3 次求解得到工程校核通过方案。最终活跃模式最低末端供水约 57.45℃，最高沿程压降约 237.96 Pa/m，满足本实验明确设定的 55℃、300 Pa/m 边界。
- 惩罚系数 0.2 的组与无惩罚组最终调度相同。该负结果保留，不能据此宣传温差改善。解析及独立反例另证明软惩罚会参与决策，同时不会放松硬温度约束。

本方法忽略冷启动、管道运输延迟及管壁蓄热；小时平均供热不能直接认证间歇脉冲下的真实室温。因此当前接口明确拒绝 RC 建筑、储能、共用泵压头、双向或网状管路、热泵 COP 联动、管网 N−1 和未适配的资产改造。原有动态建筑模型和多尺度实验分别保留；不能把两种近似未经检验地拼成一个完全验证的系统。

## 验收与复现

当前模块的 13 项常规测试和 61 项独立测试全部通过。独立验证包括两腿守恒、另一种 Colebrook 求根、两支路有效模式穷举、低流温降失败、高流压降失败、平均容量虚假可行反例，以及数据/状态/模式身份篡改。`independent_review.json` 绑定实际源码身份。

```powershell
python scripts/run_network_feedback_reference.py freeze --out <新的输出目录>
python scripts/run_network_feedback_reference.py run --out <同一输出目录>
python scripts/verify_network_feedback_reference.py --input <同一输出目录> --out <新的审核JSON>
python scripts/report_network_feedback_reference.py --input <同一输出目录> --out <新的图表目录>
```

正式 `run_02` 的保存解向量、逐时导出及切割记录通过 30 项复核，4 个内存篡改反例全部被拒绝。原主模型和旧水力校核源文件未修改，历史冻结身份保留。

## 方法依据

[Buildings 稳态管道热损文档](https://build.openmodelica.org/Documentation/Buildings.Fluid.FixedResistances.BaseClasses.PlugFlowHeatLoss.html)说明均匀边界、截面均温等假设下的指数热衰减；这里只采用稳态能量关系。[Modelica 管壁摩擦文档](https://doc.modelica.org/Modelica%203.2.3/Resources/helpWSM/Modelica/Modelica.Fluid.UsersGuide.ComponentDefinition.WallFriction.html)提供 Darcy/Colebrook 关系背景。

相关研究为 van der Heijde 等，*Dynamic equation-based thermo-hydraulic pipe model for district heating and cooling systems*，2017，DOI [10.1016/j.enconman.2017.08.072](https://doi.org/10.1016/j.enconman.2017.08.072)。该研究通过 Buildings 官方文档识别；本实现没有复现其完整动态管道模型，也不借用该论文的实验验证作为本项目的验证。
