# 实施接口与文件归属 v1

2026-10-01开始实际实施；基线11.11.1。总控负责model.py、io.py、linear.py、chronology.py、experiments.py、公共入口及根README；专项不得直接修改这些共享文件。先阅读上级docs中的评分复审、并行依赖、来源索引和参数清单。各专项在本目录交接，使用真实文件和测试结果说明进度。

## 首轮共同约定

- 正式主案例以S3武汉公开气象原型为首选，继承现有设备与网络；补3—4类合成建筑原型，明确其非实测身份。保留S1/S2兼容测试。
- 内部时间使用带时区UTC；占用、电价及供暖服务期按Asia/Shanghai生成后映射。功率kW为时段平均量；室温℃和SOC kWh为时段末状态；dt以小时计。
- 新顶层`building_thermal: {enabled: true, buildings: [...]}`。楼宇必填`name,node,heat_grade,area_m2,heat_loss_kw_per_c,thermal_capacity_kwh_per_c,initial_temperature_c,min_temperature_c,max_temperature_c`。得热字段`internal_gain_kw`/`internal_gain_column`；可选`min_temperature_column,max_temperature_column,solar_gain_column`。字段来源与假设由数据包另行登记。
- 楼宇按node/heat_grade分组。RC覆盖的node-grade，其旧demand_column只可用作参考/备用容量尺度，不重复强制旧固定负荷、DR守恒或免费计量节热。DHW若启用须单列固定服务。
- 新变量语义：`building_temperature`(name,t)时段末℃，`building_heat`(name,t)实际送达kW，`building_cooling`(name,t)实际冷量kW。一年内冷热进入同一RC状态方程；若改用季节模式，必须显式缩小验证声明。
- 建筑独立模块输出node-grade热量变量映射、node冷量映射，供总控替换主模型相应热/冷负荷侧；不重复创建热源或设备。初末态机制支持fixed/cyclic，按boundary_group承接，不把相邻代表日随意连起来；正式连续回放采用明确固定初态。
- 最小一阶RC精确离散：a=exp(-H*dt/C)，T_end=a*T_start+(1-a)*T_out+(1-a)/H*(Q_heat-Q_cool+gains)。温度与实际送达能量相连，短缺不能与室温脱节。
- 正式基准共用天气、面积、H/C、占用、舒适范围、冷热服务及初末态；改变技术配置或控制规则。明确一次能源输入/实际送达/参考负荷分母，不能沿用不适用的旧热量指标名误导。
- 建筑与储热可靠容量信用未能由关键时段状态证明时用保守值（新RC默认0建筑信用）。初始储能必须交代预充来源及末态要求。
- 新来源/网络预处理独立模块可验证温级、候选距离和楼宇归属；现有网络仍为静态kW输热。不得把kW代入质量流量、流速或迟滞公式。
- 成本/碳目标、碳帽和前沿采用统一总冷热边界；记录原有供热分量和制冷分量。常规预设ε点独立，端点与词典序阶段依序；反馈按原因回退，投资变更重验。
- 独立输出目录`outputs/competition_20261001/<role_or_run_id>`。成功输出最后写manifest；不能读取仍在写入的结果作正式证据。
- 每轮至少登记代码/配置/数据/投资身份、求解状态、gap、耗时、数值与物理判定。所有新增算法优势必须对应可复核比较。

## 首轮独立写入范围

| 角色 | 可写入 |
| --- | --- |
| C1 数据 | scripts/prepare_competition_case.py；data/competition_20261001/；tests/test_competition_data.py；本目录C1交接 |
| C2 建筑 | src/heatplan/building_thermal.py；tests/test_building_thermal.py；本目录C2交接及API说明 |
| C3 空间 | src/heatplan/spatial_quality.py；tests/test_spatial_quality.py；data/competition_spatial/；本目录C3交接 |
| C0 总控 | 共享核心接入、基线、状态、线程登记；与各角色协商后唯一修订本接口 |

专项可在专属目录跑轻测试，不启动完整季/年度大型MILP，不调用其他任务发消息。总控读取交接并派下一步。发现接口矛盾在交接文件具体说明并提出修正，不自行侵占共享文件。未经总控分配不继续扩展任务或创建子对话。
