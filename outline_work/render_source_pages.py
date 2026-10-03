from pathlib import Path
import pypdfium2 as pdfium

root = Path(__file__).resolve().parents[1]
path = root / 'CP-202611中国建筑科学研究院有限公司-面向供热格局变革的城镇供热源荷匹配与规划寻优模型构建比赛方案.pdf'
pdf = pdfium.PdfDocument(path)
for number in (4,5):
    pdf[number-1].render(scale=1.5).to_pil().save(root / 'outline_work' / f'competition_page_{number}.png')
print('Rendered scoring pages 4 and 5')
