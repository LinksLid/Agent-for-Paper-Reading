"""Verify a fresh source tree with Python stdlib only; no browser/model/network service."""
from pathlib import Path
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import urlopen


def smoke(root,launcher='python'):
    root=Path(root).resolve()
    for forbidden in ['.env','.venv','.runtime','output','external']:
        if (root/forbidden).exists():raise RuntimeError('Use a fresh release tree; unexpected '+forbidden)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    # -S excludes site-packages: the website must not depend on dotenv, LangGraph, etc.
    command=([sys.executable,'-S',str(root/'launch.py')] if launcher=='python' else
             ['cmd','/d','/c','start.bat'] if launcher=='bat' else ['sh','start.sh'])
    process=subprocess.Popen([*command,'--port',str(port),'--no-browser','--presentation-only'],
            cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
            start_new_session=os.name!='nt')
    try:
        deadline=time.monotonic()+25
        data=None
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError(process.stdout.read().decode('utf-8','replace'))
            try:
                with urlopen(f'http://127.0.0.1:{port}/api/data',timeout=1) as response:data=json.load(response)
                break
            except OSError:time.sleep(0.1)
        if data is None:raise RuntimeError('Presentation did not become ready')
        assert data['data_origin']['kind']=='public-demo'
        assert data['stats']['papers']==8 and data['stats']['accepted']==88
        assert len(data['results'])==8
        for suffix in ['/', '/assets/app.js','/data/snapshot.js','/downloads/report.md',
                       '/'+data['results'][0]['artifacts'][0]['url']]:
            with urlopen(f'http://127.0.0.1:{port}'+suffix,timeout=3) as response:assert response.status==200
        try:urlopen(f'http://127.0.0.1:{port}/.env',timeout=3)
        except HTTPError as error:assert error.code==404
        else:raise AssertionError('Private configuration route was exposed')
        assert not (root/'output').exists() and not (root/'.venv').exists()
        print(json.dumps(dict(status='passed',python=sys.version.split()[0],site_packages=False,
                              papers=8,accepted_claims=88,api_key_required=False,model_calls=0),ensure_ascii=False))
    finally:
        if os.name=='nt':
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        else:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--launcher',choices=['python','bat','sh'],default='python')
    args=parser.parse_args()
    smoke(args.root,args.launcher)
