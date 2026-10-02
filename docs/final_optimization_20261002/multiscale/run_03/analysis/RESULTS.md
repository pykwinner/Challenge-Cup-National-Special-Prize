# 纯热容与公用电上限的完整时序对照

四案均使用同一武汉真实网格气象与已登记公共原型参考。A保持H=.1043、C=.535576；B固定H=.15645，仅将C从.535576改为2.142304 kWh/℃。C倍率是研究设计轴，不是新增实测材料或BESTEST900建筑。所有案同样20–27℃、初末20℃、现有理想设备及2880小时天气；第二轴仅移除或保留6.4796375 kW公用电上限。

| 案例 | 120日参考电费/元 | 用电/kWh | 固定平均因子CO₂/kg | 峰值/kW | 实际绑定小时 |
|---|---:|---:|---:|---:|---:|
| C1_unlimited | 5918.140565 | 8155.108595 | 3297.925916 | 14.106374 | 0 |
| C4_unlimited | 5490.938905 | 8725.440239 | 3528.568033 | 25.333513 | 0 |
| C1_shared | 5937.643314 | 8050.607557 | 3255.665696 | 6.479638 | 125 |
| C4_shared | 5660.964848 | 8386.067389 | 3391.325652 | 6.479638 | 530 |

在共同电上限下，单独增大B热容的电费变化（C4−C1）为-276.678466元，用电变化335.459831kWh。无共同电上限下相应电费变化为-427.201660元；两者差异即条件交互150.523194元。这里保留能耗与碳的真实变化，不将省费、削峰混同节能。

费用、逐时源/网约束与每个建筑的完整历史解析RC卷积独立核验；另保存完整变量向量，重建矩阵核对所有约束、边界和整数以及导出一一对应。独立热量账本解析积分每小时温度，核对净送热+增益=围护热损+库存变化，不使用终点温度矩形近似。审计本身不重新求解或另行证明最优性。

文献到本轮实现的对应：

- [A generic quantification method for the active demand response potential of structural storage in buildings](https://publications.ibpsa.org/proceedings/bs/2015/papers/bs2015_2475.pdf)：Hold H, gains, weather and service fixed; vary C alone; report bill, energy, CO2, inventory and true hourly shared-power constraints. 读取范围：Original IBPSA proceedings full text, pp1986-1993, read2026-10-02

- [On the lumped capacitance approximation accuracy in RC network building models](https://www.sciencedirect.com/science/article/pii/S0378778815302930)：Distinguish exact solution of registered1R1C equations from evidence that1R1C is an accurate actual building model. 读取范围：Publisher abstract/highlights from search2026-10-02; full text not obtained

- [Aggregation and Data Driven Identification of Building Thermal Dynamic Model and Unmeasured Disturbance](https://arxiv.org/abs/2001.09141)：Retain two individual temperature states and audit each, rather than claim an equivalent aggregate RC model. 读取范围：Authors' original arXiv abstract read2026-10-02; not a fresh full-text reproduction

- [Towards Exact Temporal Aggregation of Time-Coupled Energy Storage Models via Active Constraint Set Identification and Machine Learning](https://arxiv.org/html/2510.14451v1)：Keep the complete continuous2880h chronology; do not transfer an exact-TSA guarantee to this binary heat/cool model. 读取范围：Original HTML sectionsIII-IV read2026-10-02

这是既有1R1C方程内的设计轴因果对照，不能证明真实建筑的RC近似误差、聚合等效性、设备实测性能、三城普遍规律、联合投资最优或项目利润。计费仍是条件性120日能源费，碳为固定平均因子参考。保留两个独立建筑状态，未实现建筑聚合识别或精确时间聚合；Klatzer预印本的先验活跃集/对偶条件与尚待证明部分不会转化为本项目保证。
