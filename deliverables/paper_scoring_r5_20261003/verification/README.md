# 本次检查与历史评阅

`check_delivery.py` 是只读交付检查器，不运行优化。运行示例（工作目录为仓库根）：

```powershell
python deliverables/paper_scoring_r5_20261003/verification/check_delivery.py deliverables/paper_scoring_r5_20261003 --baseline-source deliverables/paper_module_advantages_20261003/paper_source --expected-pages 27 --file-manifest deliverables/paper_scoring_r5_20261003/MANIFEST.json
```

`final_scoring_text_review.json` 和 `final_c4_claim_review.json` 保留审查时快照，不覆写其时间或 PDF 身份；审查提出的源码对应表和“净供热功率”两项修正已在最终源码落实。最终交付 PDF 与计字、编译、哈希结果以根目录 `VALIDATION.json` 为准。`rubric_source.json` 记录当地提供的官方题面文件身份，不分发该第三方 PDF。
