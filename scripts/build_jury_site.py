"""Package ONLY the public jury build and sanitized historical projections."""
import argparse
import base64
import gzip
import hashlib
import json
import mimetypes
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def build(ui, bundle, site):
    ui,bundle,site=ui.resolve(),bundle.resolve(),site.resolve()
    if not ui.is_relative_to(ROOT/'.test-tmp') or not site.is_relative_to(ROOT/'.tools'):
        raise ValueError('Use a new isolated jury build and Site checkout')
    hosting=json.loads((site/'.openai/hosting.json').read_text())
    if not hosting.get('project_id') or hosting.get('static'):
        raise ValueError('Expected the selected Worker Site manifest')
    data=json.loads(bundle.read_text(encoding='utf-8'))
    if data.get('mode')!='historical_readonly' or len(data.get('catalog',[]))<1:
        raise ValueError('Expected a validated historical replay export')
    assets={}
    for path in sorted(ui.rglob('*')):
        if not path.is_file():continue
        if path.suffix not in {'.html','.js','.css','.woff','.woff2','.svg','.png','.jpg','.ico'}:
            raise ValueError('Unexpected public asset type')
        name=path.relative_to(ui).as_posix()
        kind={'.js':'application/javascript; charset=utf-8','.css':'text/css; charset=utf-8',
              '.woff':'font/woff','.woff2':'font/woff2','.svg':'image/svg+xml'}.get(path.suffix)
        assets[name]={'type':kind or mimetypes.guess_type(name)[0] or 'application/octet-stream',
                      'body':base64.b64encode(path.read_bytes()).decode()}
    if 'index.html' not in assets:raise ValueError('Missing jury frontend')
    source=(ROOT/'deployment/jury/worker.mjs').read_text(encoding='utf-8')
    module=source+'\nconst DATA='+json.dumps(data,ensure_ascii=False,separators=(',',':'))+';\nconst ASSETS='+json.dumps(assets,separators=(',',':'))+';\nexport default createWorker(DATA,ASSETS);\n'
    compressed=len(gzip.compress(module.encode()))
    if compressed>3_000_000:raise ValueError('Conservative free-worker compressed size budget exceeded')
    (site/'dist/server').mkdir(parents=True,exist_ok=True)
    (site/'dist/.openai').mkdir(parents=True,exist_ok=True)
    (site/'dist/server/index.js').write_text(module,encoding='utf-8')
    (site/'dist/.openai/hosting.json').write_text(json.dumps(hosting,indent=2),encoding='utf-8')
    (site/'worker.mjs').write_text(source,encoding='utf-8')
    (site/'replay.json').write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    (site/'package.json').write_text(json.dumps({'name':'undrift-jury-demo','private':True,'type':'module'},indent=2))
    # Hash the actual bytes, including the host's newline convention, rather than
    # the pre-write string. The saved deployment archive must match this record.
    written=(site/'dist/server/index.js').read_bytes()
    manifest={'snapshot_id':data['snapshot_id'],'assets':len(assets),'module_bytes':len(written),
              'gzip_bytes':len(gzip.compress(written)),'worker_sha256':hashlib.sha256(written).hexdigest(),
              'mode':data['mode'],'source_databases':[v['database_id'] for v in data['catalog']]}
    (site/'PUBLIC-REPLAY.json').write_text(json.dumps(manifest,indent=2))
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ui',type=Path,required=True);parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--site',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.ui,args.bundle,args.site),indent=2))
