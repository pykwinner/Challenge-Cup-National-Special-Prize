# 06 GIS（OSM）——⚠️ 已被 `小挑/datasets/osm/` 取代

## 结论：以 `datasets/osm/` 为准，本节降级为交叉核对

2026-10-02 新出现的 `C:\Users\13349\Desktop\小挑\datasets\osm\` 是**三城市域范围**的完整 OSM 抽取；本节当时只做了三城**中心 0.12° 见方**的小窗口计数，范围约为其 1/100。

| 维度 | `datasets/osm/`（准） | 本节 06_GIS（降级） |
|---|---|---|
| 范围 | 全市域：乌市 86.79–88.97°E / 42.92–45.00°N；固原 105.32–106.95°E / 35.24–36.52°N；武汉 113.70–115.08°E / 29.97–31.36°N | 三城中心 0.12°×0.12° |
| 建筑 | 乌市 27194、固原 3531、武汉 56207，含 footprint_area_m2(100%)、levels、height_m | 仅有计数与设施标签 |
| 图层 | buildings / roads / landuse / pois / power / railways / water / boundary 八类 | 仅 facilities 一类 |
| 采集状态 | 完整 | **当时 Overpass 镜像不稳（kumi 反复断连），只跑到乌市建筑数 8944 就被中止** |

## 本节仅保留的交叉核对价值

- 本节乌市中心窗口建筑计数 8944 栋，可与 `datasets/osm/urumqi_buildings.csv` 按同一 bbox 重新裁剪后**互校**，用于判断 `datasets` 抽取是否遗漏。
- 本节同时抓了 `power=plant`、`man_made=chimney`、`landuse=industrial`、`building=industrial` 标签，可用于核对 `datasets/osm/*_power.csv` 是否覆盖同类要素。

## 引用限制（两节共同）

- OSM 是**众源数据（ODbL 许可）**，不是政府官方台账：中国境内建筑为志愿者补绘，**不等于实际栋数**。
- 竖向信息质量差异极大（见下），做体量/负荷分层前必须先查覆盖率。
- `datasets/osm/` **没有随附来源登记、抓取时间与许可说明**；本节的 `receipts` 与 `来源与适用范围_补充.csv` 可部分补上这一缺口。
