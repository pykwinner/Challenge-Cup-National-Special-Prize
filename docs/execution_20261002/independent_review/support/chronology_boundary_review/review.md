# 代表日与时序边界独立复核（2026-10-02）

只读复核核心源码，并运行明确标记的 24/48 小时解析夹具；未修改核心、原 CSV、旧审核证据，未 build/solve MILP 或运行工程测试。证据见 `fixture_results.json`，复算入口为 `recompute_boundaries.py`。结果只对该 JSON 中源码 SHA256 对应版本有效。

最新快照：representative `720adc5e96c6198787111b9333cdd2e25ad6b27b01f70c3ed2988e05fd0d3ade`；scenarios `5c18ae1a3a37a8f0adc1ec4ea810988848669be6fe56f4f532b1339db225e131`；chronology `5c7a883c757e38522e488b14d102fcc298f568ceb55f67c847a0773c7272186d`。复算前后五个核心文件哈希一致；此前快照保留为带哈希标识的 fixture JSON。

## 仍存问题与可实施修正

1. **P1：代表日接口与启用的 1R1C 不兼容。** `src/heatplan/representative.py` 的 `build_multiyear_representative_days` 在增加 `source_time` 后仍在 194–195 行把 `time` 改成 `year::...`，再在 201 行调用 `add_model_derived_anchor_columns` 构造 `HeatPlanningModel`。`src/heatplan/building_thermal.py:211` 仍要求 `time` 是真实、有时区的时间。48 个连续 UTC 小时的原输入通过 RC 校验，经过 builder 却直接报 `building time must be timezone-aware`。代表日选择在 `src/heatplan/scenarios.py:341`–342 再生成 `rep_...` 的标签，同样会破坏 RC 的时间接口。最小改进是将唯一标签与物理时间分列，并使 RC 的每个源年/连续轨迹有独立边界组；不能仅取消前缀而把不连续的多个年份合成一条 RC 轨迹，也不能削弱时间校验。验证：单年连续 UTC、非相邻两年、逐年情景束分别走到模型构造；每个代表日内部步长正确，时间标签与组身份清晰；无需求解。

2. **P2：选择与覆盖报告的真实爬坡口径不一致。** `src/heatplan/chronology.py:667`–685 的 `_maximum_grouped_ramp` 仍按组内行顺序 `.diff()`，仅择一使用 `boundary_group` 或 `scenario`，不检查真实时间或 `source_weather_year`。`representative_period_coverage_audit` 在同文件 785–786 行用它计算代表与全年爬坡。两组各 24 小时、分别来自 2020 与 2022 年的平稳序列，旧覆盖函数报告 100，新选择的连续小时 mask 报告 0。正常单年、排序后的连续全年路径不受这一跨年夹具影响。最小改进：覆盖的“观测连续边”采用与选择相同的时间与分组语义，但不强制覆盖输入必须为 24 行块；另列模型循环边。验证：跨午夜真实连续边、缺小时、缺整天、跨年份/情景、交错分组、整数 time + 真实 timestamp，并确认真实与人工循环边分别报告。

3. **P2：旧 chronology CLI 可接受与配置不符的均匀步长。** `src/heatplan/chronology.py:1439`–1447 的 `prepare_full_year_timeseries` 只检查时间间隔恒定，不核对配置 dt。`src/heatplan/chronology_cli.py:37` 直接调用它，41–43 行再校验与求解；非 RC 模式的一般输入校验不核对真实步长。夹具中的 24 条均匀 2 小时观测被接受，在 dt=1 的配置下会使用 24 小时成本/能量权重及 1 小时动态，而真实观测覆盖 48 小时。年度 `annual_replay_preflight` 会拒绝该夹具，故不能表述为正式年度门控整体失效。最小改进：helper 接收期望步长，CLI 显式传入配置 dt，或 CLI 先调用 chronology 质量门。验证：dt=1 输入 1h 通过、2h 拒绝；dt=2 输入 2h 通过；重复、缺值、倒序整理和缺小时给明确结果。

4. **P2（声明边界）：年度平均 API 未证明输入是完整年度。** `src/heatplan/representative.py:180`–181 仅检查非零的 24 倍行数。24 小时输入、遗漏整天的两块 24 小时输入、06:00 开始的 48 小时输入都被接受，输出权重分别为 1、2、2 天。新 mask 已能拒绝块内缺小时，却不会因此证明完整年度或自然日对齐。既有 `tests/test_multiyear.py:264` 后特意使用两天小型例子，应保留演示兼容；建议让 API 明示“多段完整 24 小时块”，正式年度研究另加 strict scope/完整年门槛及审核身份。平均权重公式本身正确：sum(weights)=sum(N_y/24)/Y；只有所有输入为真正完整年时才等于平均年天数，例如常年与闰年为 365.5。验证：365/366 天及明确供热年度、缺整天、错年标签、自然日相位、小型演示模式分别声明通过条件。

## 已修及应保留的限制

- 新选择逻辑排除跨气象年、跨边界/情景及不连续时间的假边。新增 timestamp 优先级后，整数 time + 真实 UTC timestamp 的 48 小时夹具有 47 条边，跨午夜边 23→24 为 True；上一快照的 46 条边问题已修。退化聚类和权重数值问题不在本子审查完整验收范围。
- 代表日分别使用 `boundary_group`，模型前驱在 `src/heatplan/model.py:116`–118 组内循环。两个 24 小时日的首行前驱分别为 23、47，没有日间链接。储热循环状态在 1426–1443 行，RC 状态的固定/循环边界在 `building_thermal.py:495`–508。权重改变年度能量与成本，不改变日内状态递推；损失为零时，对每个代表日求和分别要求充放能量平衡，因此不能把季节盈余转移给另一代表日。
- 单日 0→23 的平滑序列，覆盖内部 ramp 为 1，但模型循环末→首边为 23。这一边是建模产生的周期假设，不应包装成真实测得的爬坡。保留跨午夜相邻两日为锚点，也不等于已加入那两个日之间的运行约束。
- 正式全年回放 `prepare_full_year_timeseries` 写一个连续 `full_year` 组（chronology 1448–1452）；其年度 preflight 检查真实配置步长并拒绝缺日（1397–1400）。显式年度范围另检查有时区、完整连续且精确的一年半开窗口（1328–1367）。这些是已有真实时序保护，不是 Kotzur 季节 inter/intra 状态分解的实现证据。

建议优先落地：物理时间接口兼容；统一真实边口径并补旧 CLI 步长门；正式年度身份与演示输入分开验证。季节状态分解属于后续新功能，当前审查不要求扩展实现。
