"""按编译顺序给主报告的图件编号，便于与评审批次页码对齐。

编号对象是 figure 环境（LaTeX 的图号按浮动体计），不是图片个数：
一个浮动体内可能并排放多张图片，只占一个图号。main.tex 的 \\input
前后都有正文，因此先把 \\input 就地展开再顺序编号。
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
INPUT_RE = re.compile(r"\\input\{([^}]*)\}")
FIGURE_RE = re.compile(r"\\begin\{figure\*?\}(.*?)\\end\{figure\*?\}", re.S)
INCLUDE_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}")
CAPTION_RE = re.compile(r"\\caption\{([^}]*)\}")
LABEL_RE = re.compile(r"\\label\{(fig:[^}]*)\}")


def expand(text: str) -> str:
    def repl(match: re.Match) -> str:
        name = match.group(1)
        if not name.endswith(".tex"):
            name += ".tex"
        for candidate in (ROOT / name, ROOT / "sections" / pathlib.Path(name).name):
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        return ""

    previous = None
    while previous != text:
        previous = text
        text = INPUT_RE.sub(repl, text)
    return text


def main() -> None:
    body = expand((ROOT / "main.tex").read_text(encoding="utf-8"))
    index = 0
    for env in FIGURE_RE.finditer(body):
        block = env.group(1)
        index += 1
        images = [pathlib.Path(p).name for p in INCLUDE_RE.findall(block)]
        caption = CAPTION_RE.search(block)
        label = LABEL_RE.search(block)
        print("{:3d}  {:<40} {:<30} {}".format(
            index, ",".join(images)[:40],
            (caption.group(1)[:28] if caption else ""),
            (label.group(1) if label else ""),
        ))


if __name__ == "__main__":
    main()
