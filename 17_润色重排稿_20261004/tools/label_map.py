# -*- coding: utf-8 -*-
"""列出正文标签与附录引用关系，供改写时避免交叉引用断裂。"""
import pathlib
import re
import sys

BODY = [
    "main.tex",
    "sections/model-derivation.tex",
    "sections/algorithm-revision.tex",
    "sections/experiments-main-r8p.tex",
    "sections/pseudocode-r8o.tex",
    "sections/site-photos-r8.tex",
]

LBL = re.compile(r"\\label\{([^}]*)\}")
REF = re.compile(r"\\(?:ref|eqref|autoref)\{([^}]*)\}")


def labels(path):
    return set(LBL.findall(pathlib.Path(path).read_text(encoding="utf-8")))


def refs(path):
    return set(REF.findall(pathlib.Path(path).read_text(encoding="utf-8")))


def main(root):
    root = pathlib.Path(root)
    body_lab = set()
    for f in BODY:
        p = root / f
        if p.exists():
            body_lab |= labels(p)

    app_files = sorted((root / "sections").glob("*.tex"))
    app_lab, app_ref = set(), {}
    for p in app_files:
        if p.name in {pathlib.Path(b).name for b in BODY}:
            continue
        app_lab |= labels(p)
        for r in refs(p):
            app_ref.setdefault(r, []).append(p.name)

    print("== 附录引用到的正文标签（改稿后必须仍存在）==")
    keep = sorted(app_ref.keys() & body_lab)
    for x in keep:
        print(f"  {x:36s} <- {','.join(app_ref[x][:3])}")
    print(f"  共 {len(keep)} 个\n")

    print("== 附录引用但全文档都不存在的标签（原本就是断链）==")
    miss = sorted(x for x in app_ref if x not in app_lab and x not in body_lab)
    for x in miss:
        print(f"  {x:36s} <- {','.join(app_ref[x][:3])}")
    print(f"  共 {len(miss)} 个\n")

    print("== 正文全部标签 ==")
    for x in sorted(body_lab):
        print("  ", x)
    print(f"  共 {len(body_lab)} 个")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
