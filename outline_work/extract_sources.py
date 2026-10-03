from pathlib import Path
import json
from pypdf import PdfReader

root = Path(__file__).resolve().parents[1]
out = root / 'outline_work'
sources = {
    'competition': root / 'CP-202611中国建筑科学研究院有限公司-面向供热格局变革的城镇供热源荷匹配与规划寻优模型构建比赛方案.pdf',
    'current_draft': root / '现稿.pdf',
    'paper': root / 'deliverables/paper/paper.pdf',
}
for name, path in sources.items():
    reader = PdfReader(path)
    chunks = [f'\n\n===== PDF PAGE {i+1} =====\n\n' + (p.extract_text() or '') for i,p in enumerate(reader.pages)]
    (out / f'{name}.txt').write_text(''.join(chunks), encoding='utf-8')
    print(json.dumps({'source': str(path), 'pages': len(reader.pages), 'characters': sum(map(len, chunks)), 'text': str(out / f'{name}.txt')},ensure_ascii=False))
