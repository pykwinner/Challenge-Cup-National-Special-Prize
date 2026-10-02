# 11.12.0rc3 增量交付记录

2026-10-02，Asia/Shanghai。状态：**研究候选版**。交付批次为11.12.0rc3，核心`__version__`仍为11.12.0rc2；原模型、数据、配置和既有冻结实验未改。基线为`66dff73128c16cefd3a62b97732fb1bebbc4865d`，保留旧提交及标签。计划使用唯一`v11.12.0rc3`标签，实际提交与远端核对见提交后交接。

## 本轮纳入范围

新增3份代表日完整季诊断、独立核验和报告脚本，1份24项专项测试；纳入`representative_season_confirmation/run_01`、`analysis_01`和原XML/JSON审计、故障注入、核验及勘误。排除`matplotlib_cache`等缓存。同步三城决策文档与followup说明，仅将两处新增成果状态更新到rc3并添加本记录链接。发布层另更新版本状态、CHANGELOG和本轮机器清单。

运行原理及全部结果见[三城决策文档](../source_intake_20261002/planning_user_update/THREE_CITY_DECISIONS.md)；原[rc2验收](../github_release_11.12.0rc2/README.md)和失败证据不覆盖。本轮不更新论文或PPT。

## 实际上传副本检查

验证目录为`github_candidate_20261002_rc3`。复用rc2发布时安装的独立CPython 3.12.7环境及[锁定依赖](../github_release_11.12.0rc2/requirements-release-rc2-lock.txt)，没有使用原工程运行库目录。

- 本轮专项：**24 passed**，1.53秒。
- 相关代表日、边界、采用规则、硬权重与方法身份：**68 passed**，1.57秒。
- 合计本轮运行**92项测试**，0失败/错误/跳过/排除/警告；与旧882项通过数分别记录。
- 独立审核：**567项来源、卷积RC、账单、权重及有限策略枚举检查通过**。最大温度差`2.4868995751603507e-14℃`；12组实际合格，0实际超帽，0估计可行集为空。

实际日志/XML和独立审核JSON保存在[validation](validation/)。执行命令：

```text
python -B -m pytest tests/test_representative_season_confirmation.py -q -p no:cacheprovider --basetemp <新目录> --junitxml <新XML>
python -B -m pytest tests/test_representative_days.py tests/test_representative_boundaries.py tests/test_representative_adoption.py tests/test_hard_weight_calibration.py tests/test_weight_adoption_method_identity.py -q -p no:cacheprovider --basetemp <另一新目录> --junitxml <另一新XML>
python -B scripts/verify_representative_season_confirmation.py --run docs/implementation_20261002_followup/representative_season_confirmation/run_01 --out <新审核JSON>
```

原工程首轮`analytical_tests.xml`包含1项导入收集错误（找不到`run_evidence_case_experiments`）；后续`analytical_tests_02.xml`与`analytical_tests_final.xml`均24项通过，三份均纳入，未删失败记录。原工程的0.1kW功率故障注入及20项数值拒绝记录保留在[negative_control_01.json](../implementation_20261002_followup/representative_season_confirmation/negative_control_01.json)，本次发布未重复注入。图已由统筹核查，本次没有重生成冻结图表、重跑8条完整轨迹或整套882测试；本轮主模型未变，已执行与新增诊断直接相关的回归。

## 可展示的算法结果

8条预先固定策略各运行2880小时，以同一8个实际日期切片评分，3权重×4预算形成12组选择、96条候选。负荷和室温为公开原型计算值；代表日继承各策略的完整季状态。费用与耗电均统计全部8策略：

| 平均绝对百分比误差 | 频数 | 软能量校准 | 硬能量/最小W1 |
|---|---:|---:|---:|
| 费用 | 0.763980% | 0.346432% | 0.117145% |
| 耗电 | 0.329620% | 0.114721% | 0.237849% |

本案例硬权重的费用估计更准确，软校准的耗电估计更准确。12组均选零预热，与库内可行最优费用差均为0；未显示策略选择优势。该诊断没有自由投资、样本外确认或仿真提速，不形成真实工程节费结论。固定电力排放因子使能量与碳帽等价，不能当两项独立实验。

[协议措辞勘误](../implementation_20261002_followup/representative_season_confirmation/protocol_semantics_erratum.json)解释易误读的旧键名。8份2880行轨迹实际均保存并受manifest约束，没有改冻结协议、脚本或结果。

## 来源与分发边界

本轮沿用rc2同一准入来源，无新增模型输入。冻结协议25项source身份和15项code身份核验完整；所需10件参考原件复用rc2已归档、同SHA文件，不重下载动态PDF，也不纳入Git。克隆后需按[rc2外取清单](../github_release_11.12.0rc2/external_sources.json)取得这些原件，方可执行相关准入审核：

```text
python -B scripts/fetch_release_sources.py --group reference
```

动态网址若无法返回同SHA，获取器会拒绝，不改旧锁认可替代字节。三城决策文档“本目录PDF审核文件”指本地历史规划审核；其6项审计附件及用户PDF未分发，也不是本轮数值实验输入。不宣称克隆后具备那些本地资料。

企业内部负荷/SCADA当前不可得，公开规划、标准原型及可追溯来源继续支撑研究；内部记录不作为本次上传前置条件。来源依据、算法数值核验与当地现场验证分别记录。

## 版本身份

`release_manifest.json`仅登记本轮增量文件、SHA、实际命令与未运行项；自身不递归哈希。提交后交接另记完整commit、日期、远端main及tag解引用，以实际已测试版本为准。此候选不标为正式版，不移动旧标签。
