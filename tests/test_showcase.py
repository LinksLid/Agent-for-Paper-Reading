import json
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from paperteam.showcase import READING, ShowcaseHandler, build_data, export_snapshot, saved_reports, saved_report


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def recorded_project(tmp_path):
    chunk=dict(chunk_id='paper:p1:c0',page=1,text='An actual continuous quotation.')
    claim=dict(paper_id='paper',text='A saved claim.',kind='method',origin_role='方法分析员',evidence=[dict(chunk_id=chunk['chunk_id'],quote='continuous quotation')])
    state=dict(papers=[dict(paper_id='paper',filename='MAC-SQL.pdf',pages=1,sha256='abc',chunks=[chunk])],accepted=[claim],rejected=[],paper_guides=[],events=[])
    save(tmp_path/READING/'state.json',state)
    save(tmp_path/READING/'manifest.json',dict(status='completed',api_calls=2))
    folder=tmp_path/'output/run/paper'
    save(folder/'artifacts/eval_result.json',[dict(res=1),dict(res=0)])
    save(folder/'result.json',dict(artifact_root=str(folder),artifacts=['artifacts/eval_result.json']))
    save(tmp_path/'output/experiments-20261101/summary.json',dict(tests_passed=80,results=[dict(paper_id='paper',filename='MAC-SQL.pdf',status='completed',run='run',metrics=[dict(name='execution_accuracy',value=0.5)])]))
    (folder/'api_usage.jsonl').write_text(json.dumps(dict(agent='同济接口连通性检查',call=1))+'\n',encoding='utf-8')
    (tmp_path/'.env').write_text('API_KEY=sentinel-do-not-export',encoding='utf-8')
    return tmp_path


def test_showcase_uses_recorded_scores_and_never_exports_credentials(recorded_project):
    data=build_data(recorded_project)
    assert data['stats']['accepted']==1
    assert data['results'][0]['sample_count']==2
    assert data['results'][0]['sample_scores']==[1,0]
    assert data['claims'][0]['evidence'][0]['exact']
    assert 'sentinel-do-not-export' not in json.dumps(data)
    assert str(recorded_project) not in json.dumps(data)
    assert data['usage']==[dict(name='连接检查',value=1)]


def test_missing_runs_do_not_create_demonstration_statistics(tmp_path):
    data=build_data(tmp_path)
    assert data['stats']['papers']==data['stats']['accepted']==data['stats']['completed']==0
    assert data['claims']==data['timeline']==data['results']==[]


def test_saved_reports_include_runs_without_web_jobs_and_reject_unsafe_paths(tmp_path):
    older=tmp_path/'output/reading-older';older.mkdir(parents=True)
    (older/'report.md').write_text('# Older',encoding='utf-8')
    current=tmp_path/'output/reading-new';current.mkdir(parents=True)
    (current/'report.md').write_text('# New report',encoding='utf-8')
    save(current/'manifest.json',dict(question='Read this paper',model='test-model',papers=[dict(filename='paper.pdf')],api_key='secret'))
    reports=saved_reports(tmp_path)
    assert [r['id'] for r in reports]==['reading-new','reading-older']
    assert reports[0]['papers']==['paper.pdf']
    assert 'secret' not in json.dumps(reports)
    assert saved_report(tmp_path,'reading-new')['report']=='# New report'
    with pytest.raises(FileNotFoundError):saved_report(tmp_path,'../reading-new')


def test_offline_snapshot_escapes_script_delimiters(recorded_project):
    path=recorded_project/READING/'state.json'
    state=json.loads(path.read_text(encoding='utf-8'))
    state['accepted'][0]['text']='</script><script>alert(1)</script>'
    save(path,state)
    output=export_snapshot(recorded_project,recorded_project/'site')
    text=output.read_text(encoding='utf-8')
    assert '</script>' not in text
    data=json.loads(text.removeprefix('window.PAPERTEAM_DATA=').rstrip(';\n'))
    assert data['claims'][0]['text']==state['accepted'][0]['text']


@pytest.fixture
def presentation_server(recorded_project):
    site=recorded_project/'site';site.mkdir()
    (site/'index.html').write_text('<h1>PaperTeam</h1>',encoding='utf-8')
    (site/'.env').write_text('secret',encoding='utf-8')
    handler=partial(ShowcaseHandler,root=recorded_project,site=site)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown();server.server_close();thread.join(timeout=3)


def test_presentation_api_serves_evidence_and_registered_artifacts(presentation_server):
    with urlopen(presentation_server+'/api/data') as response:
        assert json.load(response)['stats']['accepted']==1
    with urlopen(presentation_server+'/artifacts/paper/eval_result.json') as response:
        assert [x['res'] for x in json.load(response)]==[1,0]


def test_presentation_api_lists_and_reads_saved_reports(presentation_server, recorded_project):
    run=recorded_project/'output/saved-reading'
    run.mkdir()
    (run/'report.md').write_text('# Historical report',encoding='utf-8')
    with urlopen(presentation_server+'/api/reports') as response:
        assert 'saved-reading' in [r['id'] for r in json.load(response)['reports']]
    with urlopen(presentation_server+'/api/reports/saved-reading') as response:
        assert json.load(response)['report']=='# Historical report'


@pytest.mark.parametrize('path',['/.env','/../.env','/%2e%2e/.env','/downloads/../../.env','/artifacts/paper/unknown.json'])
def test_presentation_server_rejects_unregistered_files(presentation_server,path):
    with pytest.raises(HTTPError) as error:
        urlopen(presentation_server+path)
    assert error.value.code==404
