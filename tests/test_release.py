import json
import os
from pathlib import Path

from paperteam.showcase import ROOT, READING, build_data, export_snapshot, reading_directory
import launch
import pytest


def demo_fixture(root):
    (root/'examples').mkdir()
    data=json.loads((ROOT/'examples/public-demo.json').read_text(encoding='utf-8'))
    (root/'examples/public-demo.json').write_text(json.dumps(data),encoding='utf-8')
    return data


def test_fresh_download_uses_explicit_demo_without_private_outputs(tmp_path):
    expected=demo_fixture(tmp_path)
    data=build_data(tmp_path)
    assert data['data_origin']['kind']=='public-demo'
    assert data['stats']==expected['stats']
    assert not (tmp_path/'output').exists()
    assert not (tmp_path/'.env').exists()


def test_demo_export_needs_no_model_configuration(tmp_path):
    demo_fixture(tmp_path)
    snapshot=export_snapshot(tmp_path,tmp_path/'showcase',mode='demo')
    assert 'public-demo' in snapshot.read_text(encoding='utf-8')
    assert not (tmp_path/'.env').exists()


def test_local_mode_does_not_fabricate_a_personal_run(tmp_path):
    demo_fixture(tmp_path)
    data=build_data(tmp_path,mode='local')
    assert data['stats']['papers']==0
    assert data['data_origin']['kind']=='local'


def test_latest_completed_reading_replaces_machine_specific_run_name(tmp_path):
    demo_fixture(tmp_path)
    for name,timestamp in [('20261101-old',1000),('my-new-reading',2000)]:
        folder=tmp_path/'output'/name;folder.mkdir(parents=True)
        (folder/'manifest.json').write_text(json.dumps(dict(status='completed',model='personal-model')),encoding='utf-8')
        (folder/'state.json').write_text(json.dumps(dict(papers=[dict(paper_id=name,filename='custom.pdf',pages=2,chunks=[])],accepted=[],rejected=[],paper_guides=[],events=[])),encoding='utf-8')
        os.utime(folder/'manifest.json',(timestamp,timestamp))
    assert reading_directory(tmp_path).name=='my-new-reading'
    data=build_data(tmp_path)
    assert data['data_origin']['kind']=='local'
    assert data['papers'][0]['id']=='my-new-reading'
    assert data['model']=='personal-model'


def test_demo_launcher_opens_browser_without_installing_agent_runtime(monkeypatch):
    import paperteam.showcase
    seen=[]
    monkeypatch.setattr(paperteam.showcase,'main',lambda args:seen.extend(args))
    monkeypatch.setattr(launch,'workbench_python',lambda:(_ for _ in ()).throw(AssertionError('unexpected pip setup')))
    launch.main(['--port','8655','--presentation-only'])
    assert '--open' in seen and seen[seen.index('--port')+1]=='8655'


def test_default_launcher_prepares_real_agent_runtime(monkeypatch):
    import paperteam.showcase
    seen=[]
    monkeypatch.setattr(paperteam.showcase,'main',lambda args:None)
    monkeypatch.setattr(launch,'workbench_python',lambda:seen.append('prepared'))
    launch.main(['--no-browser'])
    assert seen==['prepared']


@pytest.mark.parametrize('open_browser',[True,False])
def test_server_opens_browser_only_when_requested_and_keeps_snapshot_public(monkeypatch,open_browser):
    import paperteam.showcase as site
    opened=[];exported=[];closed=[]
    monkeypatch.setattr(site,'export_snapshot',lambda **kwargs:exported.append(kwargs['mode']))
    def unavailable(*args,**kwargs):raise OSError('no existing server')
    monkeypatch.setattr(site,'urlopen',unavailable)
    monkeypatch.setattr(site.webbrowser,'open',lambda url:opened.append(url))
    class Timer:
        def __init__(self,delay,callback,args):self.callback=callback;self.args=args
        def start(self):self.callback(*self.args)
    class Server:
        def __init__(self,*args):pass
        def serve_forever(self):raise KeyboardInterrupt
        def server_close(self):closed.append(True)
    monkeypatch.setattr(site.threading,'Timer',Timer)
    monkeypatch.setattr(site,'PresentationServer',Server)
    site.main(['--port','8699','--data-mode','local']+(['--open'] if open_browser else []))
    assert opened==(['http://127.0.0.1:8699'] if open_browser else [])
    assert exported==['demo']
    assert closed==[True]
