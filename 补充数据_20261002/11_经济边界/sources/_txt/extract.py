import sys, pymupdf
src, dst = sys.argv[1], sys.argv[2]
d = pymupdf.open(src)
out = []
for i, pg in enumerate(d):
    out.append(f"\n===== PAGE {i+1} =====\n")
    out.append(pg.get_text())
open(dst, "w", encoding="utf-8").write("".join(out))
print("ok", len(d), "pages ->", dst)
