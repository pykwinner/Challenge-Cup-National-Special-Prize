# BDG2 权威来源、计量口径与适用性审查

BDG2 cleaned 核心10个CSV已齐，且全部与原作者仓库固定提交的LFS内容身份一致。中国三城项目校准数据仍未齐。该结论只完成来源、文件身份与适用性审查，未执行独立预测/热模型验证、优化或改写模型。

仓库提交 `9b97ccbe90096aff42ed4fd6493bf7ae692d7118`（2023-10-14T11:26:45+08:00），wiki提交 `87246a4d9f2d5c685a4c8f6e3c4ca7910f4dabf2`。Git API tree取得成功，blob请求遇限流；普通公开git稀疏下载只取得指针及小文档，LFS下载关闭。指针Git SHA已对上官方tree，再用指针中的SHA-256/字节数对主线程完整读取的本地10文件哈希，10/10匹配。没有重复下载大表，也未重复扫描8大表。

全表读取统计归主线程[bdg2_integrity.json](<D:\competion\xiaotiao\北方供热三类场景公用总框架V10\north_heat_challenge\docs\source_intake_20261002\datasets_batch_03\bdg2_integrity.json>)；本站记录其SHA以界定版本。原作者说明与本地表头对应1636栋、3053表计、19站点、2016—2017每表17544小时；metadata实际32字段，weather实际10字段。所有8个计量表的building_id表头与metadata相应Yes标识集合完全相同。

| 表 | 通道数 | 单位/小时区间 | 计量边界 |
|---|---:|---|---|
| electricity | 1578 | kWh | 全楼电能；未分离采暖电耗 |
| hotwater | 185 | kWh | 热水/供热能量；空间供热与生活热水边界未逐表确认 |
| chilledwater | 555 | kWh | 冷水供冷能量；不是制冷机电耗 |
| steam | 370 | kWh | 蒸汽能量换算；不是逐站实测焓差 |
| gas | 177 | kWh | 燃料输入能量；不是输出热量 |
| solar | 5 | kWh | 太阳能能源计量；不是辐照或可用供热功率 |
| water | 146 | L | 用水体积；不是能量 |
| irrigation | 37 | L | 灌溉体积；不是能量 |

原论文明确每行是该小时能量/体积之和，不能直接称瞬时kW；1小时平均功率可以用kWh÷1h得到。BDG2 raw/cleaned已按作者说明归一，不能再套Kaggle原单位转换。气体输入转热输出还需本设备效率与用途；热水/蒸汽也不能不查连接关系就相加。

论文初始处理包括单位归一、单值/重复表删除、负值置NaN及相应剔除、连续缺测超100天表剔除、对数域极大离群值剔除、4位小数。cleaned另声明S-H-ESD离群值、长于24小时的连续零值、电表全部零值置NaN。NaN包含删值，不全是原始缺测，不能补0；非电表零值可是真实季节停机。未证明插补或传感器计量校准。

weather来自NOAA/NCEI ISD-Lite就近可用站。关联键是计量表列building_id→metadata.site_id→weather(site_id,timestamp)。天气与表计均为当地时间标签；作者天气字典明确忽略DST，不能按Kaggle天气UTC口径平移。US/Eastern也用于加拿大站，时区别名不能证明国别。原文未给逐表时钟变换或时间标签为区间起/末。

天气量纲：气温/露点°C，云量oktas及源天空编码，1h/6h降水mm，海平面气压hPa，风向度、风速m/s。无独立相对湿度或太阳辐照列；露点派生湿度应另标派生。天气表331166行，较19×17544少2170个小时键，另有128条气温空值。Crow/Moose的2017年有效气温8246/8760=94.13%；只按现有行算100%不代表全年完整。

公开位置可识别美国7站、加拿大2站、英国4站、爱尔兰1站；其余5匿名站不凭US时区别名补国家。全部是海外数据。论文表3给14个公开站的ISD代码，CSV自身未带station_id；代码属于作者关联声明，本轮未另取NOAA原始序列逐值比对。19站的城市、国家判定依据、时区、气象缺口及站号见audit.json。坐标是城市/园区中心，不是逐栋GIS。

需要保留的来源问题：论文Table1把Eagle/Peacock的数量及Kaggle编号与当前metadata/wiki对调；Wolf经度+6.2603与都柏林位置不一致，均未自动修正。正式论文Table5的MJ及加仑转换方向有疑点，不应对已归一CSV再执行。当前notebook10还存在files[0:1]、离群函数回读raw、输出_cleaned1等行为，未认证为8个发布cleaned文件的完整复现流程。

已补的是来源身份明确的海外表计与配套天气基准、面积/用途等部分建筑描述，以及可设计的按建筑/站点、2016训练—2017留出验证条件。未补中国三城真实负荷、室内温度、几何/材料/U值/通风/热容、型号化COP、管网/配电、储能损失寿命及项目价格/合同。主线程727个热/冷通道初筛是候选通道数，既不是727栋独立建筑，也不是已完成性能验证。

数据仓库LICENSE原题为“Attribution-ShareAlike 4.0 Unported”；论文本身为CC BY4.0，不能用论文本身许可覆盖仓库数据。引用Miller等2020 Scientific Data7,368及DOI，同时保留仓库固定提交、访问日期2026-10-02、筛选通道与缺测/派生处理说明和LICENSE。论文对附属元数据的CC0说明不自动认证本地metadata.csv另行豁免。

逐条证据文件、SHA、原文表/代码定位、身份比对和未填缺口见[audit.json](<D:\competion\xiaotiao\北方供热三类场景公用总框架V10\north_heat_challenge\docs\source_intake_20261002\datasets_batch_03\bdg2_sources\audit.json>)。权威来源：

- [原作者仓库](https://github.com/buds-lab/building-data-genome-project-2)
- [正式论文](https://doi.org/10.1038/s41597-020-00712-x)，已取得[Europe PMC正式全文XML](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC7591488/fullTextXML)
- [计量表数据字典](https://github.com/buds-lab/building-data-genome-project-2/wiki/Meters-data-features)
- [天气数据字典](https://github.com/buds-lab/building-data-genome-project-2/wiki/Weather-features)
