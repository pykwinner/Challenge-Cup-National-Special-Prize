# 11.12.0rc6 研究候选发布验收

2026-10-02，Asia/Shanghai。交付批次 **11.12.0rc6**，核心 **11.12.0rc2**，研究候选。基线`7a4df3cbfc034d52a16a018eef6d6941858497f0`（rc5）；旧main、用户补充数据、所有旧tag/失败证据保留。最终提交、tag对象及实际上传/远端核对另见提交后交接，不填尚不存在的自引用SHA。

推荐从[最终研究总结及评分对应](../final_optimization_20261002/README.md)进入，再看[文献与设计决策](../final_optimization_20261002/LITERATURE_AND_DESIGN_DECISIONS.md)。这是四个新增模块/实验的集成，原53个src/config文件身份完全不变，没有替换旧核心求解语义。冻结482文件及清单自身逐一同SHA复制；[SOURCE_FREEZE](../final_optimization_20261002/SOURCE_FREEZE.json) SHA256 `28825f3162a6a326ce53906f6d7918c67f5ac7974fdabd152813443c74873621`。统筹独立核对482文件、252唯一测试、热泵30份dispatch/summary字节同旧结果，回执见[独立检查](validation/root_readonly_candidate_check.json)。

## 实际候选验收

独立CPython3.12.7环境，保留rc5原34个精确包版本，仅新增与原工程一致的PyMuPDF1.27.2；fitz/MuPDF版本均1.27.2，新env禁用user-site，pip check及新旧环境独立核对通过。原rc5环境完整freeze/配置/解释器/锁未变。wheel、安装来源、实际35包锁与新增原因全部留档。新测试及复算设置单数值线程，不据此说明所有历史HiGHS运行或Codex服务档位。

| 实际范围 | 结果 |
|---|---|
| 时间登记30、结论证据44、已有质量34、纯C轴24、已有多尺度48 | 180通过，20.06秒 |
| 热泵根目录三文件（不收frozen_code副本） | 72通过，17.65秒 |
| 本轮两个XML去重合计 | **252唯一testcase通过，0失败/错误/跳过/排除/警告** |
| 从候选仓内input_bundle新跑恒温器 | 9×2880h，独立审计25920小时，PDF165个Q/P工况点零差异 |
| 同有限固定设备完美预知费用LP | 6×2880h，独立审计17280小时及6份native弱对偶下界 |
| 纯C×共享容量四案新MILP | 4×2880h×2建筑，四案数值与独立物理账本通过；report及旧run03对照留档 |
| 时间登记新报告 | 11份实际输入、5种时间身份；权重核算与连续时长分开 |
| 求解证据新只读报告 | 61记录、690文件身份；22登记子问题+38有限主目标/选定候选次目标+1方法参考 |

本轮**19个新复算案例**=9恒温器+6固定LP+4有限MILP；两份登记/证据报告不再算求解案例。旧373/882等未重复或累计。旧heatpump run01/optimization01、C run01/02/03、time_registry/run_final、solution_evidence/report_final和旧失败材料均未覆盖。初次证据报告把输出放在被检查的候选内，触发既有outside-root守卫exit1，日志/命令保留；之后外置新输出，输入仍全为候选，再逐字节镜像入发布目录，不改代码或弱化保护。

热泵15案的30份dispatch/summary与冻结旧结果逐字节相同；六案费用降1.10127%–1.59703%，电量/共同固定因子碳量均升。纯C共享上限对照费用降4.659735%，电量增4.166888%；完整逐时/变量/解析热损和图源身份见新复现与比较记录。这是移峰省费与散热/用电代价，不称同步节能减碳。

## 克隆后的推荐环境和命令

```text
python -m pip install -r requirements-release-rc6.txt -c docs/github_release_11.12.0rc6/requirements-release-rc6-lock.txt
python -B -m pytest tests/test_time_registry.py tests/test_solution_evidence.py tests/test_solution_quality.py tests/test_multiscale_capacity_axis.py tests/test_multiscale_reference_audit.py tests/test_multiscale_reference_protocol.py docs/final_optimization_20261002/heatpump/test_heatpump_reference.py docs/final_optimization_20261002/heatpump/test_heatpump_optimization.py docs/final_optimization_20261002/heatpump/test_native_lp_bound.py -q -p no:cacheprovider
```

设置`PYTHONPATH=src`、`PYTHONDONTWRITEBYTECODE=1`。热泵完整5步见[运行说明](../final_optimization_20261002/heatpump/RUN_INSTRUCTIONS.txt)，从热泵目录显式`--source-dir run_01/input_bundle`运行至全新目录，不依赖仓外补充路径；审计冻结结果必须用新`--audit-out`，native证书必须用新`--out`。多尺度freeze/run/audit及report见[组说明](../final_optimization_20261002/multiscale/HANDOFF.json)。report原生生成`<新run>/analysis`，保留该路径。

```text
python -B scripts/build_time_registry.py --root . --spec docs/final_optimization_20261002/time_registry/spec_final.json --out <全新时间报告目录>
python -B scripts/report_solution_evidence.py --evidence-root . --out <仓库外全新输出目录>
```

求解证据扫描器明确要求输出在被扫描仓库外，保护输入身份；该约束不等于使用仓外数据。所有实际完整argv与exit、XML/log/结果身份在[validation](validation/)，新结果在[reproduction](reproduction/)。

## 来源、分发和结论边界

原厂家PDF和CSV此前已在用户rc5提交中跟踪，本轮继续保留其精确身份及冻结bundle，不能套用旧FT8三份合同PDF排除规则。TICA数据是经销商归档的厂家样本精确副本，逐格重新核对165个Q/P工况点；未称本次从厂家端点认证同版、设备现场实测或化霜/部分负荷验证。公开BESTEST600 48m²原型的20份合成复制是研究规模，三城NASA格点气象真实，湖北电价/固定排放因子是共同条件轴，地方账户/并网适用性另待确认。没有TICA报价，不混用FT8套餐价；原表空角/越界拒绝、备用容量有限、部分负荷采用明确理想占空比。

有理数下界只针对已编码binary64系数、有限变量界的费用LP；incumbent仍按数值容差可行，不能升级为严格可行上界、精确最优或全局词典序证明。C的gap0是求解器对声明有限MILP的数值报告；独立RC/热损/容量账本不独立证明整数全局最优。时间身份不证明同源数据/费用/设备或所有历史CLI统一。61条结论分类保留原始界、冻结残差和既有审核容差，不把gap乘KPI制造区间。

旧原件获取规则仍适用于原rc2–rc5相应严格入口，本轮新热泵流程只读仓内bundle、没有网络数据重下载。来源审核、数值可行、求解范围和实测验证分别表述。暂未做新的实测/盲测、自由投资/LCC/回收期、企业认证、PPT或论文更新；本tag计划`v11.12.0rc6`为研究候选，不标正式工程全面验收。
