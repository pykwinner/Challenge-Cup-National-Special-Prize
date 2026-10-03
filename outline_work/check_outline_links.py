from pathlib import Path
import re
import json

root = Path(__file__).resolve().parents[1]
results = []
for path in sorted((root / '论文与答辩大纲_20261003').glob('*.md')):
    text = path.read_text(encoding='utf-8')
    links = re.findall(r'\]\(([^)]+)\)', text)
    missing = []
    for link in links:
        if re.match(r'https?://', link):
            continue
        target = path.parent / link
        if not target.exists():
            missing.append(link)
    results.append({'file': path.name, 'characters': len(text), 'links': len(links), 'missing': missing, 'replacement_characters': text.count('\ufffd')})
print(json.dumps(results, ensure_ascii=True, indent=2))
assert not any(r['missing'] or r['replacement_characters'] for r in results)
