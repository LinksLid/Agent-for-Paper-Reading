"""Local, persistent reading jobs. HTTP responses never include credentials or host paths."""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

MAX_PDF_BYTES = 20 * 1024 * 1024
NODES = ['ingest', 'planner', 'method_analyst', 'experiment_analyst', 'verifier',
         'targeted_repair', 'paper_explainer', 'reporter']
LOCK = threading.RLock()


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {} if default is None else default


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('无效的任务或文件编号。')
    return value


def config_values(root):
    values = {}
    path = Path(root) / '.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if '=' not in line or line.lstrip().startswith('#'):
                continue
            key, value = line.split('=', 1)
            value = value.strip()
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except ValueError:
                    value = value.strip('"')
            else:
                value = value.strip("'")
            values[key.strip()] = value
    return {**values, **os.environ}


def runtime_python(root):
    candidate = Path(root) / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    return candidate if candidate.is_file() else Path(sys.executable)


class JobService:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.folder = self.root / '.cache/web-jobs'
        self.processes = {}
        self._deps = None
        self._checked = 0

    @staticmethod
    def process_alive(pid):
        if not isinstance(pid, int) or pid <= 0:
            return False
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
            finally:
                kernel.CloseHandle(handle)
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def runtime(self):
        if self._deps is None or time.monotonic() - self._checked > 30:
            result = subprocess.run([str(runtime_python(self.root)), '-c',
                'import langgraph,pypdf,httpx,dotenv; from langgraph.checkpoint.sqlite import SqliteSaver'], capture_output=True, cwd=self.root,
                timeout=20, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self._deps = result.returncode == 0
            self._checked = time.monotonic()
        values = config_values(self.root)
        base = values.get('OPENAI_BASE_URL') or values.get('BASE_URL') or values.get('Base_url', '')
        key = values.get('OPENAI_API_KEY') or values.get('API_KEY', '')
        model = values.get('MODEL') or values.get('OPENAI_MODEL', '')
        return dict(version=2, dependencies_ready=self._deps, configured=bool(base and key and model),
                    base_url=base, model=model, max_upload_mb=20, max_papers=3)

    def save_settings(self, data):
        base = str(data.get('base_url', '')).strip().rstrip('/')
        model = str(data.get('model', '')).strip()
        key = str(data.get('api_key', '')).strip()
        parts = urlsplit(base)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.query or parts.fragment:
            raise ValueError('请填写有效的 API Base URL，例如 https://agi.tongji.edu.cn/v1。')
        if not model or len(model) > 160 or any(c in model + key + base for c in '\r\n\x00') or len(key) > 4096:
            raise ValueError('模型或密钥格式不正确。')
        existing = config_values(self.root)
        if not key:
            key = existing.get('OPENAI_API_KEY') or existing.get('API_KEY', '')
        if not key:
            raise ValueError('首次配置需要填写 API Key。')
        path = self.root / '.env'
        lines = path.read_text(encoding='utf-8-sig').splitlines() if path.is_file() else []
        names = {'Base_url', 'BASE_URL', 'OPENAI_BASE_URL', 'API_KEY', 'OPENAI_API_KEY', 'MODEL', 'OPENAI_MODEL'}
        lines = [line for line in lines if line.split('=', 1)[0].strip() not in names]
        lines += [name + '=' + json.dumps(value, ensure_ascii=False) for name, value in
                  [('Base_url', base), ('API_KEY', key), ('MODEL', model)]]
        # Settings uses this value only if the selected provider supports it.
        if not any(line.startswith('REASONING_EFFORT=') for line in lines):
            lines.append('REASONING_EFFORT=none')
        with LOCK:
            path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return self.runtime()

    def upload(self, filename, raw):
        if not raw or len(raw) > MAX_PDF_BYTES:
            raise ValueError('每篇论文最大 20 MB。')
        if not raw.lstrip()[:1024].startswith(b'%PDF-'):
            raise ValueError('文件不是有效的 PDF。请上传文本型论文 PDF。')
        filename = re.split(r'[/\\]', filename)[-1].strip()
        if not filename.lower().endswith('.pdf') or len(filename) > 150:
            raise ValueError('请选择 PDF 文件，文件名不超过 150 个字符。')
        upload_id = uuid.uuid4().hex
        folder = self.folder / 'uploads' / upload_id
        folder.mkdir(parents=True)
        safe_name = re.sub(r'[^\w.\-\u4e00-\u9fff ()]', '_', filename)
        (folder / safe_name).write_bytes(raw)
        meta = dict(id=upload_id, filename=safe_name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), uploaded_at=now())
        write(folder / 'upload.json', meta)
        return meta

    def uploads(self, ids):
        if not isinstance(ids, list) or not 1 <= len(ids) <= 3 or len(set(ids)) != len(ids):
            raise ValueError('请选择 1 至 3 篇不同的论文。')
        files = []
        for upload_id in ids:
            meta = read(self.folder / 'uploads' / identifier(upload_id) / 'upload.json')
            if not meta:
                raise ValueError('上传文件不存在，请重新上传。')
            files.append(meta)
        digests={f.get('sha256') or hashlib.sha256(
            (self.folder/'uploads'/f['id']/f['filename']).read_bytes()).hexdigest() for f in files}
        if len(digests) != len(files):
            raise ValueError('同一篇论文被重复上传，请只保留一份。')
        return files

    def start(self, data):
        question = str(data.get('question', '')).strip()
        if not 5 <= len(question) <= 4000:
            raise ValueError('请填写 5 至 4000 字的阅读要求。')
        mode = data.get('mode', 'verified')
        if mode not in {'verified', 'multi', 'single'}:
            raise ValueError('无效的阅读模式。')
        max_calls = data.get('max_calls', 60)
        if type(max_calls) is not int or not 10 <= max_calls <= 200:
            raise ValueError('模型调用上限应为 10 至 200 次。')
        files = self.uploads(data.get('upload_ids'))
        runtime = self.runtime()
        if not runtime['dependencies_ready']:
            raise ValueError('运行依赖尚未安装，请关闭服务后双击 start.bat，或运行 pip install -r requirements.txt。')
        if not runtime['configured']:
            raise ValueError('请先在“模型连接”中保存 Base URL、模型名称和 API Key。')
        with LOCK:
            if any(item['status'] in {'queued', 'running'} for item in self.list_jobs()):
                raise ValueError('已有阅读任务运行中，请等待完成或停止当前任务。')
            job_id = uuid.uuid4().hex
            job = dict(id=job_id, status='queued', created_at=now(), question=question,
                       files=files, mode=mode, explain=bool(data.get('explain', True)),
                       max_calls=max_calls, run_id='web-' + job_id, nodes={}, events=[],
                       calls=0, total_tokens=0)
            folder = self.folder / job_id
            write(folder / 'job.json', job)
            with (folder / 'worker.log').open('ab') as output:
                process = subprocess.Popen([str(runtime_python(self.root)), '-m', 'paperteam.web_jobs',
                    '--root', str(self.root), '--job', job_id], cwd=self.root,
                    stdout=output, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.processes[job_id] = process
            return job

    def get(self, job_id, report=False):
        job_id = identifier(job_id)
        path = self.folder / job_id / 'job.json'
        job = read(path)
        if not job:
            raise FileNotFoundError('任务不存在。')
        process = self.processes.get(job_id)
        stopped = process is not None and process.poll() is not None
        if process is None and job.get('pid'):
            stopped = not self.process_alive(job['pid'])
        if stopped and job['status'] in {'queued', 'running'}:
            job.update(status='failed', error='执行进程已退出，请检查模型连接后重试。', finished_at=now())
            write(path, job)
        run = self.root / 'output' / job['run_id']
        records = []
        usage_path = run / 'api_usage.jsonl'
        if usage_path.is_file():
            for line in usage_path.read_text(encoding='utf-8').splitlines():
                try:
                    records.append(json.loads(line))
                except ValueError:
                    pass
        job['calls'] = len(records)
        job['total_tokens'] = sum(r.get('usage', {}).get('total_tokens', 0) for r in records)
        job['report_ready'] = job['status'] == 'completed' and (run / 'report.md').is_file()
        if report and job['report_ready']:
            job['report'] = (run / 'report.md').read_text(encoding='utf-8')
            state = read(run / 'state.json')
            chunks = {c['chunk_id']: c for p in state.get('papers', []) for c in p.get('chunks', [])}
            job['claims'] = [{**{k: c.get(k) for k in ['text', 'kind', 'paper_id']}, 'evidence': [
                dict(quote=e['quote'], page=chunks.get(e['chunk_id'], {}).get('page'), chunk_id=e['chunk_id'])
                for e in c.get('evidence', [])]} for c in state.get('accepted', [])]
            job['papers'] = [{k: p.get(k) for k in ['filename', 'paper_id', 'pages']} for p in state.get('papers', [])]
            manifest = read(run / 'manifest.json')
            job['summary'] = {k: manifest.get(k) for k in ['model', 'seconds', 'accepted_claims',
                                                      'rejected_claims', 'review_rounds']}
        return job

    def list_jobs(self):
        jobs = []
        for path in self.folder.glob('*/job.json'):
            try:
                job = self.get(path.parent.name)
                jobs.append({k: job.get(k) for k in ['id', 'status', 'created_at', 'files', 'question', 'calls', 'report_ready']})
            except (ValueError, FileNotFoundError):
                continue
        return sorted(jobs, key=lambda j: j['created_at'], reverse=True)[:30]

    def stop(self, job_id):
        with LOCK:
            job = self.get(job_id)
            process = self.processes.get(job_id)
            if job['status'] not in {'queued', 'running'}:
                return job
            if process is None:
                raise ValueError('此任务由先前的服务启动。请等待其结束后刷新页面。')
            if os.name == 'nt':
                # Windows venv Python launches a child interpreter; terminate both.
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, timeout=15)
            else:
                process.terminate()
            process.wait(timeout=15)
            job = read(self.folder / job_id / 'job.json')
            job.update(status='cancelled', finished_at=now())
            for node in job['nodes'].values():
                if node['status'] == 'running':
                    node['status'] = 'cancelled'
            write(self.folder / job_id / 'job.json', job)
            return job


def execute(root, job_id):
    from .config import Settings
    from .runner import run
    from pypdf import PdfReader
    root = Path(root).resolve()
    service = JobService(root)
    path = service.folder / identifier(job_id) / 'job.json'
    job = read(path)
    if not job:
        raise ValueError('任务不存在')

    def event(node, status, values=None):
        with LOCK:
            item = job['nodes'].setdefault(node, {})
            item.update(status=status, updated_at=now())
            if status == 'running':
                item['rounds'] = item.get('rounds', 0) + 1
            job['events'].append(dict(node=node, status=status, time=now()))
            if values:
                if 'accepted' in values:
                    job['accepted_claims'] = len(values['accepted'])
                    job['rejected_claims'] = len(values.get('rejected', []))
            write(path, job)
    settings = None
    try:
        job.update(status='running', started_at=now(), pid=os.getpid())
        write(path, job)
        paths = []
        for meta in job['files']:
            pdf = service.folder / 'uploads' / identifier(meta['id']) / meta['filename']
            reader = PdfReader(pdf)
            if reader.is_encrypted or not 1 <= len(reader.pages) <= 150:
                raise ValueError('请使用未加密、1 至 150 页的文本型 PDF。')
            if not any((p.extract_text() or '').strip() for p in reader.pages):
                raise ValueError('PDF 没有可提取的文字，请先 OCR 转换为文本型 PDF。')
            paths.append(pdf)
        settings = replace(Settings.load(root, provider='tongji'), max_calls=job['max_calls'])
        state, output = run(settings, paths, job['question'], mode=job['mode'],
            max_repairs=1 if job['mode'] == 'verified' else 0, reproduce=False,
            explain=job['explain'], run_id=job['run_id'],
            progress=lambda node, values: event(node, 'completed', values),
            on_node_start=lambda node: event(node, 'running'))
        job.update(status='completed', finished_at=now())
        write(path, job)
    except Exception as error:
        # Known local validation / pipeline messages are safe after credential redaction.
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        if settings and settings.api_key:
            message = message.replace(settings.api_key, '[REDACTED]')
        message = re.sub(r'sk-[A-Za-z0-9_-]+', '[REDACTED]', message)
        message = message.replace(str(root), '[项目目录]')
        job.update(status='failed', error=message[:700], finished_at=now())
        for node in job['nodes'].values():
            if node['status'] == 'running':
                node['status'] = 'failed'
        write(path, job)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--job', required=True)
    args = parser.parse_args()
    execute(args.root, args.job)
