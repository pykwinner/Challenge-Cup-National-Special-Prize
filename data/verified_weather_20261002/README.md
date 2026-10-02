# 已逐小时核对的 NASA POWER 气象产品

2026-10-02 从 NASA POWER 官方 API 获取并保存三城市（乌鲁木齐、固原、武汉）2020—2024 年共 15 份原 JSON。与原工程 CSV 比较 131544 小时、4 个气象字段，共 526176 个数值，全部一致；绝对比较容差为 1e-9，原文件未改。完整核对报告在 `../../outputs/competition_20261001/C0_weather_02/`。

`clean/` 仅导出对应原 JSON 的四个产品字段和原 LST 时间标签，不含合成需求、占用、电价、建筑 H/C 或空间数据。CSV 序列化后逐值回读核对，原响应、请求、单位、API 版本和 SHA256 可从 `clean_export_records.json` 追溯。

| clean 字段 | 原 API 字段 | 单位与含义 |
|---|---|---|
| source_time_lst | YYYYMMDDHH 标签 | NASA 当地太阳时标签；不是已转换的 UTC 或法定地方时 |
| outdoor_temp_c | T2M | ℃，2 m 高度温度产品 |
| relative_humidity_pct | RH2M | %，2 m 高度相对湿度产品 |
| wind_speed_10m_m_s | WS10M | m/s，10 m 风速产品 |
| solar_downward_energy_wh_m2 | ALLSKY_SFC_SW_DWN | Wh/m²，逐小时下行短波辐射量；本文件不沿用旧 CSV 的错误 kWh 字段名 |

原资料是格点气象产品，不称地面站或建筑实测。相同数值证明存档与本次官方产品一致，不证明当地微气候误差为零。跨数据集使用时必须另审 UTC/LST 与服务区间、平均窗口的约定，不能给 LST 直接贴 UTC+8 标签。

历史 2023 抓取记录只有 `reused_existing_file`，没有原请求 URL；本次以同城市 2024 记录的经纬度、字段及 LST 选项构建新的 2023 校验请求。补充计划见 `request_provenance_addendum.json`，这不恢复原抓取日期或 API 版本。首次武汉 2024 网络 SSL 失败保留于 `C0_weather/`，第二次验证全部完成，报告未覆盖失败记录。

来源：[NASA POWER Hourly API 文档](https://power.larc.nasa.gov/docs/services/api/temporal/hourly/)，NASA Langley Research Center POWER Project。每个下载 URL 与返回的源产品、版本、单位保存在原始响应及导出记录中。本数据包不为任何建筑参数、设备性能、电价或规划收益背书。

核查脚本：`../../scripts/verify_original_weather_archive.py`；导出脚本：`../../scripts/export_verified_weather.py`。导出包拒绝覆盖已有完成清单。
