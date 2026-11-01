import json
from functools import partial
from http.server import ThreadingHTTPServer
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from paperteam.showcase import ShowcaseHandler
from paperteam.web_jobs import JobService, identifier, write, read, execute


@pytest.fixture
def web(tmp_path):
    site=tmp_path/'showcase';site.mkdir()
    (site/'index.html').write_text('<h1>Reading</h1>',encoding='utf-8')
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(ShowcaseHandler,root=tmp_path,site=site))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}',tmp_path
    server.shutdown();server.server_close();thread.join(3)


def request(base,path,body,headers=None):
    if not isinstance(body,bytes):body=json.dumps(body).encode()
    with urlopen(Request(base+path,data=body,headers=headers or {},method='POST')) as response:
        return json.load(response)


def test_http_upload_validates_pdf_and_does_not_expose_paths(web):
    base,root=web
    meta=request(base,'/api/uploads',b'%PDF-1.7\nfixture',{'X-Filename':'..%2Fsample.pdf'})
    assert meta['filename']=='sample.pdf'
    assert str(root) not in json.dumps(meta)
    with urlopen(base+'/api/uploads/'+meta['id']+'/paper.pdf') as response:
        assert response.read()==b'%PDF-1.7\nfixture'
    with pytest.raises(HTTPError) as error:
        request(base,'/api/uploads',b'<html>not a PDF</html>')
    assert error.value.code==400
    with pytest.raises(HTTPError) as error:
        urlopen(base+'/api/jobs/../../.env')
    assert error.value.code==404


def test_browser_cannot_write_settings_from_another_origin(web):
    base,root=web
    with pytest.raises(HTTPError) as error:
        request(base,'/api/settings',{}, {'Origin':'https://untrusted.example'})
    assert error.value.code==403
    assert not (root/'.env').exists()


def test_settings_save_key_locally_without_returning_it(tmp_path,monkeypatch):
    for name in ['OPENAI_API_KEY','API_KEY','OPENAI_BASE_URL','BASE_URL','Base_url','MODEL','OPENAI_MODEL']:
        monkeypatch.delenv(name,raising=False)
    service=JobService(tmp_path);service._deps=True;service._checked=__import__('time').monotonic()
    data=dict(base_url='https://model.example/v1',model='chat',api_key='private-test-key-012345')
    result=service.save_settings(data)
    assert result['configured']
    assert 'private-test-key' not in json.dumps(result)
    assert 'private-test-key' in (tmp_path/'.env').read_text()
    result=service.save_settings({**data,'api_key':''})
    assert result['configured']


def test_duplicate_uploads_and_invalid_ids_rejected(tmp_path):
    service=JobService(tmp_path)
    a=service.upload('paper.pdf',b'%PDF-1.7\na')
    b=service.upload('renamed.pdf',b'%PDF-1.7\na')
    with pytest.raises(ValueError,match='重复'):
        service.uploads([a['id'],b['id']])
    for value in ['../secrets','x'*32,None]:
        with pytest.raises(ValueError):identifier(value)


def test_completed_job_report_exposes_only_selected_results(web):
    base,root=web;service=JobService(root);job_id='a'*32;run_id='web-'+job_id
    write(service.folder/job_id/'job.json',dict(id=job_id,run_id=run_id,status='completed',events=[],nodes={}))
    output=root/'output'/run_id;output.mkdir(parents=True)
    (output/'report.md').write_text('# report',encoding='utf-8')
    write(output/'state.json',dict(paths=['C:/private/file'],accepted=[],papers=[]))
    write(output/'manifest.json',dict(model='test',api_key='must-not-expose'))
    with urlopen(base+'/api/jobs/'+job_id+'/result') as response:data=json.load(response)
    assert data['report']=='# report' and data['report_ready']
    assert 'private' not in json.dumps(data) and 'must-not-expose' not in json.dumps(data)
    with urlopen(base+'/api/jobs/'+job_id+'/report.md') as response:
        assert response.read()==b'# report'


def test_worker_rejects_empty_pdf_before_making_model_calls(tmp_path,monkeypatch):
    from pypdf import PdfWriter
    import io
    import paperteam.runner
    buffer=io.BytesIO();writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.write(buffer)
    service=JobService(tmp_path);meta=service.upload('empty.pdf',buffer.getvalue());job_id='b'*32
    write(service.folder/job_id/'job.json',dict(id=job_id,status='queued',files=[meta],nodes={},events=[]))
    monkeypatch.setattr(paperteam.runner,'run',lambda *a,**k:pytest.fail('must not invoke model pipeline'))
    execute(tmp_path,job_id)
    job=read(service.folder/job_id/'job.json')
    assert job['status']=='failed' and 'OCR' in job['error']
    assert not (tmp_path/'output').exists()


def test_worker_observes_actual_nodes_and_saves_completed_job(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import paperteam.runner
    import paperteam.config
    import pypdf
    service=JobService(tmp_path);meta=service.upload('paper.pdf',b'%PDF-1.7\ntest');job_id='c'*32
    write(service.folder/job_id/'job.json',dict(id=job_id,status='queued',files=[meta],nodes={},events=[],
          max_calls=40,mode='verified',explain=True,question='test question',run_id='web-'+job_id))
    monkeypatch.setattr(pypdf,'PdfReader',lambda _:SimpleNamespace(is_encrypted=False,pages=[SimpleNamespace(extract_text=lambda:'paper text')]))
    monkeypatch.setattr(paperteam.config.Settings,'load',lambda *a,**k:paperteam.config.Settings(root=tmp_path,api_key='secret'))
    def run(*args,**kwargs):
        kwargs['on_node_start']('planner')
        assert read(service.folder/job_id/'job.json')['nodes']['planner']['status']=='running'
        kwargs['progress']('planner',{})
        kwargs['on_node_start']('verifier')
        kwargs['progress']('verifier',dict(accepted=[{}],rejected=[]))
        return {},tmp_path
    monkeypatch.setattr(paperteam.runner,'run',run)
    execute(tmp_path,job_id)
    job=service.get(job_id)
    assert job['status']=='completed' and job['accepted_claims']==1
    assert [(e['node'],e['status']) for e in job['events']]==[
        ('planner','running'),('planner','completed'),('verifier','running'),('verifier','completed')]


def test_active_job_prevents_concurrent_model_runs(tmp_path,monkeypatch):
    service=JobService(tmp_path);meta=service.upload('paper.pdf',b'%PDF-1.7\ntest')
    job_id='d'*32
    write(service.folder/job_id/'job.json',dict(id=job_id,run_id='web-'+job_id,status='running',
          created_at='2026-11-01',files=[meta],nodes={},events=[]))
    monkeypatch.setattr(service,'runtime',lambda:dict(configured=True,dependencies_ready=True))
    with pytest.raises(ValueError,match='运行中'):
        service.start(dict(upload_ids=[meta['id']],question='test question'))


def test_cancel_terminates_owned_worker_and_marks_running_nodes(tmp_path,monkeypatch):
    import os
    import paperteam.web_jobs as module
    service=JobService(tmp_path);job_id='e'*32
    write(service.folder/job_id/'job.json',dict(id=job_id,run_id='web-'+job_id,status='running',
          nodes={'planner':{'status':'running'}},events=[]))
    actions=[]
    class Worker:
        pid=12345
        def poll(self):return None
        def terminate(self):actions.append('terminate')
        def wait(self,timeout):actions.append('wait')
    service.processes[job_id]=Worker()
    monkeypatch.setattr(module.subprocess,'run',lambda args,**kwargs:actions.append(args))
    job=service.stop(job_id)
    assert job['status']=='cancelled' and job['nodes']['planner']['status']=='cancelled'
    if os.name=='nt':assert actions[0]==['taskkill','/PID','12345','/T','/F']
    else:assert actions[0]=='terminate'
    assert actions[-1]=='wait'
