# 11.12.0rc2 候选发布记录

日期：2026-10-02，Asia/Shanghai。目标仓库 `pykwinner/xiaotiao`，分支 `main`，状态为研究候选版。计划里程碑标签 `v11.12.0rc2`；实际创建及远端身份以提交后交接记录为准，不将计划标签写成既成发布。

上一代码上传为 `4722991341334d39c129eec8ad1d2c551eca36c8`；本次直接基于已补充资料的远端 `93b96447330f9aa7a03d143a72aa6deb9716a38f`，保留三个数据提交及471项新增资料。原工程和上传副本不自动同步；本次验证目录为 `github_candidate_20261002_rc2`。

## 本次变化

- 同步已冻结的模型、算法、脚本、测试、参考配置与选定证据：硬能量约束下的代表日权重、RC实际冷热服务、统一服务排放、共享容量、固定投资回放及求解结果范围标签等。具体行为与限制见 [CHANGELOG](../../CHANGELOG.md)、[完整实施记录](../implementation_20261002/README.md)和[跟进记录](../implementation_20261002_followup/README.md)。
- 纳入2880小时平价/条件性TOU参考实验、约束与加权法对照、两批BDG2确认、三城共享候选走廊、旧失败证据与各代码时期。历史数字仍绑定原始锁和脚本，不改日期冒充新实验。
- 增加官方来源外取器、完整候选依赖、14行来源位置映射和旧OSM输入附件。5份原锁字节码仅作历史身份附件，其余缓存与运行库不分发。

## 实际上传副本验证

在新建的CPython 3.12.7独立环境安装 `requirements-release-rc2.txt`，没有引用原工程的 `_runtime_packages`；`pip check`通过。完整实际安装版本见 [依赖锁](requirements-release-rc2-lock.txt)。

最终回归：**882 passed，26 subtests passed，2 deselected，2 warnings；0失败、0错误、0跳过；71.15秒**。JUnit的`tests`总计908，XML含882个`testcase`主项；子测试不能混计为另一份独立实验。命令：

```text
python -B -m pytest tests -q -p no:cacheprovider -k "not test_approval_and_first_round_entire_tree_unchanged and not test_final_manifest_hashes_and_approved_identities" --basetemp=<新临时目录> --junitxml=<新结果文件>
```

排除的两项是旧探索目录整树、当时源码与审批快照的完全身份断言；本版维护后不满足“当前整树未改变”的前提，测试及旧证据仍保留，不计入通过。两条警告来自专门保留旧算法空簇失败的反例测试：空均值和无效除法。最终日志/XML保存在本目录 `validation/`。

首轮候选回归曾为864通过、6失败、12错误、2排除、26子测试通过；原因是外取原件尚未提供、两个历史RC快照未复制，以及followup旧源码定位不兼容。统筹修复历史定位并保全原脚本，发布侧补齐附件后再次验证。首轮失败日志/XML一并保留。

额外实际核验：

- 5份BDG2原CSV和GAMS方法PDF从官方地址重新下载，**6件SHA-256与大小全部匹配**。其余10件来源原件用已归档、同SHA的本地原件供本次验证；没有声称本次从动态网址重获成功。
- 外取清单16件全部核对SHA与大小；平价及TOU准入检查通过。`full_economics_admitted=false`，实际账户资格仍未验证。
- 新便携入口从CSV复算软权重9栋/36条、硬权重6栋/30条及6个LP见证，全部通过。不是重跑KMeans、重新选样或再测算法速度。
- 外部RC两批144指标独立复算通过，没有重新拟合。原输出与历史源码快照身份保留，新的计算脚本另记身份。

本次没有重新运行全套规划求解、所有三城年度实验或旧整树审计，也不把历史610检查、842回归数字改为本版新验证。

## 克隆后的数据与复现

安装候选依赖后，在仓库根目录执行；所有输出选新位置，勿覆盖冻结证据：

```text
python -m pip install -r requirements-release-rc2.txt
python -B scripts/fetch_release_sources.py --group bdg2 --group method
python -B scripts/fetch_release_sources.py --group reference
python -B scripts/fetch_release_sources.py --verify-only
python -B scripts/verify_bdg2_soft_confirmation.py --data-root data/external/bdg2 --output-file <新目录>/soft.json
python -B scripts/verify_bdg2_hard_confirmation.py --data-root data/external/bdg2 --output-file <新目录>/hard.json
python -B scripts/reproduce_external_measurement_studies.py --output-dir <新目录>/external_rc
```

[外取清单](external_sources.json)保留来源URL、固定BDG2提交、许可、大小和SHA。只有字节匹配才准入；动态网站失效或返回变化会拒绝获取，不改旧锁“认证”新内容。未取得10件参考原件时，与其相关的准入检查和部分测试不可直接运行。39份历史原文页面截图未分发：重新渲染不能保证旧截图字节，亦不作为数值核验前提。

BDG2数据许可沿用原仓库 [LICENSE](https://github.com/buds-lab/building-data-genome-project-2/blob/9b97ccbe90096aff42ed4fd6493bf7ae692d7118/LICENSE)，保留作者、DOI与固定版本；不以论文CC BY许可覆盖数据许可。旧完整实验脚本仍保留本机路径和环境限制；新便携入口交付的是独立数值复算，不宣称整个旧实验已跨平台重跑。

`data/external/osm_original/`附24份旧OSM CSV及SHA索引，署名 © OpenStreetMap contributors，依据[ODbL](https://www.openstreetmap.org/copyright)。旧查询、下载日期和导出转换尚缺，归档字节一致不等于出处链补齐。新原生响应和共享图分别保留；道路走廊不能当真实竣工热管。

[PORTABLE_SOURCE_MAP.csv](PORTABLE_SOURCE_MAP.csv)将原14行本机定位映射为仓库内位置，原来源索引不改写。宏观材料、境外表计、规范参考和本地项目观测分别列范围；仍需任选三城一个实际项目的同步热输入、室温、设备记录以及建筑、网络和账单边界，才能完成当地标定和工程经济验证。

## 身份与上传核对

`release_manifest.json`记录实际纳入文件的SHA-256，清单自身不递归记自身；旧基线与本轮失败证据保留。最终40位commit、上传时刻、远端main及tag核对结果在提交后外部交接记录中登记，避免提交文件包含自身SHA的循环问题。候选版不等于正式工程验收。
