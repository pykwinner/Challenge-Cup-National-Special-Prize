# C6 精选包便携性审查

**结论：数值复现依赖闭合，可排除CTSM探索文件；精选测试须明确排除两项原工作区检查。** 未改主脚本/冻结测试，未导入研究模块、拟合、求解或运行测试。

C0原复现目录的22项输入及3项产物哈希全部一致；保存报告的两组参数与144个MAE/RMSE/bias/max标量均与冻结报告完全一致。短路径解压包`D:/competion/xiaotiao/_release_check01/north_heat_challenge`具备以下31项必要文件且字节一致；其保存的`portable_n2_01`记录两组refit参数/指标差均为0。第二次测试XML记录55通过、0失败（C0执行，C6只读核对）。这些是数值便携性证据，不能扩展为准确率批准。

复现入口只读取first manifest的15项选定输入、两份原XLSX、观测CSV、两组全部outputs/manifest及followup脚本/测试；不调用`verify_approval`、`snapshot_first_round`、`verified_members`或原`run`。导入及计算需要numpy/pandas/scipy/openpyxl，保留两份requirements文件可复现已验证环境。已提供XLSX时无需inflate64解包；复现不调用绘图或联网。保留工程相对层级，输入根目录由脚本位置定位。

实际问题及范围限制：

1. **需要排除两项**：`test_approval_and_first_round_entire_tree_unchanged`匹配原36文件快照；`test_final_manifest_hashes_and_approved_identities`读取followup manifest全部38项输入。后者也包含未分发的full2/CTSM等，不能只排除第一项。保持冻结测试不改，不为通过检查补分发探索资料。
2. 保留其余followup测试时，`test_existing_output_or_file_is_never_overwritten`在拒绝旧目录前先验审批，额外需要下列五个文件（当前解压包已含）：

- docs/execution_20261001/C1_external_followup_plan.md
- outputs/competition_20261001/C0/external_followup_approval_01.json
- outputs/competition_20261001/C1_external/final_verification.json
- outputs/competition_20261001/C1_external/test_result.txt
- docs/execution_20261001/C1_external_validation.md

3. 公开LP测试还需五个来源文件：`published_input.json`、`predeclared_plan.json`、`source_manifest.json`、`predeclared_review_identity.json`、`epscm.pdf`，位于`data/published_multiobjective_20261002`。`heatplan.__init__`间接导入model/io/policy_alignment，所以只带linear.py不够，还需要JSON列明的7文件导入闭包及PyYAML。其失败路径测试在注入失败前仍验来源身份。无需C4旧结果树。
4. 气象测试只调用脚本的`compare`及自建fixture，不需真实NASA文件或网络；发布门禁测试使用tmp_path自建审批/数据/结果，不依赖原C0审批树。
5. 原followup manifest作为冻结证据保留，但不能拿它当精选计算依赖清单直接交给通用`checked_manifest`，否则会要求全部旧树输入。独立reproduction manifest才记录已选择计算身份。
6. `--output-dir`必须不存在；验证失败可留下空/部分新目录，换新目录重试。必须在冻结输入/结果树之外选新目录。首次CSV复现加两列training-block信息，并将未计分初始化行的持续性值设为空，因此第一份CSV不承诺逐字节复制；144个计分标量一致。
7. C0已报告深路径解压触发Windows260字符限制，短根路径运行成功。审查时包内README仍只排除第一项，应更新两项排除和短路径提示；原工作区全部628通过不能替代精选包适用范围说明。

建议精选测试命令（仅供交接，本次未执行）：

```text
python -m pytest tests/test_external_rc_benchmark.py tests/test_external_rc_followup.py tests/test_published_multiobjective.py tests/test_weather_archive_comparison.py tests/test_release_provenance_gate.py -q -p no:cacheprovider -k "not test_approval_and_first_round_entire_tree_unchanged and not test_final_manifest_hashes_and_approved_identities" --basetemp outputs/portable_tests_new
```

数值复现必带的31项文件（保留相对路径；两组全部outputs均已包括）：

- data/external_rc_benchmark_20261001/README.md
- data/external_rc_benchmark_20261001/analysis_plan.json
- data/external_rc_benchmark_20261001/data_quality.json
- data/external_rc_benchmark_20261001/evidence/Twin_house_N2_exp1_full1_10min.xlsx
- data/external_rc_benchmark_20261001/evidence/Twin_house_weather_exp1_10min_compensated.xlsx
- data/external_rc_benchmark_20261001/evidence/annex71_landing.html
- data/external_rc_benchmark_20261001/evidence/annex71_main_experiment.zip
- data/external_rc_benchmark_20261001/evidence/annex71_specification.pdf
- data/external_rc_benchmark_20261001/evidence/downloads.json
- data/external_rc_benchmark_20261001/extracted_members.json
- data/external_rc_benchmark_20261001/field_evidence_map.json
- data/external_rc_benchmark_20261001/observations.csv
- data/external_rc_benchmark_20261001/source_provenance.json
- outputs/competition_20261001/C1_external/external_validation.png
- outputs/competition_20261001/C1_external/manifest.json
- outputs/competition_20261001/C1_external/predictions_and_residuals.csv
- outputs/competition_20261001/C1_external/validation_report.json
- outputs/competition_20261001/C1_external_followup/exploratory_validation.png
- outputs/competition_20261001/C1_external_followup/first_round_before.json
- outputs/competition_20261001/C1_external_followup/first_round_freeze_check.json
- outputs/competition_20261001/C1_external_followup/first_vs_followup_metrics.csv
- outputs/competition_20261001/C1_external_followup/fit_result.json
- outputs/competition_20261001/C1_external_followup/manifest.json
- outputs/competition_20261001/C1_external_followup/predictions_and_residuals.csv
- outputs/competition_20261001/C1_external_followup/run_summary.json
- outputs/competition_20261001/C1_external_followup/validation_report.json
- scripts/reproduce_external_measurement_studies.py
- scripts/validate_external_rc_benchmark.py
- scripts/validate_external_rc_followup.py
- tests/test_external_rc_benchmark.py
- tests/test_external_rc_followup.py

完整哈希、附加测试依赖、函数行号与C0记录核对见同目录`portable_reproduction_review.json`。五个指定测试中除上述两项外，未发现必须依赖未选择CTSM/旧整树的第三项检查。
