import json
import time
from pathlib import Path

import pytest

from paperteam.selection_qa import (anchors, plain, QuestionService, QuestionPlan, AnswerDraft,
                                     ExampleReview, answer_graph)
from paperteam.web_jobs import JobService, write, read
from paperteam.models import Claim, Evidence, Review, Verdict

QUOTE='The selector chooses relevant tables and columns.'
PAPER=dict(paper_id='p',filename='sample.pdf',pages=1,chunks=[dict(paper_id='p',chunk_id='p:p1:c0',page=1,text=QUOTE)])
REPORT='## sample.pdf\n\n**Selector** 选择表列。 [^S001]\n\n- 输入：问题与模式。\n\n## 实验复现\n\n状态 disabled\n\n[^S001]: sample.pdf，PDF p.1，`p:p1:c0`。\n'


@pytest.fixture
def saved(tmp_path):
    job_id='a'*32
    jobs=JobService(tmp_path)
    folder=tmp_path/'output'/('web-'+job_id);folder.mkdir(parents=True)
    write(jobs.folder/job_id/'job.json',dict(id=job_id,run_id='web-'+job_id,status='completed',files=[dict(id='b'*32,filename='sample.pdf')]))
    write(folder/'state.json',dict(papers=[PAPER],accepted=[]))
    (folder/'report.md').write_text(REPORT,encoding='utf-8')
    return QuestionService(jobs),job_id


def payload(service, job_id):
    index=service.context(job_id)[2]
    block=next(b for b in index['blocks'] if b['text'].startswith('Selector'))
    return dict(version=index['version'],block_id=block['id'],selection='选择表列',question='这句话是什么意思？')


class FakeLLM:
    calls=0
    model='fixture'
    records=[]
    models_used=[dict(provider='fixture',model='fixture')]
    settings=type('Config',(),{'api_key':'private-test-key'})()
    def __init__(self,*args):self.calls=0;self.records=[]
    def close(self):pass
    def structured(self, role, prompt, schema, **kwargs):
        self.calls+=1
        self.records.append(dict(usage=dict(total_tokens=5)))
        if schema is QuestionPlan:return QuestionPlan(queries=['selector tables columns'])
        if schema is AnswerDraft:return AnswerDraft(points=[Claim(paper_id='p',kind='method',text='Selector选择相关表列。',evidence=[Evidence(chunk_id='p:p1:c0',quote=QUOTE)])],example='教学假想：有学生表和课程表，只选择相关表。')
        if schema is Review:
            material=json.loads(prompt.split('\n',1)[1])
            return Review(verdicts=[Verdict(claim_index=i,supported=True,reason='fixture') for i in range(len(material))])
        if schema is ExampleReview:return ExampleReview(consistent=True)
        raise AssertionError(schema)


def test_anchor_normalization_and_paper_ownership():
    state=dict(papers=[PAPER],accepted=[dict(paper_id='p',text='结论',evidence=[dict(chunk_id='p:p1:c0',quote=QUOTE)])])
    index=anchors(REPORT,state)
    first=next(b for b in index['blocks'] if b['text'].startswith('Selector'))
    assert first['text']=='Selector 选择表列。 [S001]'
    assert first['source_ids']==['p:p1:c0']
    assert all(b['paper_id']=='p' and 'disabled' not in b['text'] for b in index['blocks'])
    assert any(b['kind']=='evidence' and b['text']==QUOTE for b in index['blocks'])
    assert index['version']!=anchors(REPORT+'changed',state)['version']


@pytest.mark.parametrize('change,match',[
    ({'version':'old'},'报告已更新'),({'block_id':'unknown'},'选文定位'),
    ({'selection':'本文没有这句话'},'选文定位'),({'selection':'a'},'选文定位'),
    ({'question':'a'},'问题应为'),({'selection':'x'*1700},'选文定位'),
    ({'parent_id':'../other'},'无效')])
def test_invalid_or_forged_context_rejected_before_model(saved,change,match):
    service,job_id=saved
    with pytest.raises(ValueError,match=match):service.start(job_id,{**payload(service,job_id),**change})
    assert service.list(job_id)==[]


def test_not_ready_job_rejected(saved):
    service,job_id=saved
    path=service.jobs.folder/job_id/'job.json';job=read(path);job['status']='failed';write(path,job)
    with pytest.raises(ValueError,match='报告生成'):service.context(job_id)


def test_async_answer_persists_and_followup_uses_same_anchor(saved,monkeypatch):
    import paperteam.llm
    monkeypatch.setattr(paperteam.llm,'LLM',FakeLLM)
    monkeypatch.setattr('paperteam.config.Settings.load',lambda *a,**k: __import__('paperteam.config',fromlist=['Settings']).Settings(root=saved[0].jobs.root,model='fixture'))
    service,job_id=saved
    data=payload(service,job_id)
    first=service.start(job_id,data)
    deadline=time.monotonic()+5
    while service.get(job_id,first['id'])['status'] not in {'completed','failed'} and time.monotonic()<deadline:time.sleep(.01)
    result=service.get(job_id,first['id'])
    assert result['status']=='completed'
    assert result['points'][0]['evidence'][0]['upload_id']=='b'*32
    assert result['calls']==4 and result['total_tokens']==20
    assert result['example'] and 'audit' not in result
    assert first['status']=='queued' and 'audit' not in first
    assert QuestionService(service.jobs).list(job_id)[0]['points']==result['points']
    bad={**data,'selection':'Selector','parent_id':first['id']}
    with pytest.raises(ValueError,match='追问与当前选文不一致'):service.start(job_id,bad)
    follow=service.start(job_id,{**data,'parent_id':first['id'],'question':'再简单一点'})
    assert follow['parent_id']==first['id']
    # Join before pytest removes the temporary directory.
    deadline=time.monotonic()+5
    while (job_id,follow['id']) in service.running and time.monotonic()<deadline:time.sleep(.01)
    assert service.get(job_id,follow['id'])['status']=='completed'


def test_unsupported_facts_and_inconsistent_example_never_displayed():
    class Wrong(FakeLLM):
        def structured(self, role,prompt,schema,**kwargs):
            if schema is AnswerDraft:return AnswerDraft(points=[Claim(paper_id='p',kind='method',text='不得展示的虚构解释',evidence=[Evidence(chunk_id='p:p1:c0',quote='this quote is fabricated')])],example='不得展示的假例子')
            return super().structured(role,prompt,schema,**kwargs)
    block=dict(text='Selector选择表列',source_ids=['p:p1:c0'])
    result=answer_graph(Wrong(),PAPER,block,'选择表列','为什么',[],lambda x:None).invoke({})
    assert not result['accepted'] and not result['example'] and result['rejected']


def test_restart_marks_unfinished_question_as_failed(saved):
    service,job_id=saved;qid='c'*32
    write(service.folder(job_id)/qid/'question.json',dict(id=qid,status='running',created_at='now'))
    assert service.get(job_id,qid)['status']=='failed'
    assert '重启' in service.get(job_id,qid)['error']


def test_cross_job_question_cannot_be_used_as_parent(saved):
    service,job_id=saved
    with pytest.raises(FileNotFoundError):service.start(job_id,{**payload(service,job_id),'parent_id':'c'*32})


def test_existing_question_prevents_duplicate_requests(saved):
    service,job_id=saved;qid='c'*32
    write(service.folder(job_id)/qid/'question.json',dict(id=qid,status='running',created_at='now'))
    service.running.add((job_id,qid))
    with pytest.raises(ValueError,match='已有问题'):service.start(job_id,payload(service,job_id))


def test_other_paper_claim_and_inconsistent_example_filtered():
    class OtherPaper(FakeLLM):
        def structured(self,role,prompt,schema,**kwargs):
            if schema is AnswerDraft:
                return AnswerDraft(points=[Claim(paper_id='other',kind='method',text='错误论文的结论',evidence=[Evidence(chunk_id='p:p1:c0',quote=QUOTE)])],example='错误例子')
            return super().structured(role,prompt,schema,**kwargs)
    block=dict(text='选择表列',source_ids=[])
    result=answer_graph(OtherPaper(),PAPER,block,'选择表列','是什么',[],lambda s:None).invoke({})
    assert not result['accepted'] and not result['example']
    class BadExample(FakeLLM):
        def structured(self,role,prompt,schema,**kwargs):
            if schema is ExampleReview:return ExampleReview(consistent=False)
            return super().structured(role,prompt,schema,**kwargs)
    result=answer_graph(BadExample(),PAPER,block,'选择表列','是什么',[],lambda s:None).invoke({})
    assert result['accepted'] and not result['example']


from tests.test_web_jobs import web, request


def test_http_questions_routes_validate_context_and_hide_audit(web):
    from urllib.request import urlopen
    from urllib.error import HTTPError
    base,root=web;job_id='d'*32;qid='e'*32
    jobs=JobService(root)
    write(jobs.folder/job_id/'job.json',dict(id=job_id,run_id='web-'+job_id,status='completed',files=[]))
    folder=root/'output'/('web-'+job_id);folder.mkdir(parents=True)
    (folder/'report.md').write_text(REPORT,encoding='utf-8')
    write(folder/'state.json',dict(papers=[PAPER],accepted=[]))
    write(folder/'manifest.json',{})
    with urlopen(base+'/api/jobs/'+job_id+'/result') as response:result=json.load(response)
    assert result['selection_index']['blocks']
    endpoint='/api/jobs/'+job_id+'/questions'
    with pytest.raises(HTTPError) as error:request(base,endpoint,dict(version='outdated'))
    assert error.value.code==400
    with pytest.raises(HTTPError) as error:request(base,endpoint,{}, {'Origin':'https://foreign.example'})
    assert error.value.code==403
    write(jobs.folder/job_id/'questions'/qid/'question.json',dict(id=qid,status='completed',created_at='now',points=[],audit={'private':'raw draft'}))
    with urlopen(base+endpoint+'/'+qid) as response:answer=json.load(response)
    assert 'audit' not in answer
    with urlopen(base+endpoint) as response:assert len(json.load(response)['questions'])==1
