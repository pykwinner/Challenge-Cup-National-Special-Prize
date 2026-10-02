# 实验时间轴登记

记录原始文件 SHA、逐时顺序、物理步长和核算权重。相同时间轴只证明时间可对齐，不能代替数据、设备、经济条件或来源审核。

| 条目 | 类型 | 采样小时 | 加权核算小时 | 连续运行小时 | 用途 |
|---|---|---:|---:|---:|---|
| urumqi_common_120d | continuous | 2880 | 2880 | 2880.0 | 三城同原型气象对照 |
| urumqi_representative_8d | representative | 192 | 2880 | 不成立 | 代表日频次基线；其他方法的权重需另行登记 |
| guyuan_common_120d | continuous | 2880 | 2880 | 2880.0 | 三城同原型气象对照 |
| guyuan_representative_8d | representative | 192 | 2880 | 不成立 | 代表日频次基线；其他方法的权重需另行登记 |
| wuhan_common_120d | continuous | 2880 | 2880 | 2880.0 | 三城同原型气象对照 |
| wuhan_representative_8d | representative | 192 | 2880 | 不成立 | 代表日频次基线；其他方法的权重需另行登记 |
| wuhan_dynamic_120d | continuous | 2880 | 2880 | 2880.0 | 动态/多尺度/条件投资公共输入 |
| urumqi_network_72h | continuous | 72 | 72 | 72.0 | 有限模式温压反馈专用输入 |
| urumqi_heatpump_120d | continuous | 2880 | 2880 | 2880.0 | 真实样本性能映射/固定设备温控及费用LP的公共气象驱动 |
| guyuan_heatpump_120d | continuous | 2880 | 2880 | 2880.0 | 真实样本性能映射/固定设备温控及费用LP的公共气象驱动 |
| wuhan_heatpump_120d | continuous | 2880 | 2880 | 2880.0 | 真实样本性能映射/固定设备温控及费用LP的公共气象驱动 |

2880 h 表示已登记的共同 120 天窗口，不自动等于三城完整法定采暖期。代表日加权小时不是连续动态仿真时长；72 h 管网试验不能代替 2880 h 运行校核。

此登记覆盖清单中的输入；旧冻结脚本保持原状。详细 UTC 起止、每块时间范围与状态连续性见 registry.json。
