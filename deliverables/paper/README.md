# 原模板研究论文

`main.tex` 与三个 `sections/*.tex` 在用户指定原模板上修改，此目录是冻结后的便携副本。`paper.pdf` 为已检查的 19 页版本，包含来源、模型、方法、失败的实测长程检验和公开数学例题。

提取汉字 8156，加封面/背景文字 178，合计 8334；另记录非空白字符 12541、英文词 464。这是内部统计，不冒充赛事官方字数算法。论文是研究稿，工程输入来源尚未闭合，不能直接宣称真实工程效果或每项评分已达满分。

9 个源文件/资源保持原相对路径。以 XeLaTeX 编译两遍：

```text
xelatex -interaction=nonstopmode -halt-on-error main.tex
xelatex -interaction=nonstopmode -halt-on-error main.tex
```

本机已用 TeX Live 2026 验证。字体为原模板 `build-fonts/` 内三个 Noto 文件；图片为原模板封面及背景；图形为原生 TikZ，无历史求解图依赖。依赖清单、源/PDF SHA256、参考资料核验和版面检查见本目录 JSON/MD。清单中的绝对路径仅是原制作环境记录，源文件内部相对引用支持迁移。

本地重新编译可能因 PDF 时间戳等元数据产生不同文件哈希，应以源身份和呈现内容核验；不要覆盖 `paper.pdf` 来伪装原冻结版本。
