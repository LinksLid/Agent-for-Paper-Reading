"""Start the real reading website; install its isolated runtime on first launch."""
from pathlib import Path
import argparse
import importlib.util
import os
import subprocess
import sys
import venv

ROOT=Path(__file__).resolve().parent


def workbench_python(root=ROOT):
    executable=Path(root)/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not executable.is_file():
        print('Creating .venv for the reading agents...',flush=True)
        venv.EnvBuilder(with_pip=True).create(Path(root)/'.venv')
    check=subprocess.run([str(executable),'-c','import streamlit,langgraph,pypdf,httpx,dotenv; from langgraph.checkpoint.sqlite import SqliteSaver'],capture_output=True)
    if check.returncode:
        print('Installing reading-agent requirements (first run needs internet)...',flush=True)
        subprocess.run([str(executable),'-m','pip','install','-r',str(Path(root)/'requirements.txt')],check=True)
    return executable


def main(argv=None):
    if sys.version_info<(3,11):
        raise SystemExit('PaperTeam needs Python 3.11 or newer. Python 3.12 is recommended.')
    parser=argparse.ArgumentParser(description='Start PaperTeam: upload PDFs, run agents, download reports.')
    parser.add_argument('--port',type=int,default=8600)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--data-mode',choices=['auto','demo','local'],default='auto')
    parser.add_argument('--workbench',action='store_true',help='Also install/start the optional Streamlit model workbench on port 8501')
    parser.add_argument('--presentation-only',action='store_true',help='Skip dependency setup; historical demonstration only')
    args=parser.parse_args(argv)
    if not args.presentation_only:
        workbench_python()
    if args.workbench:
        from urllib.request import urlopen
        running=False
        try:
            with urlopen('http://127.0.0.1:8501/_stcore/health',timeout=2) as response:running=response.status==200
        except OSError:pass
        if not running:
            python=workbench_python()
            logs=ROOT/'.cache';logs.mkdir(exist_ok=True)
            with (logs/'workbench.log').open('ab') as output:
                subprocess.Popen([str(python),'-m','streamlit','run',str(ROOT/'app.py'),
                    '--server.address','127.0.0.1','--server.port','8501','--server.headless','true',
                    '--browser.gatherUsageStats','false'],cwd=ROOT,stdout=output,stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        print('Model workbench: http://127.0.0.1:8501 (real reading needs your own .env and PDFs)',flush=True)
    from paperteam.showcase import main as serve
    params=['--port',str(args.port),'--host',args.host,'--data-mode',args.data_mode]
    if not args.no_browser:params.append('--open')
    serve(params)


if __name__=='__main__':main()
