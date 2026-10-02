# 最终论文源依赖清单（2026-10-02）

主文件：`D:\competion\xiaotiao\16\16_LaTeX源文件\main.tex`
最终研究交付PDF：`D:\competion\xiaotiao\16\C7_compact_method_build_20261001\C7_research_paper_20261002.pdf`
19页；提取汉字8156，图像文字补计178，合计8334。非空白提取字符12541，英文词464（额外口径，不冒称赛事官方计数算法）。

复制时保持以下9个文件的相对目录：recorder确认4个tex和2张源根目录图片，fontspec声明3个相对字体文件。

- `body-background.jpg` — SHA256 `93540a2af4166d81977ec5dbfeb4206fbafab2c780a4b898b799479c5e427f9b`
- `cover.jpg` — SHA256 `3e1bb1985ee542c9d019b0d0f7611fa3d140e1eaa2090c255c1e4b44dbcb4c8a`
- `main.tex` — SHA256 `7660509f90288ff314bf3d5ee5f96dd0a16ca77dbfa125330b330cd073131075`
- `sections/algorithm-revision.tex` — SHA256 `e045dbe4d2ba7a3ae637186c5dca30e231e3952a658633c8dd95e60c2708bc60`
- `sections/model-appendix.tex` — SHA256 `901ff7354c04f0b546eaba0c3a50a0725ef4b0dd7b5571ab51b6d6dfd865c61f`
- `sections/model-derivation.tex` — SHA256 `cf87eaa4c3c56ab822b3f4d5c3a934ab3888691218defbf757d20d9f3a577587`
- `build-fonts/NotoSerifSC-Regular.ttf` — SHA256 `be369c4126abb10d76d58c2a9d8622e49e21f3e4227ba8b6590cd5cedf7a79bd`
- `build-fonts/NotoSerifSC-Bold.ttf` — SHA256 `a420f886ef0331c205a318e4d4aba75cbed4180e2b4ef3803125b4b1e3417725`
- `build-fonts/NotoSansSC-Regular.ttf` — SHA256 `25628aac5ef8b03a013166472539318eafeee1d9004017549723f4e1aedf02a6`

注意：cover.jpg与body-background.jpg实际位于源根目录，不能根据未使用的graphicspath误移至media。两幅正文图为既有TikZ原生图，无额外图片依赖。无需复制旧结果图和未引用algorithm-framework.tex。

编译：XeLaTeX/TeX Live 2026，常规包见JSON。复制后在源根目录建立build，再执行两遍：

```powershell
xelatex -interaction=nonstopmode -halt-on-error -file-line-error -recorder -output-directory=build main.tex
xelatex -interaction=nonstopmode -halt-on-error -file-line-error -recorder -output-directory=build main.tex
```

字体/背景/前导区保持原模板。当前文稿完成的是方法、来源与适用性研究交付；工程参数尚未闭合，不称可正式提交的工程实证论文。源/PDF已按本清单SHA冻结。
