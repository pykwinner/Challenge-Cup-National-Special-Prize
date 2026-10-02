import sys, io, re, html
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
def load(p):
    b = open(p,'rb').read()
    for enc in ('utf-8','gbk','gb18030','big5'):
        try:
            s = b.decode(enc)
            if s.count('�') < len(s)*0.01:
                return s, enc
        except Exception: pass
    return b.decode('utf-8',errors='replace'), 'utf-8-replace'
if __name__ == '__main__':
    p = sys.argv[1]
    s, enc = load(p)
    print('#ENC', enc, 'LEN', len(s))
    if len(sys.argv) > 2 and sys.argv[2] == 'text':
        s = re.sub(r'<script.*?</script>','',s,flags=re.S|re.I)
        s = re.sub(r'<style.*?</style>','',s,flags=re.S|re.I)
        s = re.sub(r'<[^>]+>',' ',s)
        s = html.unescape(s)
        s = re.sub(r'[ \t\xa0]+',' ',s)
        s = re.sub(r'\n\s*\n+','\n',s)
        print(s)
    else:
        print(s)
