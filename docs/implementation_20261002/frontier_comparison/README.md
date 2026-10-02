# 有界运行费用—碳对照（E5补充）

公开BESTEST派生单建筑RC原型、武汉天气与条件工业TOU电量价。2880小时完整供暖季；不是中国三城现场验证。仅计电量费与固定平均因子CO2，不计全工程成本；0项新技术投资，点之间改变调度而非工程结构。

λ给排放权重。预冻结λ=[0,0.25,0.5,0.75,1]；内点目标系数p=λ/(1−λ)·ΔC/ΔE，属于研究权重，不是市场碳价。归一范围来自已有双阶段端点的实际电费与CO2；端点容差保留。λ=0/1复用双阶段端点，共4 solver stages；加权内点另3次求解，既有ε另5次，均120s/stage、gap目标1e-4。求解次数不同，不能称等预算加速。

账本从source_heat和cooling_output分别按效率/EER换算用电，再逐小时乘原始TOU与排放因子。所有点校验objective_yuan=C+p·E，且独立复算RC、温度、边界、容量与调度时序。端点第二阶段的solver_objective_value可能是排放，不可直接当元；图纵轴只使用独立电量账单。

|方法/点|λ|研究权重 元/kg|电量费 元|CO2 kg|gap|全部阶段 solver秒|记录wall秒|
|---|---:|---:|---:|---:|---:|---:|---:|
|shared_endpoint/cost_endpoint|0|0.00000000|2265.428738|1284.746473|0|4.592590|7.104271|
|shared_endpoint/emission_endpoint|1|极限端点|2305.821944|1230.593287|0|1.769041|3.995591|
|epsilon/epsilon_0|—|0.00000000|2305.821968|1230.593275|0|0.855120|2.855440|
|epsilon/epsilon_1|—|0.00000000|2282.810840|1244.131596|0|5.875927|7.822896|
|epsilon/epsilon_2|—|0.00000000|2269.694089|1257.669893|3.58435e-07|9.565693|11.676466|
|epsilon/epsilon_3|—|0.00000000|2266.935600|1271.208189|0|9.665457|12.549516|
|epsilon/epsilon_4|—|0.00000000|2265.428737|1284.746486|0|12.136338|14.226831|
|normalized_weighted/lambda_0_25|0.25|0.24863546|2268.484120|1260.954787|0|0.924035|2.625372|
|normalized_weighted/lambda_0_5|0.5|0.74590638|2271.773600|1254.033959|0|0.865166|2.626295|
|normalized_weighted/lambda_0_75|0.75|2.23771915|2305.821968|1230.593275|0|0.777846|2.611592|

## 观察到的覆盖

加权5点最大相邻归一碳间隔：0.43934047；既有ε5点：0.25000046。详细位置、范围与加入共享端点后的指标见comparison.json。只比较这批样点，未证明完整Pareto前沿，也不预设方法普遍优胜。

共享端点solver合计6.361631s；新增加权3次2.567048s；既有ε5次38.098535s。记录wall分别11.099862/7.863260/49.131149s；独立重算/绘图与重复验证耗时另列或不属于solver时间。历史ε与本次加权不是同时计时。

失败记录：0项。协议、源码快照、原始来源与既有结果hash见protocol.json/protocol.lock.json；未改原结果。manifest.json覆盖本批输出。

## 复现

工程根目录，使用D:/app/anaconda3/python.exe：

```powershell
$env:PYTHONIOENCODING='utf-8'
D:/app/anaconda3/python.exe scripts/compare_reference_frontier.py self-check
D:/app/anaconda3/python.exe scripts/compare_reference_frontier.py verify
# 新求解必须新目录；不覆盖本批记录
D:/app/anaconda3/python.exe scripts/compare_reference_frontier.py freeze --out docs/implementation_20261002/frontier_comparison/reproduction_01
D:/app/anaconda3/python.exe scripts/compare_reference_frontier.py run --out docs/implementation_20261002/frontier_comparison/reproduction_01
D:/app/anaconda3/python.exe scripts/compare_reference_frontier.py verify --out docs/implementation_20261002/frontier_comparison/reproduction_01
```

来源准入仍有账户资格、电网接入、设备效率、无太阳增益、初末20℃预处理等限制；研究加权目标不升级这些限制。未重跑842项主线回归，本任务未改src/tests。
