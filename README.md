# 城镇供热源荷匹配与规划寻优

CP-202611 三城市供热规划工程，当前核心版本 **11.12.0rc1**。采用 Python、SciPy/HiGHS，包含源—网—荷—储联合建模、建筑热状态、投资与运行优化、连续时序回放、成本碳评价及独立检查。

本仓库包含当前完整代码、三城运行配置和必要数据，并附已选择的研究证据。乌鲁木齐、固原、武汉的原场景均保留。旧版本工程、临时求解目录、重复压缩包和无关资料未复制。

## 三城市入口

| 场景 | 基础配置 | 基础时序 |
|---|---|---|
| 乌鲁木齐／既有供热网络转型 | `data/scenario1_existing_network.yaml` | `data/scenario1_existing_network_timeseries.csv` |
| 固原／分布式清洁供热 | `data/scenario2_distributed_clean.yaml` | `data/scenario2_timeseries.csv` |
| 武汉／高品质新建与冷热联供 | `data/scenario3_high_quality_newbuild.yaml` | `data/scenario3_timeseries.csv` |

公开气象场景配置位于 `data/public_scenario_inputs/`：`scenario1_urumqi_public.yaml`、`scenario2_guyuan_evidence_public.yaml`、`scenario3_wuhan_joint_public.yaml`。完整年度及多气象年输入也保留于该目录和 `data/public_scenario_multiyear/`。基础时序是程序演示/情景输入，不等于当地现场实测。

## 安装与运行

已验证 Windows、Python 3.12.7、SciPy 1.15.3。建议使用短克隆目录，避免部分 Windows 工具的长路径限制。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-validated.txt -r requirements-external-validation.txt
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m heatplan.cli --config data/scenario1_existing_network.yaml --timeseries data/scenario1_existing_network_timeseries.csv --output outputs/urumqi_demo --time-limit 120
```

固原、武汉替换为上表对应配置和时序。该命令运行基础例，不代表新版三城完整年度或真实工程验证。武汉新增 RC 原型与年/季运行入口见 [运行说明](README_COMPETITION.md)。

```powershell
.\.venv\Scripts\python.exe scripts/fetch_published_method_source.py
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider -k "not test_approval_and_first_round_entire_tree_unchanged and not test_final_manifest_hashes_and_approved_identities" --basetemp outputs/github_tests_new
```

两个明确排除的测试检查原研究工作区整棵探索树，依赖未用于计算、未分发的 CTSM 等发现材料。原测试保持不改；其余计算与身份测试照常执行。每次使用新临时目录。上传时的实际验证见 [上传说明](GITHUB_UPLOAD.md)。

## 数据与完成范围

- 三城2020—2024年 NASA POWER 气象及核验后的纯气象表保留来源 URL、单位、时钟和哈希。格点产品不等同当地地面站观测。
- 建筑热工、负荷、价格、设备和空间输入仍有假设或待补依据。保留它们是为了运行和追溯原工程，不表示已经核实。
- 新版完整年/季原型实验主要在武汉完成；乌鲁木齐、固原尚未完成同等新版验证。三城供热模型的新版正式多目标前沿仍待执行。
- Fraunhofer N2 实测检验和公开数学例题只支持各自范围，如实保留长程预测较差的结果；不替代三城工程证据。
- 后续规范设计参考与资料核查见 `data/design_reference_20261002/`、`docs/source_intake_20261002/`。新增资料未自动改写历史实验。CSWD原始镜像及未使用的第三方探索库不随代码上传，保留获取地址和登记。

参见 [数据真实性审计](docs/execution_20261001/data_truth_audit/README.md)、[评分对照](docs/execution_20261001/评分逐项验收与算法优势.md)、[研究交付说明](最终交付说明.md)和 [原工程说明](docs/history/README_before_github_upload.md)。归档文档对应各自冻结时间，新增数据不自动改变旧结论。

## 目录

| 目录 | 内容 |
|---|---|
| `src/heatplan/` | 模型、求解、时序、多目标、经济与审计模块 |
| `scripts/` | 数据准备、批量运行、来源核验、研究复现和报告工具 |
| `tests/`、`examples/` | 测试与最小示例 |
| `data/` | 三城市配置、气象、示例/原型输入及来源 |
| `docs/` | 算法、方法、数据依据与历史记录 |
| `outputs/competition_20261001/` | 明确选入的证据及必要脚本，新增输出默认忽略 |
| `deliverables/paper/` | 原模板研究论文及源文件副本 |

## 来源与许可

第三方资料保留原许可和归属，不因上传而变更为项目代码许可。N2 发布数据及派生观测按 CC BY-SA 4.0 署名共享，详见数据目录 README。未额外替项目声明开源许可证，代码授权范围由项目所有者决定。
