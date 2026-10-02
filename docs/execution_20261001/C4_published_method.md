# C4 已发表多目标数学算例复现交接（2026-10-02）

**已完成：真实作者原文核对、先存方案、小 LP 实际求解、独立解析核对、全矩阵审计和不可覆盖证据包。仅为已发表数学算例的方法验证，非供热工程前沿，非实测验证；不改变工程参数准入状态。未运行任何新增工程重 MILP。**

工程实际 `heatplan.linear.LinearMILP` / SciPy `milp` 已用于连续 LP，所有 integrality=0。独立两阶段词典序包装先求主目标，再把实际主目标最优值设为精确等式，求次目标。没有调用 `HeatPlanningModel.solve_lexicographic`，没有声称完整实现 AUGMECON 的松弛加权或提前退出。

## 来源与预声明

主来源为 [George Mavrotas 官方 GAMS 作者报告](https://www.gams.com/modlib/adddocs/epscm.pdf)。完整物理页6-9已渲染并亲自核对。第4.1节模型在第6页（索引5）；表2和图2在第7页（索引6）；四等分规则在第8页（索引7）；图4在第9页（索引8）。不把 HTTP Last-Modified 当发表日期，报告日期未声称已验证。

源 PDF SHA256：`964c7bef93a278a4bea264a1d6f8fb51faf328644f3034215e871343eea3730a`；237007字节、12页。下载UTC时间为2026-10-01T16:13:49.498267+00:00（北京时间10月2日00:13）。完整 PDF、页图、页文本、HTTP元数据在 `data/published_multiobjective_20261002/`。

每个源数字逐字段记录于 `published_input.json`。非负域是图2可行域的图示解释，原模型公式未单列下界；1和0分别为单位系数/缺项的代数编码，未伪称全部数字均逐字印在公式里。表2预期端点只用于结果核验，不进入模型约束。三个中间ε值166/172/178为原文四等分规则的推导值，不伪称独立印在原文中。

`predeclared_plan.json` 在任何实验及测试求解前保存（UTC16:15:38），SHA256 `69f0da3d5e3662557f6e4d34f3ec43a03dec9b5d308c4fb0182a941c67cd4ea0`。输入SHA256 `d8f286a0c2fa1d3ed5f0b47f122d1e95bba2cf084cb0bfca249bf2fa84838d99`。另有不可覆盖 `predeclared_review_identity.json` 冻结原文、输入和方案身份。

## 实际结果

主算例资源上限200，两词典序端点与表2一致：

| 优先顺序 | 实际x1 | 实际x2 | 实际f1 | 实际f2 |
|---|---:|---:|---:|---:|
| f1→f2 | 20 | 25 | 20 | 160 |
| f2→f1 | 8 | 40 | 8 | 184 |

ε网格由实际求得的端点f2范围四等分生成，再核对预声明值；未硬写最优解作输入。

| ε | 实际x1 | 实际x2 | 实际f1 | 实际f2 |
|---:|---:|---:|---:|---:|
| 160 | 20 | 25 | 20 | 160 |
| 166 | 17 | 28.75 | 17 | 166 |
| 172 | 14 | 32.5 | 14 | 172 |
| 178 | 11 | 36.25 | 11 | 178 |
| 184 | 8 | 40 | 8 | 184 |

5个实际点均唯一且互不支配。边界试验是预声明数学检验协议；180/220不是论文原始数据或现场数据。

| 资源上限 | f1→f2实际目标对 | f2→f1实际目标对 | 用途 |
|---:|---|---|---|
| 180 | (20,140) | (4,172) | 数学边界检验 |
| 200 | (20,160) | (8,184) | 原文主算例 |
| 220 | (20,180) | (12,196) | 数学边界检验 |

合计23次实验 LP：22次 status=0，预声明负例“资源180、ε184”status=2、没有解向量且不启动第二阶段。该负例解析上f2≤0.6×180+1.6×40=172，故ε184不可行。求解累计0.0177428秒；每阶段独立时钟、状态、message、全向量均留存。`summary.wall_seconds` 只测实验计算/CSV阶段，排除源快照及后续图表/报告生成，不作完整CLI耗时。

## 核验与证据

每阶段保存完整变量界、integrality、目标向量、矩阵及行界。可行解逐行活动/上下松弛、逐变量界违反和目标重算均审计通过：最大行违反0、变量界违反0、目标重算误差0。独立有理数多边形算法从源模型半空间枚举全部交点，并核对每阶段最优目标；它不调用求解器、不读取工程求解矩阵。另一个不导入复现脚本的闭式解和完整矩阵复算共147项检查全部通过。

纯LP未暴露的mip_dual_bound/mip_gap/mip_node_count保留null，不伪报0；独立解析最优界另标来源，不冒称为求解器暴露的界。绝对/相对容差、10秒时限及零主目标劣化 allowance均在求解前登记为数值协议。

`published_lp_01/manifest.json` complete=true，最后写入；106个归档文件哈希全部校验通过。主原文、输入、方案、代码快照及每阶段矩阵/结果JSON都在清单中。实验开始和结束代码/输入身份相等。独立复核记录在包外的 `published_lp_01_review.json`，明确为补充复核，不改写原manifest。

16项相关测试通过（0.95秒），覆盖解析独立性、实际核心端点、失败向量拒绝、不可行无解审计、未暴露界不伪造、参照端点不进模型、失败无complete清单、禁止覆盖和篡改检出。JUnit `test_results_02.xml`。第一次8项setup错误仅为pytest basetemp父目录尚未存在，记录 `test_results_01.xml` 保留；父目录存在后全部通过，未发生数学求解测试失败。

执行源码SHA256：

| 文件 | SHA256 |
|---|---|
| scripts/validate_published_multiobjective.py | 86a45367042b92e93f64f0685b7c5776672d832c360fba52b92cbcdd71a52168 |
| src/heatplan/linear.py（只读） | af8ae466c1c474d25b5acf51a7de62406ad927e3d2afd7334be87b35e31b210c |
| tests/test_published_multiobjective.py | b44e9b5419dc2209a0160ea2724dbd5709985fd0dc334d1963423216bbab4d84 |

## 路径与复核命令

- 脚本：`scripts/validate_published_multiobjective.py`
- 测试：`tests/test_published_multiobjective.py`
- 源与预声明：`data/published_multiobjective_20261002/`
- 完整实验：`outputs/competition_20261001/C4_published_method/published_lp_01/`
- 报告：上述实验目录的 `report.md`
- CSV：`stages.csv`（23阶段）、`points.csv`（11个词典序最终点，含端点及ε点）
- 图：`published_frontier.png`、`published_frontier.svg`；PNG已亲自检查，无遮挡/裁切。
- 追加独立复核：`outputs/competition_20261001/C4_published_method/published_lp_01_review.json`

在工程根目录运行：

```powershell
$env:PYTHONPATH='src'
$env:PYTHONDONTWRITEBYTECODE='1'
& 'D:/app/anaconda3/python.exe' scripts/validate_published_multiobjective.py verify --run-dir outputs/competition_20261001/C4_published_method/published_lp_01
```

如以后获授权重新运行，必须用全新run-id，已有目录直接拒绝。当前已完成，无需额外求解或重跑全工程回归。所有共享核心和旧工程结果未修改；`empirical_validation=false`、`heating_engineering_validation=false`、`parameter_provenance_approved_for_heating_scenario=false`。
