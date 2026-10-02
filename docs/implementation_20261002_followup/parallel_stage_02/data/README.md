# 三城同一公开原型：共同120天气候输入

2026-10-02，parallel stage 02数据准备。**乌鲁木齐、固原、武汉均已形成同条件物理驱动，每城2880行、21列；来源和日历验收通过，26项专项测试通过。** 本阶段不跑求解大矩阵，不生成当地实测负荷或新增价格/排放结果。企业内部SCADA不可取得，不是推进本研究的前置条件。

## 输入及复现

在工程根执行：

```powershell
& 'D:/app/anaconda3/python.exe' -X utf8 scripts/prepare_three_city_common_reference.py --verify-only
New-Item -ItemType Directory -Force -Path docs/implementation_20261002_followup/parallel_stage_02/data | Out-Null
& 'D:/app/anaconda3/python.exe' -X utf8 -m pytest tests/test_three_city_common_reference.py -q --basetemp docs/implementation_20261002_followup/parallel_stage_02/data/pytest_reproduce -p no:cacheprovider
```

首次创建才使用不带`--verify-only`的准备命令；可传`--source-dir <已有UTC原件目录>`。只读已有文件、不联网；完成后拒绝覆盖manifest。验收从本目录`evidence/`六份原JSON/请求回执副本重算，不需要外部原包绝对路径。外部原路径仅在首次来源读取中使用。原件副本SHA与原包一致，未重新下载、改写原数据或重贴来源日期。

| 交付 | 路径（工程相对） |
|---|---|
| 乌鲁木齐小时驱动 | `data/derived/three_city_common_reference_20261002/urumqi_hourly.csv` |
| 固原小时驱动 | `data/derived/three_city_common_reference_20261002/guyuan_hourly.csv` |
| 武汉小时驱动 | `data/derived/three_city_common_reference_20261002/wuhan_hourly.csv` |
| 准备/专项测试 | `scripts/prepare_three_city_common_reference.py`、`tests/test_three_city_common_reference.py` |
| 共同物理与比较协议 | 本目录`common_physical_reference.json`、`protocol.json` |
| 来源/小时字段/日历 | `source_inventory.json`、`field_provenance.csv`（63行）、`validation.json` |
| 拟确认窗口与文件锁 | `confirmation_candidates.csv`、`manifest.json` |
| 实际测试结果 | `tests.xml`，26 passed；首轮25通过、1项临时目录父目录不存在的环境错误，创建父目录后重跑通过 |

manifest锁13个已有来源输入、1个本阶段准备脚本和19个新输入/证据文件；文档与测试输出不当作气象原件。已有E0配置、CSV、准入、主README和冻结实验均未改。

## 共同窗口与源身份

三城均取北京时间`[2024-11-16T00:00+08:00, 2025-03-16T00:00+08:00)`，120个完整自然日，各24h。原UTC首键为`2024111516`，末键为`2025031515`。小时表显式保留`time`、`interval_end`、`time_utc`和`source_time_utc`，全部小时权重/概率/步长为1，不拆日重置，不拼年度、不填缺测。

此窗口是三城共同气候对照，**不是乌鲁木齐或固原完整当地供暖季，也不是三城行政采暖日历一致的证据**。武汉窗口沿用既有参考，不新增当地法规声明。

三份CSV各自是一份确定性对照输入，概率1不是三城联合概率；不要直接拼成一个模型的三情景。若研究联合场景，另立概率和边界协议。

| 城市 | 请求经度/纬度 | 返回Point经度/纬度/高程m | 原生覆盖 |
|---|---|---|---|
| 乌鲁木齐 | 87.6168 / 43.8256 | 87.617 / 43.826 / 737.35 | 三城均为UTC2024-10-09至2025-04-16，4560h |
| 固原 | 106.2426 / 36.0159 | 106.243 / 36.016 / 1847.81 | 完整覆盖共同窗口，无需改变日期 |
| 武汉 | 114.3055 / 30.5928 | 114.305 / 30.593 / 29.3 | 同上 |

原件是NASA POWER v2.10.2、MERRA2/SYN1DEG/POWER格点产品，**不是WMO站点或建筑传感器实测**；表中高程来自返回Point，不称地面站海拔。请求URL、返回header/units、原件和回执SHA、连续4560键及2880窗口覆盖均核。下载回执没有原取得时间，索引明确为null，不编造抓取日期。

`data/verified_weather_20261002/clean/`已有LST数据身份正确，但不能直接贴北京时间。本组使用既有`*_UTC.json`及相符请求回执；保留了LST说明、city_batch_02独立审计、三城决策及前次武汉方法协议的只读快照。CSWD典型年独立保留，未混入这批实际日历年产品；未使用未锁定交叉资料来做气象偏差修正。

## 同物理对象与显式推导

三城共用已准入BESTEST600派生几何：8×6×2.7m，48m²；H=0.1043kW/K，C=0.535576kWh/K（内侧材料集中化近似），内部显热0.2kW，显式排除太阳得热，硬舒适20–27℃、初末20℃、相同理想热冷容量1000kW和效率/EER1。原型高海拔渗透H项保持不变；天气替换不把它变为当地建筑或当地校准H/C，27℃仍是理想服务标签。

第一组只改变原生四个天气字段及城市标签。T2M是本阶段热模型的活跃天气驱动，RH/风/短波仍保存原生单位作元数据，不偷偷引入湿负荷、风致通风或太阳得热。短波是Wh/m²每小时，不能直接当kW得热。

```text
heat_reference_no_gain_kw = H*max(20-Tout,0)
steady_heat_demand_kw      = max(H*(20-Tout)-0.2,0)
steady_cooling_demand_kw   = max(H*(Tout-27)+0.2,0)
steady_temperature_c      = clip(Tout+0.2/H,20,27)
```

这些列是公开模型推导的容量/压缩锚或稳态参照，**不是当地热量表、不是已经求出的RC实际交付或室温轨迹**。没有把城市规划热负荷分摊给随机OSM建筑。后续连续1R1C运行由共同物理引用及这些源天气驱动，另冻结求解版本和结果。

字段表中的`NASA_<CITY>`对应`source_inventory.json`同名城市原件；`BESTEST_REFERENCE`对应已锁E0和共同物理引用；`COMPARISON_PROTOCOL`是显式实验规则。63条映射恰覆盖三份表全部21列，无未知默认列。

**小时表没有electricity_price、grid_emission_factor或基本费。** `common_physical_reference.json`不是完整经济求解配置，也不承接原YAML的价格/金融占位。不要将缺价格列直接交给会填默认价的经济优化入口；下一阶段先明确物理能量目标，或另冻结实际适用/明确共同方法价格协议。湖北参考价不能当另两城实际价，本阶段不作省级排放差异比较。

## 开发与确认身份

武汉这120天已用于前次公开原型、TOU和代表日策略诊断，是已知开发/诊断窗口。乌鲁木齐/固原相同日期**拟保留给新价格耦合方法确认，待算法使用记录与父端冻结核验**；本阶段不认证held-out、不称整个工程从未见过这些天气。算法开发侧应在承诺冻结前避免读取这两个窗口的负荷/策略/误差数值；如已接触，须记录后重新判定。

未来确认即使成立，也只验证同一公开原型在不同气候驱动下的方法表现，不是三城现场工程或当地建筑预测精度验证。TMY、当地全季及其他年度若另用，分别冻结协议和身份。

七项评分与权重保持不变。本阶段主要补S2动态仿真15（连续驱动验收）、S3场景完整15（三城同条件输入）和S7复现10（哈希/映射/拒绝错误输入）；其余S1架构20、S4多目标15、S5多尺度10、S6可视化分析15没有因准备数据而新增求解或工程效果证明。不预估得分。
