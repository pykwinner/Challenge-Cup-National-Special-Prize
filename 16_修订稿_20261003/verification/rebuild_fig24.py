"""把成本—碳排 Pareto 三联图改为竖排，用于恢复面板可读性。

原图把三个城市面板并排放在同一张 5344x1503 的位图上，缩到正文宽度后
面板内文字按占位宽换算不足 3 pt。这里按背景色测得的面板边界把三块裁开、
竖排并保留原有图面配色，不改动任何数值或图元，只是重排版面。
"""
import pathlib

import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "media/code-results/fig11_cost_emission_pareto.png"
DST = ROOT / "media/code-results/fig11_cost_emission_pareto_stack.png"

BACKGROUND = (251, 246, 234)
# 由背景色扫描得到的面板横向边界：三块等宽，切在空白列上
PANEL_X = [(20, 1780), (1800, 3560), (3580, 5340)]
PANEL_Y = (290, 1480)
GAP_PX = 26
# 第一面板左下的说明条与标记、轴刻度互相压字；其内容改由图注承载
NOTE_BOX = (315, 1268, 1095, 1318)


def main() -> None:
    image = Image.open(SRC).convert("RGB")
    for box in (NOTE_BOX,):
        image.paste(Image.new("RGB", (box[2] - box[0], box[3] - box[1]), BACKGROUND), box[:2])
    canvas_w = PANEL_X[0][1] - PANEL_X[0][0]
    panel_h = PANEL_Y[1] - PANEL_Y[0]
    canvas_h = panel_h * len(PANEL_X) + GAP_PX * (len(PANEL_X) - 1)
    canvas = Image.new("RGB", (canvas_w, canvas_h), BACKGROUND)
    for index, (x0, x1) in enumerate(PANEL_X):
        if x1 - x0 != canvas_w:
            raise ValueError(f"panel {index} width {x1 - x0} != {canvas_w}")
        crop = image.crop((x0, PANEL_Y[0], x1, PANEL_Y[1]))
        canvas.paste(crop, (0, index * (panel_h + GAP_PX)))
    canvas.save(DST)
    print("written", DST, canvas.size, "aspect h/w = %.4f" % (canvas.size[1] / canvas.size[0]))


if __name__ == "__main__":
    main()
