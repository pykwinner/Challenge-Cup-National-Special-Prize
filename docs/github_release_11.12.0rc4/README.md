# 11.12.0rc4 增量交付与实际副本验收

2026-10-02，Asia/Shanghai。**研究候选版**；交付批次11.12.0rc4，核心`__version__`仍11.12.0rc2。基线为`b29247d01aff188323ce1d9f658e2d31cc4a2f28`（rc3）。既有模型、配置、原数据与冻结结果保持身份。本次计划创建唯一`v11.12.0rc4`，最终commit、实际上传时间及远端核对登记在提交后交接，本文不编造尚未形成的SHA。

## 本轮新增内容和数据身份

按统筹最终白名单复制301份源文件、82,551,947字节：价格/运行矩配对权重候选与解析测试，三城同窗输入准备及3份逐时CSV，便携处理输入复算入口和38路径交付清单，三城调度/数值跟进/报告/独立核验4个脚本，原始协议、结果、图表、审查及两份主说明。发布层另纳入版本记录与实际副本测试证据；详细逐文件身份见[复制清单](validation/source_copy_manifest.json)。不复制临时运行库、pytest目录、绘图库缓存或其他未批准工作。

数据准备纳入6份NASA原JSON/receipt和4份context快照。三城共用2024-11-16 00:00至2025-03-16 00:00（Asia/Shanghai、右端不含）的120天/2880小时窗口，按同一公开BESTEST原型和共同湖北方法学电价做气候控制对照；不是三地各自完整法定供暖季、本地账户价或实测建筑负荷。字段来源覆盖63行，未继承价格/排放因子为城市新依据。原始气象和公开原型依据可追查，计算负荷/室温不能标为现场观测。

19件新增来源PDF/政府页PNG不分发，URL、SHA、字段适用性记录及排除清单保留。新增设备采购合同已闭合历史含税成套价与安装范围，**尚未闭合同型号多工况性能、适用末端和配电条件、包外改造/运维/寿命边界**，不准入为完整工程投资结论。三城旧数据缺口不能因为新材料入库就视为全部消除。

## 实际候选副本检查

验证对象为`github_candidate_20261002_rc4`，使用rc2发布时独立安装的CPython 3.12.7及[锁定依赖](../github_release_11.12.0rc2/requirements-release-rc2-lock.txt)。不使用原工程运行库；字节码写入关闭，OMP/OpenBLAS/MKL线程数为1，未启用加速。

| 本轮实际运行 | 数量 | 结果 |
|---|---:|---|
| 新配对权重解析/边界测试 | 59 | 通过 |
| 旧硬权重和方法身份相关测试 | 19 | 通过 |
| 三城输入/来源/日历 | 26 | 通过 |
| 气候调度接口 | 3 | 通过 |
| 便携复算：21小测试+一次真实2880h | 22 | 通过 |
| 合计测试 | **129** | **0失败/错误/跳过/排除/警告** |

前107项用时6.92秒，便携22项14.59秒。日志/XML保存在[validation](validation/)。本轮不重复或累计旧882项、rc3的92项。

此外在实际副本完成：三城source/calendar `--verify-only`；run01与refinement02两轮protocol/manifest的来源、代码和输出身份检查；[新的独立导出审核](validation/climate_export_audit_candidate.json)**160项全部通过**，9组0.1kW导出篡改全部拒绝。三城12行决策表、图的输入关系和文件SHA均复核，图与原结果未改写。发布前检查另记范围、版本、凭据模式、缓存与链接。

执行命令（`<prep>`为新验证目录）：

```text
python -B -m pytest tests/test_decision_weight_calibration.py tests/test_hard_weight_calibration.py tests/test_weight_adoption_method_identity.py tests/test_three_city_common_reference.py tests/test_three_city_climate_dispatch.py -q -p no:cacheprovider --basetemp <prep>/pytest_scoped --junitxml <prep>/scoped_tests.xml
python -B -m pytest tests/test_portable_reference_reproduction.py -q -p no:cacheprovider --basetemp <prep>/pytest_portable --junitxml <prep>/portable_tests.xml
python -B scripts/prepare_three_city_common_reference.py --verify-only
python -B scripts/run_three_city_climate_dispatch.py verify --out docs/implementation_20261002_followup/parallel_stage_02/climate_dispatch_run_01
python -B scripts/refine_three_city_climate_dispatch.py verify --out docs/implementation_20261002_followup/parallel_stage_02/climate_dispatch_refinement_02
python -B scripts/verify_three_city_climate_dispatch.py --run docs/implementation_20261002_followup/parallel_stage_02/climate_dispatch_refinement_02 --out <prep>/climate_export_audit_candidate.json
```

## 本版可展示的结果与具体边界

[12行决策表及图](../implementation_20261002_followup/parallel_stage_02/climate_dispatch_analysis_02/)包含三城各4项：预先登记的反馈基线、费用端点、碳端点和半额额外碳空间的中间点。连续状态、终端条件和服务约束保持一致。中间点保留最大能量电费节省的80.9177%–89.4424%，同时节費端点的能耗/碳及用电峰值可能增加；这些是固定理想已有资产、共同参考价下的公开原型调度结果，不是三城真实节费或自由投资效果。固定排放因子下能耗和碳是同一变化的不同单位。

初轮run01请求gap为1e-4；其中乌鲁木齐碳端点出现约0.2605kg劣于已知反馈可行值的容差现象。原结果保留，后续三城统一请求gap=0的refinement02保留独立协议、输入和代码身份，15求解阶段成功终止，最大返回gap约2.63e-15。这是数值收敛跟进；两阶段仍受主目标可行值锚定容差和求解容差约束，不能称精确全局词典序证明或新盲测。费用为能量电费，未含自由投资、配电/需量费等未闭合边界。

新配对权重`decision-moments-0.1.0-experimental`以价格和运行矩约束补充硬能量/最小W1，已验证解析可行、不可行及误差预算边界；**城市效果确认0组**，未替换三城主模型，不能用59测试宣称经验优势。方法需要先冻结新确认协议再做同服务对照。

审查记录指出run01基线和每城输入准备在局部异常捕获之前，部分启动失败可能无法写最终统一manifest；目前12行成功案例未触发，已作为非阻断限制登记，**本版没有修复**，不称全失败路径已覆盖。历史失败和不利指标保留。

## 便携复算与来源原件的分工

默认入口只接受原admission锁定且完全同SHA的6件处理后输入，逐字段/时间轴检查后数值复现；**沿用既有来源审核，本次默认未重新核验全部原件**。候选实际在不含PDF的输入隔离目录跑一次2880h并通过独立输出审核；0.1kW保存输出故障注入被拒绝，没有第二次求解。[隔离记录](validation/actual_portable_isolation_validation.json)、summary、manifest及独立小审计保留；完整实际输出留在发布准备目录，不把这些小记录的旁边误称完整输出树。上游交付白名单另保存其原始完整复算输出。

```text
python -B scripts/reproduce_admitted_reference.py --help
python -B scripts/fetch_release_sources.py --group reference
```

严格原件核验仍需取得[rc2外取清单](../github_release_11.12.0rc2/external_sources.json)中的原件。本次严格检查复用rc3已归档、同SHA的10件参考原件，0网络重下载，不纳入Git。公开克隆没有这些原件；动态URL若字节不同会拒绝，不改旧锁认可替代文件。新增设备来源join的本地PDF/截图审查不能称公开克隆包含所有原件或完成全离线来源复核。默认便携复算也不绕过新参数准入。

## 未运行项及版本登记

本轮未重复882全库、三城12条主求解、图重生成或新权重城市确认；前者因核心冻结且已完成相关回归，中间两项仅复核原冻结证据，最后一项尚待公平协议和数据准入。没有自由投资主案例、现场工程验证、论文或PPT更新；不以企业SCADA为公开原型实验前提，也不填未知费用为0收益。

`release_manifest.json`登记实际增量、SHA、命令、环境和未运行/排除原因，自身不递归哈希。提交后交接记录最终commit/tag/远端一致性；旧rc1–rc3证据与标签保留，此候选不标为正式版。
