# 暖城向新 R8P-A4：核心代码附录扩充

文档候选版 **1.2.0-rc1**，论文修订号 **R8P-A4**，标签 `paper-r8p-a4-v1.2.0-rc1-20261004`。本版是文档交付，不是新的工程验收版本。

- [论文 PDF（124 页）](pdf/暖城向新_核心代码附录扩充_R8P-A4.pdf)：代码附录在阅读器第 63–104 页。
- [可编辑 LaTeX 主文件](source/main.tex)；[详注代码附录](source/sections/code-appendix-r8p.tex)。
- [完整 LaTeX 源包](暖城向新_R8P-A4_GitHub_LaTeX.zip)：含全部 503 个源文件，与本目录 source 逐项一致；不含历史 review 压缩档；采用 ZIP LZMA 压缩，可用 7-Zip 或 Python zipfile 解压。
- [代码注释与来源映射](source/code_annotated_r8p_a4/)；[固定源码快照](source/source_snapshot/)。
- [发布说明](RELEASE.json)、[本次上传副本验证](VALIDATION.json)、[文件指纹](UPLOAD_MANIFEST.json)。

## 修改范围

本版在本地 R8P-A3 上只扩充代码附录：12 段增至 17 段，原代码 722 行增至 1066 行，中文补充注释 133 行增至 224 行。每段补充输入输出、实现思路、关键约束和结果核对方法；新增共享余热资源、逐楼热状态、配电容量、硬能量最小 W1 权重优化、紧凑启停五项实现。正文、模型公式、已有图表与本地 A3 保持一致。

本次基于远端 `97a59e047c8fc7fda2339b39b6dbcd9a36aba548` 新建独立目录；远端已有 `deliverables/paper`、R5 等历史修订保留，不自动合并或替换。这里的“正文未改”指 A4 相对本地 A3，不表示与远端较早版本正文相同。

## 编辑与复现

在 source 目录使用已安装的 XeLaTeX 编译 main.tex；可运行随包 build_paper.ps1，首次构建需要目录更新时再运行一遍。字体及 OFL 许可随包提供。完整源码快照便于阅读与对应，不包含全部实验输入，不应单独当成一键重跑所有实验包。

本轮不运行优化实验，不累计以往回归数量。论文原文件、源包及历史复核记录仍留在工作目录。这里的 GitHub 源包是单独整理的 source-only 交付，与此前本地大压缩包不是同一文件。

## 来源与分发

保留 source/build-fonts 中字体许可、validation 中现场照片来源与许可说明，以及各图件附带来源记录；不声明整个模板、照片与第三方资料统一原创或统一许可。原始参考论文 PDF、历史构建页图和非本版资料未加入本次交付。

验证记录中的 `validation/delivery.json` 是先前本地交付身份，记录了未上传的旧压缩包；本次 GitHub 源包的实际大小及 SHA-256 以 `VALIDATION.json` 和 `UPLOAD_MANIFEST.json` 为准。
