# 11.12.0rc1 研究交付包

在既有工程上修改的代码、真实来源检验与如实报告的研究稿。当前不批准作为已完成真实工程实证的正式提交包。建筑、价格、设备、空间输入的待补依据见 [数据审计](docs/execution_20261001/data_truth_audit/README.md)。

本包只收录明确选择的证据，历史工程参数没有自动进入数据目录。软件单元测试中的人工数值用于验证数学边界，不是实测数据；部分兼容脚本需要未随包分发的历史配置，不能据此运行新的工程实验。审计账本包含被否决的历史数值，是问题记录，不是准入参数。

## 数据来源与使用范围

1. NASA POWER：三个城市、2020—2024 年共 15 份小时气象产品，131544 小时、526176 个数值逐项复核一致。`data/verified_weather_20261002/clean/` 只包含气象及原 LST 时间标签；不是地面站实测，也不是北京时间。太阳辐射列为 Wh/m²。
2. Fraunhofer/IEA EBC Annex 71 N2：DOI `10.24406/fordatis/76.2`，CC BY-SA 4.0；11988 个十分钟样本。保存发布方的处理后文件及派生过程。温度补值、部分功率补零及部分分通道处理继承原发布语义；不能称全部为未经处理的原始传感器读数。原始来源 ZIP 中其他建筑及明确模拟的通道没有进入本次拟合。
3. Mavrotas 作者公开报告：GAMS `https://www.gams.com/modlib/adddocs/epscm.pdf` 第 4.1 节例题。公开数学常数用于 ε 约束与词典序复现，不能当作供热工程数据。新增资源上界扰动明确标为数学检验设定。

来源链接、许可、时间、字段转换和 SHA256 与数据一起保存。下载成功不证明模型适用；公开文献参数也不自动适用于本地建筑。外部来源文件的原有许可保留，不因本包而改为项目代码许可。N2 派生观测与结果按其原 CC BY-SA 4.0 要求署名及共享，详见数据来源文件。

## 可重复执行的范围

本轮验证环境为 Windows、Python 3.12.7、NumPy 1.26.4、SciPy 1.15.3。Windows 请解压到短目录（例如 `D:\heat_release`），过深的目录会触及部分工具的 260 字符路径限制；这是解压验收中实际发现的问题。使用解压后的工程根目录，先安装依赖：

```powershell
python -m pip install -r requirements-validated.txt -r requirements-external-validation.txt
$env:PYTHONPATH='src'
$env:PYTHONDONTWRITEBYTECODE='1'
```

同一 N2 研究的只读复算，输出目录必须为新目录：

```powershell
python scripts/reproduce_external_measurement_studies.py --output-dir outputs/reproduce_n2_new --refit
```

它复用已经冻结的四个训练起点、损失、时间切分和边界，复算两次既有研究，没有额外筛选模型。首轮测试一步 RMSE 为 0.085979℃，持续温度基线为 0.091964℃；持续开环为 1.885259℃，基线为 0.790172℃。后续探索的持续开环达到 7.425830℃，没有改善。后续试验是在看过首轮测试结果后提出，不能称独立确认或外部预注册。

论文数学例题的存档完整性检查及新目录复现：

```powershell
python scripts/validate_published_multiobjective.py verify --run-dir outputs/competition_20261001/C4_published_method/published_lp_01
python scripts/validate_published_multiobjective.py run --run-id published_lp_reproduce_new
```

该例题调用项目 `LinearMILP` 数值层及独立两阶段封装，未证明工程模型的 Pareto 前沿或完整 AUGMECON。23 个 LP 阶段包含 22 次最优和 1 次预先声明的不可行负例；两端点和五个 ε 点与解析解一致。

精简包的相关测试：

```powershell
python -m pytest tests/test_external_rc_benchmark.py tests/test_external_rc_followup.py tests/test_published_multiobjective.py tests/test_weather_archive_comparison.py tests/test_release_provenance_gate.py -q -p no:cacheprovider -k "not test_approval_and_first_round_entire_tree_unchanged and not test_final_manifest_hashes_and_approved_identities" --basetemp outputs/portable_tests_new
```

上述命令明确排除 2 项原工作区整棵目录冻结快照及完整输入清单检查，因为其范围包含未用于计算、未获准分发的早期探索下载；原测试保持不改，复现器仍逐项核对全部入选计算输入与结果哈希。完整工作目录的最后回归为 628 项通过、67.55 秒，记录附在 `outputs/competition_20261001/C0/regression_004.xml`。精简包未附所有历史数据，全量历史兼容测试的通过不能等同于本包全量测试通过。最终便携验收另见交付说明。

## 结果阅读顺序

先看最终交付说明、逐项评分映射和数据缺口，再看论文及逐页答辩 MD。未知团队姓名、阶段工作、企业合作和鉴定均留白。研究稿没有武汉真实项目节费或减碳比例结论。所有可重算结果都对应指定 manifest，历史实验不冒用当前版本身份。
