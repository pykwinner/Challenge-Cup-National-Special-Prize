# 两栋建筑与共享配电的连续时序对照

本试验补充已经存在但尚未用真实绑定对照证明的建筑—系统耦合机制。只使用现有 `HeatPlanningModel`，不修改核心求解器，不输出现场节约或自由投资工程结论。

输入沿用已审核的武汉 2024-11-16 00:00 至 2025-03-16 00:00（右端不含）连续 2880h 气候和条件性湖北参考账户分时价格。其来源、账户限制和排除项见 `docs/implementation_20261002/data/admission_tou.json`。天气为 NASA 网格参考，设备为已有理想供热/制冷装置；费用仅供暖窗口电量费。

建筑 A 使用原 BESTEST600 派生 48m² 单区的 H=0.1043kW/K、C=0.535576kWh/K。建筑 B 明确为设计变体，面积同为48m²，H 为 A 的1.5倍，C 为 A 的4倍；不是实测建筑，也不是经验证的 BESTEST900。两楼各有0.2kW内得热，太阳得热排除。不同节点及各自设备保证供热、制冷和费用可以逐楼归属。

共同服务为每小时20–27℃、初始和最终20℃、冷热互斥。关闭柔性的消融把指定楼固定20℃，属于共同舒适合同内的更严格操作约束，不能说其控制可行域与柔性解相同。

共享配电容量在优化前按 `K=(H_A+H_B)*max(20-min(Tout),0)` 冻结，约6.4796375kW。使用 `portfolio_dispatch` 保留静态 firm 检查，避免把两台1000kW理想装置的铭牌相加当成实际运行需求。另先校验全部2880h固定20℃冷热策略的总负荷不超过K；不能只检查供热寒端而忽略暖时段制冷。

预先登记七个案例：A/B各自独立费用调度；双楼无共享上限；共享上限下双楼柔性；分别固定A、固定B、两楼均固定20℃。容量及变体参数不因求解结果调整。每案例120s、请求相对gap0；求解限时而有通过物理审计的解只能标可行近似，失败和无解全部保留。

建筑→系统证据要求：独立楼解拼接在同一配电上限下的实际越限小时、最大越限和累计超额，以及关闭楼宇柔性对系统账单/峰值的影响。系统→建筑证据要求：只加入共享上限后，每楼供能和室温轨迹变化，尤其实际配电绑定时段。静态 firm 等式不能替代运行约束绑定。若未观察到绑定、变化或不可行消融，如实记录负结果，不调整参数制造结果。

独立审计从每楼配置 H/C 和完整输入重建连续RC，检查初末状态、舒适、冷热互斥、楼—设备逐时能量、总配电进口、电费和平均因子CO2。独立审计不导入模型或模型自带RC/会计函数。固定排放因子使碳与电量成正比，本试验不新增独立小时碳优化主张。

执行顺序（工作目录为本工程，Python使用 `D:/app/anaconda3/python.exe`，设置 `PYTHONPATH=src;scripts` 和 `PYTHONIOENCODING=utf-8`）：

1. `python -B -m pytest tests/test_multiscale_reference_protocol.py tests/test_multiscale_reference_audit.py -q -p no:cacheprovider`
2. `python -B scripts/run_multiscale_reference.py freeze --out docs/implementation_20261002_followup/parallel_stage_03/multiscale/run_01`
3. `python -B scripts/run_multiscale_reference.py run --out docs/implementation_20261002_followup/parallel_stage_03/multiscale/run_01`
4. `python -B scripts/audit_multiscale_reference.py --run docs/implementation_20261002_followup/parallel_stage_03/multiscale/run_01`
5. `python -B scripts/analyze_multiscale_reference.py --run docs/implementation_20261002_followup/parallel_stage_03/multiscale/run_01`

冻结原输入、各案例配置/时序、协议及源码快照；结果包括原始状态、上下界、逐楼轨迹、独立审核、失败清单、图和分析表。已存在的目录不能覆盖；需要修正时另立版本并说明已暴露的结果。运行成本证明仅覆盖该有限模型及注册的数值容差，不证明工程投资最优。
