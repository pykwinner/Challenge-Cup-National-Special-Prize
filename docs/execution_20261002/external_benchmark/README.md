# BDG2 外部计量代表日基准

先阅读 [已运行发现和边界](findings.md)。原始方案见 [protocol.md](protocol.md)，事后扩大诊断范围见 [posthoc_scope_sensitivity.md](posthoc_scope_sensitivity.md)；两者不混写为预注册结果。

在工程根目录，原 CSV 路径默认 `../../datasets/BDG2`，不需要安装或联网：

```powershell
& 'D:\app\anaconda3\python.exe' -m pytest tests/test_bdg2_external_benchmark.py -q --junitxml=docs/execution_20261002/external_benchmark/focused_tests.xml
& 'D:\app\anaconda3\python.exe' scripts/bdg2_external_benchmark.py freeze
& 'D:\app\anaconda3\python.exe' scripts/bdg2_external_benchmark.py run --core-label legacy --core-file docs/execution_20261002/external_benchmark/versions/legacy/scenarios.py
& 'D:\app\anaconda3\python.exe' scripts/bdg2_external_benchmark.py run --core-label new_core --core-file docs/execution_20261002/external_benchmark/versions/new_core/scenarios.py
& 'D:\app\anaconda3\python.exe' scripts/bdg2_external_benchmark.py report
& 'D:\app\anaconda3\python.exe' scripts/bdg2_benchmark_diagnostics.py analyze
& 'D:\app\anaconda3\python.exe' scripts/bdg2_benchmark_diagnostics.py verify
```

`freeze` 对已有锁只验证，不重选。第一次执行 `run` 会先拷贝 core 快照并写版本锁；本交付已有快照，复现使用快照而非变化的 live core。重跑固定版本会重写该版本结果和实际计时，完成全部命令后再核验；如要保留本轮实际计时，先在工程副本中复现。任何已锁输入/协议/脚本或运行环境版本变化均会拒绝。绝对路径锁用于本工作工程，移机需保留同一工程结构及指定的数据目录，不应把验证失败当成已复现。

- `selection.json`：12 通道、两年质量、稳定顺序、31 次拒绝。
- `protocol_lock.json`、`environment.json`：运行前锁定，不覆盖。
- `versions/<version>/`：源码快照、锁、48/72 条逐法结果、失败、原始三次计时、指标 CSV、run_manifest。
- `cases/<case>/<year>/`：原日期全年未补值日历、覆盖、退出日、实际日和日权重、真实时间爬坡事件及每法 JSON。
- `summary.json/csv`、`paired_comparisons.csv`、`version_comparisons.csv`：摘要与预算配对。
- `calibration_paired_comparisons.csv`：同选日校准前后成功和失败。
- `posthoc_scope_sensitivity.json/csv`、`diagnostic_summary.json`：事后范围诊断。
- `focused_tests.xml`、`delivery_check.json`、`status.json`：实际验证及进度。

数据来源沿用已有 [BDG2 原始证据链](../../source_intake_20261002/datasets_batch_03/bdg2_sources/README.md)。Miller et al., Scientific Data 7, 368 (2020), DOI `10.1038/s41597-020-00712-x`。CSV 来源许可与论文许可不同，继承既有审计的 Attribution-ShareAlike 条款，不以论文许可覆盖数据许可。本目录中的观测摘录保留 BDG2 来源/固定仓库版本和输入 hash，不能称中国本地实测。
