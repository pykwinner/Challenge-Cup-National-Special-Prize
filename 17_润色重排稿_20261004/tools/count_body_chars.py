# -*- coding: utf-8 -*-
"""按竞赛计数口径统计六章正文的非空可见字符。

计数规则：
  统计六章正文 PDF 文本层中的标题、表格、图注和公式字符；
  不计摘要、参考文献、附录、页码和栅格图片内部文字。
  非空可见字符 = 去掉所有空白字符后的字符数。

用法：
  python count_body_chars.py <pdf路径>
"""
import re
import sys

try:
    import pymupdf as fitz
except ImportError:  # 兼容旧版
    import fitz


CHAPTER_TITLES = [
    "研究背景与文献综述",
    "负荷预测与协同优化模型",
    "多场景多尺度源荷协同优化方法",
    "数据体系与模型验证",
    "模型应用与决策分析",
    "模型创新、局限与结论",
]

END_MARKERS = ["参考文献", "附录1", "附录2"]


def norm(s):
    """折叠空白，便于标题匹配。"""
    return re.sub(r"\s+", "", s)


def page_texts(doc):
    return [p.get_text("text") for p in doc]


def strip_page_number(text):
    """去掉独立的页码行（纯数字行）。"""
    lines = text.splitlines()
    kept = [ln for ln in lines if not re.fullmatch(r"\s*\d+\s*", ln)]
    return "\n".join(kept)


def find_chapter_pages(texts):
    """返回 [(章节标题, 起始页索引)]，页索引从 0 开始。

    以“章节标题出现在页首附近”为判据，避免命中目录页。
    """
    toc_pages = set()
    # 目录页：同时出现“目 录”与多个章节标题的页
    for i, t in enumerate(texts):
        if norm(t).count("目录") and sum(norm(t).count(norm(c)) for c in CHAPTER_TITLES) >= 3:
            toc_pages.add(i)

    starts = []
    for idx, title in enumerate(CHAPTER_TITLES):
        want = norm(title)
        found = None
        for i, t in enumerate(texts):
            if i in toc_pages:
                continue
            head = norm(t)[:220]
            if want in head:
                # 章节标题通常带“一、”式前缀，且位于页首
                if found is None:
                    found = i
        if found is None:
            raise SystemExit(f"未找到章节标题：{title}")
        starts.append((title, found))
    return starts, toc_pages


def count_pdf(path):
    doc = fitz.open(path)
    texts = [p.get_text("text") for p in doc]
    starts, toc_pages = find_chapter_pages(texts)

    # 结束页：第六章之后第一个出现参考文献/附录标题的页
    end_page = len(texts)
    sixth_start = starts[-1][1]
    for i in range(sixth_start, len(texts)):
        head = norm(texts[i])[:120]
        if any(norm(m) in head for m in END_MARKERS):
            end_page = i
            break

    results = []
    bounds = [s[1] for s in starts] + [end_page]
    for k, (title, start) in enumerate(starts):
        stop = bounds[k + 1]
        chunk = []
        for i in range(start, stop):
            if i in toc_pages:
                continue
            chunk.append(strip_page_number(texts[i]))
        body = "".join(chunk)
        vis = re.sub(r"\s+", "", body)
        results.append((title, len(vis)))
    doc.close()
    return results


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for path in sys.argv[1:]:
        print("=" * 68)
        print(path)
        print("=" * 68)
        rows = count_pdf(path)
        total = 0
        for title, n in rows:
            print(f"  {title:<20s} {n:>7,d}")
            total += n
        print(f"  {'合计':<20s} {total:>7,d}")
        print()


if __name__ == "__main__":
    main()
