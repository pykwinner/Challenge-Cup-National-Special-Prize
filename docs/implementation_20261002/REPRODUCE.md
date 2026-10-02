# 本轮复现入口

工作目录是原工程 `north_heat_challenge`。以下为已经执行过的入口或同协议的新目录重跑方式；不覆盖历史实验。

## 环境与测试

本机使用 `D:/app/anaconda3/python.exe`。依赖来自工程原环境；OSM参考模块需要可选 pyproj/networkx，本机已有隔离副本。移动项目时按工程依赖安装，不假定下列绝对Python路径存在。

```powershell
$env:PYTHONPATH='src;docs/execution_20261002/spatial_reference/_runtime_packages'
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
New-Item -ItemType Directory -Force .test_tmp | Out-Null
python -m pytest -q --basetemp .test_tmp/reproduce_final
```

结果：842项测试和26个子测试通过。若不加载可选空间依赖，对应模块会跳过，应明确报告，不将跳过计为通过。本机最终日志见 `final_with_optional.log`。

## 先验证来源，再求解

```powershell
python scripts/prepare_evidence_case_20261002.py --verify-only
python docs/implementation_20261002/data/prepare_tou.py --verify-only
```

它们校验原件、价格时段、输入内容和来源锁。原始数据包不由本轮代码改写。

若要重新运行分时完整季、固定资产回放、控制对照、两端点和5个ε位置，选择一个尚不存在的输出目录：

```powershell
python scripts/run_evidence_case_experiments.py --config configs/evidence_case_20261002/wuhan_bestest600_rc_tou.yaml --timeseries data/derived/evidence_case_20261002/wuhan_2024_2025_hourly_tou.csv --admission docs/implementation_20261002/data/admission_tou.json --out outputs/reproduce_reference_tou/rc --frontier
python scripts/verify_reference_case_experiments.py --base outputs/reproduce_reference_tou --timeseries data/derived/evidence_case_20261002/wuhan_2024_2025_hourly_tou.csv --kinds rc --reference-import-upper-kw 100
```

`run` 拒绝覆盖已有目录。代码版本变化时生成新协议，不将新输出写回2026-10-02冻结记录。实际账户与接网未验证，100kW仅是本参考的量级检查，不是获批配电容量。

现有结果无需重算求解，可独立复核：

```powershell
python docs/implementation_20261002/data/review_base_experiments.py
python docs/implementation_20261002/data/review_tou_experiments.py
```

上述复审不导入热规划模型或调用求解器。它们检查导出值和原来源间的映射；平价与分时分别保存代码身份和复审报告。

加权扫描补充比较复核：

```powershell
python scripts/compare_reference_frontier.py self-check
python scripts/compare_reference_frontier.py verify
```

它另外运行3个归一加权内点，共享已有双阶段端点；费用从调度重算，与研究加权目标分开。新目录重跑约定见 `frontier_comparison/README.md`。

## 真实计量权重实验

```powershell
python scripts/bdg2_confirmation_benchmark.py verify
python scripts/bdg2_hard_weight_confirmation.py verify
python docs/implementation_20261002/algorithm_hard_weights/verify_confirmation.py
python docs/implementation_20261002/algorithm_hard_weights/correct_method_identity.py
```

前两项验证冻结身份；第三项直接从5份来源CSV独立重算30条记录；第四项生成/核对方法身份勘误，保留原始结果及其锁。完整初跑次序和新输出规则见两份算法 README。修改方法后重跑既有建筑只能作为开发/回归，不能重新称为未见确认集。

硬W1接口仅在显式统计API/基准入口启用。它没有静默替换已有多负荷、多年代表期方法，不处理储能跨日状态或自动认证固定投资连续运行。

## 共享几何与实验图

```powershell
python -B scripts/build_shared_osm_candidates.py
python scripts/plot_implementation_evidence.py --tou docs/implementation_20261002/reference_experiments_tou/rc
```

空间脚本离线读取已保全的OSM原生响应，复核旧18栋样本与98条way，重新输出唯一候选段。图脚本只读已导出数据，生成PNG/SVG及来源清单，不改变观测或求解结果。

三城几何不自动成为热网输入；正式接入需补建设许可、实际对象映射、管道热工/水力和费用字段。具体字段见 `spatial/README.md`。

最终交付身份检查：

```powershell
python scripts/audit_implementation_evidence.py
```
