"""Acquire exact release source identities; never rewrite historical locks.

Only a matching SHA-256 and size admits a download. Source URLs may change
their response: failure is evidence of unavailability, not a new admission.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def fetch(root, manifest, groups, verify_only=False):
    records=[]
    for item in manifest['sources']:
        if groups and item['group'] not in groups: continue
        relative=Path(item['path'].replace('\\','/'))
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe manifest path')
        target=(root/relative).resolve()
        if not target.is_relative_to(root.resolve()): raise ValueError('Outside output root')
        if target.is_file():
            if target.stat().st_size!=item['bytes'] or sha(target)!=item['sha256']:
                raise ValueError(f'Existing file identity mismatch: {relative}')
            records.append({'path':relative.as_posix(),'status':'existing_identity_verified'})
            continue
        if verify_only: raise FileNotFoundError(target)
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_name(target.name+'.fetch-part')
        if temporary.exists(): raise FileExistsError(temporary)
        errors=[]
        for url in item['urls']:
            try:
                request=urllib.request.Request(url,headers={'User-Agent':'Heatplan-source-identity-verifier/11.12.0rc2'})
                with urllib.request.urlopen(request,timeout=90) as response, temporary.open('wb') as output:
                    while block:=response.read(1024*1024): output.write(block)
                if temporary.stat().st_size!=item['bytes'] or sha(temporary)!=item['sha256']:
                    raise ValueError('Downloaded bytes do not match frozen size/SHA256')
                temporary.replace(target)
                records.append({'path':relative.as_posix(),'status':'downloaded_identity_verified','url':url})
                break
            except Exception as exc:
                errors.append(f'{type(exc).__name__}: {exc}')
                if temporary.exists(): temporary.unlink()
        else:
            raise RuntimeError(f'Cannot acquire frozen source {relative}: {errors}')
        print(f'Verified {relative}',flush=True)
    return records

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,default=ROOT/'docs/github_release_11.12.0rc2/external_sources.json')
    parser.add_argument('--output-root',type=Path,default=ROOT)
    parser.add_argument('--group',action='append',choices=['bdg2','reference','method'])
    parser.add_argument('--verify-only',action='store_true')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    if args.report and args.report.exists(): raise FileExistsError(args.report)
    manifest=json.loads(args.manifest.read_text(encoding='utf-8'))
    records=fetch(args.output_root,manifest,set(args.group or []),args.verify_only)
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps({'records':records,'verified':len(records)},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'{len(records)} exact source identities verified')

if __name__=='__main__': main()
