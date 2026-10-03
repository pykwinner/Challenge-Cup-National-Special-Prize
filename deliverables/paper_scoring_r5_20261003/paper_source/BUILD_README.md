# 论文编译入口

主文件为 `main.tex`，编译器为 **XeLaTeX**，编码为 UTF-8。

`sections/` 下的文件是被主文件加载的章节，依赖主文件中的字体、宏包和定义。支持 TeX root 注释的编辑器可以从章节跳转到主文件编译；其他编辑器请直接打开 `main.tex`。

在本目录执行 `powershell -ExecutionPolicy Bypass -File .\build_paper.ps1`，或者在编辑器中选择 XeLaTeX 并连续编译两次。输出保存在 `build/main.pdf`。

当前为 `paper-scoring-20261003-r5`。直接运行本目录的 `build_paper.ps1` 编译已编辑源码；不要调用旧R2的 `assemble_paper.py` 或包含它的旧构建流程，以免覆盖本版摘要与附录。GitHub 交付根目录的 `R5_REVIEW.md` 对应评分项，`EVIDENCE_INDEX.json` 对应实验记录，四段完整伪代码位于 `supplement/ALGORITHM_DETAILS.md`。

请保留 `build-fonts/`、`media/` 和全部已加载章节。首次编译产生交叉引用信息，第二次用于更新引用与目录。

2026-10-03：为七个当前章节补充主文件/引擎/编码提示，统一 COP、EER、CRF 的数学正体。该修改不改变模型方程和实验数据。
