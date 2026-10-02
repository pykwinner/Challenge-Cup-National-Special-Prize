import re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def load(p):
    b = open(p, 'rb').read()
    for enc in ('utf-8', 'gb18030', 'big5'):
        try:
            return b.decode(enc)
        except Exception:
            pass
    return b.decode('utf-8', errors='ignore')

for p in sys.argv[1:]:
    h = load(p)
    h = re.sub(r'(?is)<(script|style|noscript).*?</\1>', ' ', h)
    h = re.sub(r'(?is)<br\s*/?>', '\n', h)
    h = re.sub(r'(?is)</(tr|div|p|li|h\d|table)>', '\n', h)
    h = re.sub(r'(?is)</t[dh]>', '\t', h)
    t = re.sub(r'(?s)<[^>]+>', ' ', h)
    for a, b in (('&nbsp;', ' '), ('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                 ('&#39;', "'"), ('&quot;', '"'), ('&deg;', '°')):
        t = t.replace(a, b)
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n\s*\n+', '\n', t)
    print(f"\n########## {p} ##########")
    print(t)
