# 11.12.0rc5 研究候选发布验收

2026-10-02，Asia/Shanghai。**交付批次11.12.0rc5，核心11.12.0rc2，研究候选**。上一发布为`9388556f8d4c1b46fd3640b05454fd992041ea7a`（rc4）；本次实际保留远端新增数据，从`499c16749c399c19fdb49616c0d56d8fd5a44f85`起构建。最终commit/tag/上传及远端核对在提交后交接登记，不编造本文件自身提交SHA。

五组范围与来源边界见[第三阶段总说明](../implementation_20261002_followup/parallel_stage_03/README.md)、[统筹验收](../implementation_20261002_followup/parallel_stage_03/ACCEPTANCE.json)和[三城说明](../source_intake_20261002/planning_user_update/THREE_CITY_DECISIONS.md)。本批在原工程增量加入价格权重、条件投资、多尺度耦合、网络反馈与可靠性记录；已有核心、配置、旧证据及远端用户数据保留。各组源码/结果按冻结白名单复制，文件身份由release_manifest约束。

## 实际提交副本验收

候选目录`github_candidate_20261002_rc5`，独立CPython3.12.7环境，rc2完整锁+`psutil==5.9.8`；pip check、RSS、CPU亲和与子进程读取通过。算法兼容复算使用单数值线程。原rc2–rc4环境未改。

| 实际运行范围 | 结果 |
|---|---|
| 网络74、多尺度48、可靠性33 | 155通过，128.00秒 |
| 算法128（含59原配对权重及19相关）、投资90 | 218通过，12.75秒 |
| 本轮最终独立测试项 | **373通过，0失败/错误/跳过/排除/警告** |
| 网络保存输出 | 30检查，4篡改拒绝 |
| 多尺度保存输出 | 7案例，11全局+137案例检查 |
| 可靠性武汉保存输出 | 90检查，7篡改拒绝 |
| 条件投资run03及reproduce02 | 各24计划、各1122检查 |
| 算法5.9.8完整兼容运行 | 18校准、27×2880h策略、42225独立检查 |

新旧算法10份输入字节相同，21566项输入/科学数值/选择对照通过，所比较科学数字最大差0；不要求时长、RSS相同。此次是既有三城开发窗口的发布兼容复算，非新盲测、外部观测或通用仿真提速证明。完整兼容结果保存在[release_compatibility_run_01](../implementation_20261002_followup/parallel_stage_03/algorithm/release_compatibility_run_01/)。

第一次候选A/B检查135通过、5项setup错误（历史run02输入包未复制）保留；补齐run01/run02/reproduce01历史材料后，上表218项通过，没有删除测试或改变其反例。原算法CSV重建的6个近并列排序错误与补充审查保留：两处约9.09e-13元次序差异在原预声明1e-8并列阈值内，预算绝对1e-7/相对1e-9及严格排序、帽、regret规则未放宽，不称普遍逐位排序稳定。

## 默认安装和核验入口

```text
python -m pip install -r requirements-release-rc5.txt -c docs/github_release_11.12.0rc5/requirements-release-rc5-lock.txt
python -B scripts/run_three_city_decision_weight_confirmation.py check --out docs/implementation_20261002_followup/parallel_stage_03/algorithm/release_compatibility_run_01
python -B scripts/verify_three_city_decision_weight_native_precision.py --out docs/implementation_20261002_followup/parallel_stage_03/algorithm/release_compatibility_run_01 --report <新审核JSON>
```

原`confirmation_run_01`仍锁Conda psutil5.9.0；在新5.9.8环境严格复核原包实际exit2、仅`version:psutil`失败，该报告如实保留，未改协议、依赖确认文件或假造版本。新兼容包实际exit0。5.9.0的PyPI wheel/源码安装失败日志与5.9.8成功安装证据保留，发布入口不引用旧包当默认环境。

## 分发、真实性与未运行项

第三方合同原PDF不上传；历史三个投资输入包排除PDF但保留锁、URL/SHA和缺原件兼容反例。run03/reproduce02新默认输入零PDF、沿用既有审核且明确未重核原件。可靠性入口仍严格核旧参考原件：本地复用10件同SHA归档文件、不分发，公开克隆需按[rc2外取清单](../github_release_11.12.0rc2/external_sources.json)取得；不能把所有新入口称无PDF复现。既有默认[便携复算](../../scripts/reproduce_admitted_reference.py)保持原范围。

21份清单要求的失败checkpoint精确保留；其余pytest故障注入大CSV、缓存、运行库和私密认证排除。初轮错误、旧源码、旧锁、修正与不利结果保留，历史轮不冒称当前代码的成果。所有新验收日志/JSON/XML见[validation](validation/)。

条件投资的真实安装包价与性能/费用研究轴分列，F=5不是寿命或五个独立年度；多尺度楼宇差异、网络几何/水参数属声明设计条件。三城共同公开原型、气候和参考电价不能转述为三城已完成投资/管网实测工程验证、完整LCC收益或FT8性能准入。旧882全库、三城全部新主求解、论文/PPT未重跑或更新；本轮执行上述必要回归及保存输出复核，另实际完整运行一次算法兼容案例。唯一候选tag计划`v11.12.0rc5`，不移动旧标签、不标正式版。
