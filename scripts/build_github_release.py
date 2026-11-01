"""Create an allowlisted, clean source release; never copy a live workspace wholesale."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from paperteam.showcase import export_snapshot

FILES=['README.md','NOTICE.md','launch.py','index.html','start.bat','start.sh','app.py','pyproject.toml',
       'requirements.txt','requirements-lock.txt','.gitignore','.gitattributes','.dockerignore',
       'Dockerfile','compose.yaml','.env.example','deepseek.env.example','tongji.env.example',
       '.streamlit/config.toml','config/paper-catalog.json','examples/public-demo.json','showcase/README.md',
       'scripts/setup.ps1','scripts/start.ps1','scripts/start-showcase.ps1','scripts/download_paper_catalog.py',
       'scripts/build_github_release.py','scripts/smoke_release.py',
       'docs/user-guide.md','docs/github-release.md','docs/demo-data.md','docs/technical-reference.md','docs/release-validation.md',
       'docs/maintainer-results-20261101.md','docs/人工评测与课堂演示清单.md',
       'docs/evaluation.md','docs/team-project.md','docs/validation.md']


def package(destination):
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Use a new empty release path; no existing directory will be overwritten')
    if destination.is_relative_to(ROOT/'showcase') or destination==ROOT:raise ValueError('Invalid release directory')
    payload={}
    for name in FILES:
        f=ROOT/name
        if not f.is_file():raise FileNotFoundError(name)
        payload[name]=f.read_bytes()
    for pattern in ['paperteam/*.py','tests/*.py','docker/*.Dockerfile','.github/workflows/*.yml',
                    'showcase/*.html','showcase/assets/*','showcase/data/artifacts/*.json','docs/assets/*.jpg','docs/assets/*.png']:
        for f in ROOT.glob(pattern):
            if f.is_file():payload[f.relative_to(ROOT).as_posix()]=f.read_bytes()
    # Scan against values kept in excluded local configuration without importing model dependencies.
    secrets=[]
    for f in [ROOT/'.env',ROOT/'deepseek.env']:
        if f.is_file():
            for line in f.read_text(encoding='utf-8-sig').splitlines():
                if '=' in line and any(label in line.split('=',1)[0].upper() for label in ['KEY','TOKEN','SECRET']):
                    value=line.split('=',1)[1].strip().strip('"\'')
                    if len(value)>=12:secrets.append(value.encode())
    for name,raw in payload.items():
        if any(secret in raw for secret in secrets):raise RuntimeError('Credential found in selected file: '+name)
    destination.mkdir(parents=True)
    for name,raw in payload.items():
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    (destination/'paper').mkdir()
    (destination/'paper/README.md').write_text('Place your own PDF files here, or run scripts/download_paper_catalog.py after installing requirements. Full PDFs are not included in Git.\n',encoding='utf-8')
    export_snapshot(destination,destination/'showcase',mode='demo')
    for f in destination.rglob('*'):
        if f.is_file() and any(secret in f.read_bytes() for secret in secrets):raise RuntimeError('Credential found after export')
    manifest={f.relative_to(destination).as_posix():hashlib.sha256(f.read_bytes()).hexdigest()
              for f in destination.rglob('*') if f.is_file()}
    (destination/'RELEASE_MANIFEST.json').write_text(json.dumps(dict(version='0.3.0',
         upstream_commit='dcf0e17bb7312b3758df12a1f339b19a5857f7b2',files=manifest),ensure_ascii=False,indent=2),encoding='utf-8')
    archive=destination.with_suffix('.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for f in destination.rglob('*'):
            if f.is_file():z.write(f,destination.name+'/'+f.relative_to(destination).as_posix())
    print(json.dumps(dict(directory=str(destination),archive=str(archive),files=len(manifest),
         credential_scan='passed',sha256=hashlib.sha256(archive.read_bytes()).hexdigest()),ensure_ascii=False))
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination',type=Path)
    package(parser.parse_args().destination)
