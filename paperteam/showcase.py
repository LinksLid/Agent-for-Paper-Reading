"""Read-only presentation website backed by saved PaperTeam runs."""
from collections import Counter
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen
import argparse
import hashlib
import json
import mimetypes
import xml.etree.ElementTree as ET
import threading
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT/'showcase'
READING = 'output/tongji-all-papers-20261101'

META = {
 'MAC-SQL.pdf': ('MAC-SQL','多智能体协作','Text-to-SQL','把自然语言问题转化为可执行的 SQL。'),
 'chess.pdf': ('CHESS','多智能体协作','Text-to-SQL','检索、模式选择、候选生成与单元测试协作。'),
 'react.pdf': ('ReAct','推理与行动','环境交互','将语言推理与外部环境中的行动连接起来。'),
 'knowtrans.pdf': ('KnowTrans','知识迁移','数据准备','通过知识增强改进数据准备模型的迁移。'),
 'llava.pdf': ('LLaVA','视觉指令微调','多模态','通过视觉指令微调连接图像与语言。'),
 'multiagent-debate.pdf': ('Multiagent Debate','多智能体协作','算术推理','多个模型实例交换回答，经过多轮讨论。'),
 'self-refine.pdf': ('Self-Refine','自反馈迭代','迭代改写','同一模型生成、反馈并反复改写。'),
 'tree-of-thoughts.pdf': ('Tree of Thoughts','显式搜索','Game24','生成多个候选思路，评价状态并搜索。'),
}
ROLES = [
 dict(id='planner',name='阅读规划员',short='规划',icon='compass',goal='把阅读问题拆成可检索的分析任务。',inputs='用户问题、论文索引',outputs='方法/实验检索词、比较维度',color='blue'),
 dict(id='method_analyst',name='方法分析员',short='方法分析',icon='layers',goal='解释输入输出、方法步骤与协作机制。',inputs='方法检索词、原文片段',outputs='带引文的方法结论',color='violet'),
 dict(id='experiment_analyst',name='实验分析员',short='实验分析',icon='flask',goal='核对数据、指标、对照与实验条件。',inputs='实验检索词、原文片段',outputs='带条件的实验结论',color='teal'),
 dict(id='verifier',name='证据核验员',short='证据核验',icon='shield',goal='检查引文存在性，以及它是否支持结论。',inputs='结论、引用与原文',outputs='接受/退回、具体理由',color='amber'),
 dict(id='reproducer',name='实验复现员',short='实验复现',icon='terminal',goal='在具备前提时执行原仓库，保存实际产物。',inputs='官方仓库、真实数据、执行计划',outputs='运行日志、原始评分或前提缺口',color='blue'),
 dict(id='reporter',name='综合报告员',short='综合报告',icon='document',goal='把通过核验的结论与运行依据组织成报告。',inputs='接受项、比较维度、实验记录',outputs='可追溯的中文阅读报告',color='violet'),
]
EVENT_NAMES={'ingest':'建立论文索引','planner':'制定阅读计划','method_analyst':'分析方法与协作机制',
 'experiment_analyst':'核对实验与评测条件','verifier':'核验结论与原文','targeted_repair':'按反馈定向补查',
 'paper_explainer':'生成并检查逐篇讲解','reproducer':'阅读运行未启用复现分支','reporter':'汇总可追溯报告'}


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {} if default is None else default


def reading_directory(root):
    """Choose the newest saved reading, independently of a particular machine/date."""
    root=Path(root)
    candidates=[p.parent for p in (root/'output').glob('*/manifest.json')
                if (p.parent/'state.json').is_file() and read_json(p).get('status')=='completed']
    return max(candidates,key=lambda p:(p/'manifest.json').stat().st_mtime) if candidates else root/READING


def experiment_summary(root):
    root=Path(root)
    candidates=list((root/'output').glob('experiments-*/summary.json'))
    selected=max(candidates,key=lambda p:p.stat().st_mtime) if candidates else None
    summary=read_json(selected) if selected else {}
    by_name={x['filename']:x for x in summary.get('results',[])}
    for path in sorted((root/'output').glob('*/reproduction.json'),key=lambda p:p.stat().st_mtime):
        if selected and path.stat().st_mtime<=selected.stat().st_mtime:continue
        run=read_json(path)
        for original in run.get('papers',[]):
            row=dict(original,run=path.parent.name,seconds=run.get('seconds'))
            by_name[row['filename']]=row
    return dict(summary,results=list(by_name.values()))


def build_data(root=ROOT,mode='auto'):
    root=Path(root)
    directory=reading_directory(root)
    state=read_json(directory/'state.json')
    demo=read_json(root/'examples/public-demo.json')
    if mode=='demo' or (mode=='auto' and not state.get('papers') and demo):
        if not demo:raise ValueError('Bundled public demo is missing: examples/public-demo.json')
        return demo
    manifest=read_json(directory/'manifest.json')
    summary=experiment_summary(root)
    sources=read_json(root/'paper/sources.json').get('papers',[])
    source_by_filename={Path(x.get('path','')).name:x for x in sources}
    # Windows paths remain valid source metadata when an exported fixture runs elsewhere.
    source_by_filename.update({x.get('path','').replace('\\','/').rsplit('/',1)[-1]:x for x in sources})
    guides={x['paper_id']:x for x in state.get('paper_guides',[])}
    papers=[]
    for p in state.get('papers',[]):
        name,mechanism,domain,description=META.get(p['filename'],(p['filename'],'待整理','论文阅读',''))
        src=source_by_filename.get(p['filename'],{})
        guide=guides.get(p['paper_id'],{})
        papers.append(dict(id=p['paper_id'],filename=p['filename'],name=name,title=src.get('title',name),
            mechanism=mechanism,domain=domain,description=description,pages=p.get('pages',0),sha256=p.get('sha256',''),
            repository=src.get('repository') or next(iter(p.get('github_urls',[])),''),source=src.get('pdf',''),
            local_pdf=(root/'paper'/Path(p['filename']).name).is_file(),
            added=src.get('group')=='added',guide_status=guide.get('status','pending'),coverage=guide.get('coverage',{}),
            task={k:guide.get('task',{}).get(k) for k in ['text','verified','rejection_reason']},
            steps=[{k:x.get(k) for k in ['step_id','name','phase','receives','does','produces','verified']} for x in guide.get('steps',[])]))
    chunks={c['chunk_id']:c for p in state.get('papers',[]) for c in p.get('chunks',[])}
    claims=[]
    def claim_row(claim,status,index,reason=''):
        evidence=[]
        for e in claim.get('evidence',[]):
            chunk=chunks.get(e['chunk_id'],{})
            evidence.append(dict(chunk_id=e['chunk_id'],quote=e.get('quote',''),page=chunk.get('page'),
                                 context=chunk.get('text','')[:2600],exact=e.get('quote','') in chunk.get('text','')))
        return dict(id=f'{status}-{index}',paper_id=claim.get('paper_id'),text=claim.get('text',''),
                    kind=claim.get('kind','method'),role=claim.get('origin_role',''),status=status,reason=reason,evidence=evidence)
    for i,c in enumerate(state.get('accepted',[])):claims.append(claim_row(c,'accepted',i))
    for i,r in enumerate(state.get('rejected',[])):claims.append(claim_row(r.get('claim',{}),'rejected',i,r.get('reason','')))
    rounds=[dict(round=x['round'],accepted=x['accepted'],rejected=len(x.get('rejected',[]))) for x in state.get('review_history',[])]
    first_cases=[]
    for i,r in enumerate(next(iter(state.get('review_history',[])),{}).get('rejected',[])[:4]):
        first_cases.append(claim_row(r['claim'],'returned',i,r['reason']))
    events=sorted(state.get('events',[]),key=lambda x:x.get('time',0))
    start=events[0].get('time',0) if events else 0
    timeline=[]
    for i,e in enumerate(events):
        detail={k:v for k,v in e.items() if k not in {'time','node'}}
        timeline.append(dict(index=i,node=e['node'],name=EVENT_NAMES.get(e['node'],e['node']),
                             offset=round(e.get('time',start)-start,1),details=detail))
    usage=Counter();total_tokens=0;total_calls=0
    for f in (root/'output').rglob('api_usage.jsonl'):
        file_calls=0
        for line in f.read_text(encoding='utf-8').splitlines():
            try:r=json.loads(line)
            except ValueError:continue
            role=r.get('agent','')
            category='原仓库模型推理' if role=='原仓库实验/API' else '实验规划与评估' if role.startswith('复现/') else '连接检查' if '检查' in role and ('API' in role or '接口' in role) else '阅读、讲解与核验'
            usage[category]+=1
            file_calls=max(file_calls,r.get('call',0))
            total_tokens+=r.get('usage',{}).get('total_tokens',0)
        total_calls+=file_calls
    tests=summary.get('tests_passed',0)
    xmls=list((root/'output').rglob('pytest.xml'))
    xml=max(xmls,key=lambda p:p.stat().st_mtime) if xmls else root/'output/pytest.xml'
    if xml.is_file():
        try:
            cases=ET.parse(xml).getroot().findall('.//testcase')
            tests=sum(not any(c.find(tag) is not None for tag in ['failure','error','skipped']) for c in cases)
        except ET.ParseError:pass
    results=[]
    for item in summary.get('results',[]):
        row={k:item.get(k) for k in ['paper_id','filename','status','scope','reason','repository','commit','metrics',
             'full_paper_reproduced','search_configuration','run','seconds','usage','repair_attempts']}
        row['name']=META.get(item['filename'],(item['filename'],))[0]
        row['commands']=[dict(phase=c.get('phase'),argv=c.get('argv'),exit_code=c.get('exit_code'),seconds=c.get('seconds')) for c in item.get('commands',[])]
        folder=root/'output'/item['run']/item['paper_id']
        original=read_json(folder/'result.json')
        row['models']=original.get('experiment_models') or item.get('experiment_models') or []
        row['refinement_unchanged']=None
        if not row.get('search_configuration'):
            for command in (original.get('plan') or {}).get('run',[]):
                args=command.get('argv',[])
                if all(flag in args for flag in ['--n_generate_sample','--n_evaluate_sample','--n_select_sample']):
                    row['search_configuration']='/'.join(label+args[args.index(flag)+1] for flag,label in
                       [('--n_generate_sample','propose'),('--n_evaluate_sample','value'),('--n_select_sample','greedy')])
        row['sample_count']=None
        row['sample_scores']=[]
        artifact_root=Path(original.get('artifact_root') or folder).resolve()
        if artifact_root.is_relative_to((root/'output').resolve()):
            row['refinement_unchanged']=read_json(artifact_root/'artifacts/outputs/identity-audit.json').get('all_final_programs_equal_initial')
        for rel in original.get('artifacts',[]):
            f=(artifact_root/rel).resolve()
            if not f.is_relative_to((root/'output').resolve()) or not f.is_file() or f.suffix!='.json':continue
            a=read_json(f)
            if item['filename']=='MAC-SQL.pdf' and isinstance(a,list) and a and all(isinstance(x,dict) and x.get('res') in (0,1) for x in a):
                row['sample_count']=len(a);row['sample_scores']=[x['res'] for x in a]
            if not isinstance(a,dict):continue
            if item['filename']=='MAC-SQL.pdf' and isinstance(a.get('res'),list):
                row['sample_count']=len(a['res']);row['sample_scores']=a['res']
            elif item['filename']=='react.pdf' and isinstance(a.get('cnts'),list):
                row['sample_count']=sum(a['cnts']);row['sample_scores']=[int(bool(e['reward'])) for e in a.get('episodes',[])]
            elif item['filename']=='multiagent-debate.pdf' and isinstance(a.get('samples'),int):row['sample_count']=a['samples']
            elif item['filename']=='self-refine.pdf' and isinstance(a.get('samples'),int):row['sample_count']=a['samples']
            elif item['filename']=='tree-of-thoughts.pdf' and not a.get('naive_run') and isinstance(a.get('n'),int):
                row['sample_count']=a['n'];row['sample_scores']=[int(any(i.get('r',0) for i in l.get('infos',[]))) for l in a.get('logs',[])]
        artifacts=[]
        for rel in original.get('artifacts',[]):
            if rel.endswith(('.json','.jsonl')) and Path(rel).name!='api_trace.json':
                artifacts.append(dict(name=Path(rel).name,url=f"/artifacts/{item['paper_id']}/{Path(rel).name}"))
        row['artifacts']=artifacts
        results.append(row)
    recorded=dict(summary.get('recorded_usage',{}))
    if usage:recorded.update(calls=total_calls,total_tokens=total_tokens)
    return dict(version=1,updated_at=summary.get('updated_at'),model=manifest.get('model') or summary.get('model','未配置'),
        data_origin=dict(kind='local',label='本机保存记录',run=directory.name,
                         run_date=datetime.fromtimestamp(start).strftime('%Y.%m.%d') if start else directory.name,
                         notice='读取已有运行，不会因为打开网站而调用模型。'),
        question=manifest.get('question',''),reading={k:manifest.get(k) for k in ['status','seconds','api_calls','total_tokens','accepted_claims','rejected_claims','review_rounds']},
        stats=dict(papers=len(papers),pages=sum(p['pages'] for p in papers),accepted=len(state.get('accepted',[])),
                   rejected=len(state.get('rejected',[])),completed=sum(r['status']=='completed' for r in results),
                   blocked=sum(r['status']=='blocked' for r in results),tests=tests,**recorded),
        papers=papers,roles=ROLES,timeline=timeline,claims=claims,first_cases=first_cases,rounds=rounds,
        results=results,usage=[dict(name=k,value=v) for k,v in usage.most_common()],
        guides=dict(ready=sum(p['guide_status']=='ready' for p in papers),partial=sum(p['guide_status']=='partial' for p in papers)),
        limits=['所有论文实验均为替换模型后的小样本验证，不能称为完整论文复现。',
                '核验接受比例和结构检查状态不等于人工测得的事实准确率。',
                '历史节点回放不发起模型请求；真实执行请进入原工作台。'])


def export_snapshot(root=ROOT, site=SITE,mode='auto'):
    data=build_data(root,mode)
    target=Path(site)/'data/snapshot.js'
    target.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    target.write_text('window.PAPERTEAM_DATA='+raw+';\n',encoding='utf-8')
    for source,name in [('maintainer-results-20261101.md','report.md'),('人工评测与课堂演示清单.md','evaluation.md')]:
        doc=Path(root)/'docs'/source
        if doc.is_file():
            copy=Path(site)/'downloads'/name
            copy.parent.mkdir(parents=True,exist_ok=True)
            copy.write_bytes(doc.read_bytes())
    return target


def allowed_downloads(root=ROOT):
    root=Path(root)
    mapping={'/downloads/report.md':root/'docs/maintainer-results-20261101.md',
             '/downloads/evaluation.md':root/'docs/人工评测与课堂演示清单.md'}
    for p in read_json(reading_directory(root)/'state.json').get('papers',[]):
        path=root/'paper'/Path(p['filename']).name
        if path.is_file():mapping[f"/papers/{p['paper_id']}.pdf"]=path
    for item in experiment_summary(root).get('results',[]):
        folder=root/'output'/item['run']/item['paper_id']
        result=read_json(folder/'result.json')
        artifact_root=Path(result.get('artifact_root') or folder).resolve()
        if not artifact_root.is_relative_to((root/'output').resolve()):continue
        for rel in result.get('artifacts',[]):
            target=(artifact_root/rel).resolve()
            if target.is_relative_to(artifact_root) and target.is_file() and target.suffix in {'.json','.jsonl'} and target.name!='api_trace.json' and target.stat().st_size<10_000_000:
                mapping[f"/artifacts/{item['paper_id']}/{target.name}"]=target
    return mapping


class ShowcaseHandler(SimpleHTTPRequestHandler):
    def __init__(self,*args,root=ROOT,site=SITE,mode='auto',**kwargs):
        self.project_root=Path(root)
        self.site_root=Path(site).resolve()
        self.data_mode=mode
        super().__init__(*args,directory=str(self.site_root),**kwargs)

    @property
    def jobs(self):
        from .web_jobs import JobService, LOCK
        with LOCK:
            if not hasattr(self.server,'job_service'):
                self.server.job_service=JobService(self.project_root)
        return self.server.job_service

    def json_response(self, data, status=200):
        raw=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers();self.wfile.write(raw)

    @property
    def questions(self):
        from .selection_qa import QuestionService
        from .web_jobs import LOCK
        with LOCK:
            if not hasattr(self.server, 'question_service'):
                self.server.question_service = QuestionService(self.jobs)
        return self.server.question_service

    def api_get(self,path):
        from .web_jobs import identifier, read
        if path=='/api/health':
            self.json_response(dict(app='PaperTeam',version=2));return
        if path=='/api/runtime':
            self.json_response(self.jobs.runtime());return
        if path=='/api/jobs':
            self.json_response(dict(jobs=self.jobs.list_jobs()));return
        parts=path.strip('/').split('/')
        if len(parts) in {4,5} and parts[:2]==['api','jobs'] and parts[3]=='questions':
            self.json_response(dict(questions=self.questions.list(parts[2])) if len(parts)==4 else
                               self.questions.get(parts[2],parts[4]));return
        if len(parts) in {3,4} and parts[:2]==['api','jobs']:
            job=self.jobs.get(parts[2],report=len(parts)==4)
            if len(parts)==3 or parts[3]=='result':
                self.json_response(job);return
            if parts[3]=='report.md' and job['report_ready']:
                raw=job['report'].encode('utf-8')
                self.send_response(200);self.send_header('Content-Type','text/markdown; charset=utf-8')
                self.send_header('Content-Disposition','attachment; filename="PaperTeam-reading-report.md"')
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
        if len(parts)==4 and parts[:2]==['api','uploads'] and parts[3]=='paper.pdf':
            folder=self.jobs.folder/'uploads'/identifier(parts[2])
            meta=read(folder/'upload.json')
            if not meta:raise FileNotFoundError()
            raw=(folder/meta['filename']).read_bytes()
            self.send_response(200);self.send_header('Content-Type','application/pdf')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
        self.json_response(dict(error='接口不存在。'),404)

    def do_GET(self):
        path=unquote(urlsplit(self.path).path)
        if path.startswith('/api/') and path!='/api/data':
            try:self.api_get(path)
            except FileNotFoundError:self.json_response(dict(error='任务或文件不存在。'),404)
            except ValueError as error:self.json_response(dict(error=str(error)),400)
            except Exception:self.json_response(dict(error='本地服务暂不可用，请查看终端日志。'),500)
            return
        if path=='/api/data':
            raw=json.dumps(build_data(self.project_root,self.data_mode),ensure_ascii=False).encode('utf-8')
            self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            return
        downloads=allowed_downloads(self.project_root) if path.startswith(('/downloads/','/papers/','/artifacts/')) else {}
        if path in downloads:
            f=downloads[path]
            if not f.is_file():self.send_error(404);return
            raw=f.read_bytes();self.send_response(200)
            self.send_header('Content-Type',mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw);return
        relative=path.lstrip('/') or 'index.html'
        target=(self.site_root/relative).resolve()
        public_json=target.suffix=='.json' and target.is_relative_to(self.site_root/'data/artifacts')
        if (not target.is_relative_to(self.site_root) or not target.is_file() or
            any(part.startswith('.') for part in Path(relative).parts) or (target.suffix not in {'.html','.css','.js','.svg','.ico'} and not public_json)):
            self.send_error(404);return
        super().do_GET()

    def do_POST(self):
        from .web_jobs import MAX_PDF_BYTES
        origin=self.headers.get('Origin')
        if origin and urlsplit(origin).netloc!=self.headers.get('Host'):
            self.json_response(dict(error='仅接受本站发起的操作。'),403);return
        try:
            path=urlsplit(self.path).path
            length=int(self.headers.get('Content-Length','0'))
            limit=MAX_PDF_BYTES if path=='/api/uploads' else 20000
            if not 0<length<=limit:
                self.json_response(dict(error='文件或请求超过大小限制（PDF 最大 20 MB）。'),413);return
            raw=self.rfile.read(length)
            if len(raw)!=length:raise ValueError('上传未完成，请重试。')
            if path=='/api/uploads':
                filename=unquote(self.headers.get('X-Filename','paper.pdf'))
                self.json_response(self.jobs.upload(filename,raw),201);return
            data=json.loads(raw.decode('utf-8'))
            if not isinstance(data,dict):raise ValueError('请求格式不正确。')
            if path=='/api/settings':
                self.json_response(self.jobs.save_settings(data));return
            if path=='/api/jobs':
                self.json_response(self.jobs.start(data),202);return
            parts=path.strip('/').split('/')
            if len(parts)==4 and parts[:2]==['api','jobs'] and parts[3]=='questions':
                self.json_response(self.questions.start(parts[2],data),202);return
            if len(parts)==4 and parts[:2]==['api','jobs'] and parts[3]=='stop':
                self.json_response(self.jobs.stop(parts[2]));return
            self.json_response(dict(error='接口不存在。'),404)
        except (ValueError,UnicodeError) as error:self.json_response(dict(error=str(error)),400)
        except FileNotFoundError:self.json_response(dict(error='文件不存在。'),404)
        except Exception:self.json_response(dict(error='操作未完成，请检查本地环境后重试。'),500)


class PresentationServer(ThreadingHTTPServer):
    # Windows otherwise permits a second listener to shadow the existing one.
    allow_reuse_address=False


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8600)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--open',action='store_true',help='Open the default browser after startup')
    parser.add_argument('--data-mode',choices=['auto','demo','local'],default='auto')
    parser.add_argument('--export-only',action='store_true')
    args=parser.parse_args(argv)
    # Keep the tracked offline snapshot public even when the API serves local runs.
    snapshot_mode=args.data_mode if args.export_only else ('demo' if (ROOT/'examples/public-demo.json').is_file() else args.data_mode)
    export_snapshot(mode=snapshot_mode)
    if args.export_only:
        print('Offline snapshot exported; no model requests made.');return
    try:
        with urlopen(f'http://127.0.0.1:{args.port}/api/health',timeout=2) as response:
            existing=json.load(response)
        if existing.get('version')==2 and existing.get('app')=='PaperTeam':
            print(f'PaperTeam is already running: http://127.0.0.1:{args.port}',flush=True)
            if args.open:webbrowser.open(f'http://127.0.0.1:{args.port}')
            return
    except (OSError,ValueError):pass
    handler=partial(ShowcaseHandler,mode=args.data_mode)
    server=PresentationServer((args.host,args.port),handler)
    print(f'PaperTeam reading agents: http://127.0.0.1:{args.port}/#read',flush=True)
    if args.open:threading.Timer(0.5,webbrowser.open,args=[f'http://127.0.0.1:{args.port}']).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
