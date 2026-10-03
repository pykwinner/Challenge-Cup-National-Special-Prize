# C4 历史数学场景证据

本目录汇入已有的武汉四楼、两区域数学场景结果，服务年为北京时间 2023-07-01 至 2024-07-01（8784 小时），声明冬季为 2023-11-15 至 2024-03-16（2928 小时），均含起点、不含终点。冬季是项目设定的服务窗口；热工、得热等采用合成参数。它们支持模型时序与约束检查，不是武汉全城或现场测量验收。

`optimized_annual_01/` 和 `optimized_season_03/` 保留各自的配置、输入时序、投资、完整调度、逐楼轨迹、交付层级、原生解和既有检查。`frozen_source_epoch_01/` 为原计算代码快照，独立于当前仓库核心版本。`inputs/` 保留服务范围及案例配置。

CSV 仅作无损 gzip 压缩；原文件 SHA-256、原路径、压缩后位置及哈希见 `ARCHIVE_INDEX.json`。例如：

```python
import gzip
from pathlib import Path
p = Path('optimized_annual_01/building_trajectory.csv.gz')
raw_csv = gzip.decompress(p.read_bytes())
```

解压后按清单恢复原路径即可核对历史 manifest；不要把其中旧绝对路径当成本机路径。原记录和失败/准入状态未改写，年度数值通过不等于来源准入或真实工程认证。本次发布只复核保存结果和身份，没有重新求解这两案；没有将固定投资回放称为已证明的全局词典序最优。
