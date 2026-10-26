"""Run downloaded code in Docker without mounting project/API configuration."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from pathlib import Path, PurePosixPath
import secrets
import shutil
import subprocess
import threading
import time
import uuid


def safe_relative(path: str) -> str:
    if not path or "\\" in path or ":" in path:
        raise ValueError("必须使用仓库内的相对路径")
    p = PurePosixPath(path)
    if p.is_absolute() or any(part == ".." or part.startswith(".env") or part == ".git" for part in p.parts):
        raise ValueError("路径不能越出仓库或访问配置")
    return p.as_posix()


def patched_text(text, suffix, old, new):
    if suffix == ".ipynb":
        notebook = json.loads(text)
        cells = [c for c in notebook["cells"] if c["cell_type"] == "code"]
        if sum("".join(c["source"]).count(old) if isinstance(c["source"], list) else c["source"].count(old) for c in cells) != 1:
            raise ValueError("Notebook 补丁必须唯一匹配一个代码单元")
        for cell in cells:
            source = "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]
            if old in source:
                cell["source"] = source.replace(old, new, 1).splitlines(keepends=True)
        return json.dumps(notebook, ensure_ascii=False, indent=1)
    if not old or text.count(old) != 1:
        raise ValueError("补丁原文必须唯一匹配")
    return text.replace(old, new, 1)


def docker_available():
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(["docker", "info", "--format", "{{.OSType}}"], capture_output=True, text=True, timeout=12)
        return result.returncode == 0 and result.stdout.strip() == "linux"
    except (OSError, subprocess.TimeoutExpired):
        return False


class Broker:
    """Temporary API adapter: repo code gets a throwaway token, never actual keys."""
    def __init__(self, llm, max_calls=20, completion_continuation=False):
        self.llm, self.max_calls, self.calls = llm, max_calls, 0
        self.completion_continuation = completion_continuation
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send_json(self, status, value):
                raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def authorized(self):
                supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
                supplied = supplied or self.headers.get("api-key", "")
                return secrets.compare_digest(supplied, broker.token)

            def do_GET(self):
                if not self.authorized():
                    return self.send_json(401, {"error": {"message": "temporary token required"}})
                if self.path.rstrip("/").endswith("/models"):
                    return self.send_json(200, {"object": "list", "data": [{"id": broker.llm.choose_model(), "object": "model"}]})
                self.send_json(404, {"error": {"message": "unsupported endpoint"}})

            def do_POST(self):
                if not self.authorized():
                    return self.send_json(401, {"error": {"message": "temporary token required"}})
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 1_000_000:
                    return self.send_json(413, {"error": {"message": "request too large"}})
                if not self.path.split("?")[0].endswith(("/chat/completions", "/completions")):
                    return self.send_json(404, {"error": {"message": "unsupported endpoint"}})
                try:
                    data = json.loads(self.rfile.read(length))
                    if data.get("stream") or data.get("tools") or data.get("n", 1) != 1:
                        return self.send_json(400, {"error": {"message": "adapter supports non-stream text completions only"}})
                    messages = data.get("messages") or [{"role": "user", "content": data.get("prompt", "")}]
                    chat = self.path.split("?")[0].endswith("/chat/completions")
                    if not chat and broker.completion_continuation:
                        messages = [{"role": "system", "content":
                            "You are a text completion engine, not a conversational assistant. Continue the user's text "
                            "exactly from its final position. Return only the next continuation, with no introduction, "
                            "Markdown, code fences, or explanation. Follow the action and thought syntax demonstrated "
                            "in the text. If the text ends with '>', output exactly one next line: an environment command "
                            "in the demonstrated grammar or a thought explicitly prefixed 'think:'. Do not narrate what "
                            "you would do in ordinary prose. Never invent observations or successful outcomes."}, *messages]
                    if not all(m.get("role") in {"system", "user", "assistant"} and isinstance(m.get("content"), str) for m in messages):
                        return self.send_json(400, {"error": {"message": "text messages required"}})
                    with broker.lock:
                        if broker.calls >= broker.max_calls:
                            return self.send_json(429, {"error": {"message": "experiment API call limit reached"}})
                        broker.calls += 1
                        text = broker.llm.chat("原仓库实验/API", json.dumps(messages, ensure_ascii=False),
                                               max_tokens=max(1, min(int(data.get("max_tokens") or 2000), 6000)),
                                               system="", messages=messages, request_options=data, allow_truncated=True)
                        record = broker.llm.records[-1]
                    choice = {"index": 0, "finish_reason": record.get("finish_reason", "stop")}
                    choice.update({"message": {"role": "assistant", "content": text}} if chat else {"text": text})
                    return self.send_json(200, {"id": "paperteam-" + uuid.uuid4().hex, "object": "chat.completion" if chat else "text_completion",
                                                "created": int(time.time()), "model": record["model"],
                                                "choices": [choice], "usage": record.get("usage", {})})
                except Exception:
                    return self.send_json(502, {"error": {"message": "budgeted upstream request failed; inspect host API usage log"}})

        self.server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class DockerRuntime:
    def __init__(self, repo: Path, output: Path, python_version: str, broker: Broker | None, datasets=None, build_tools=False):
        if python_version not in {"3.10", "3.11", "3.12"}:
            raise ValueError("不支持的 Python 运行时")
        self.repo, self.output = repo.resolve(), output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.name = "paperteam-" + uuid.uuid4().hex[:12]
        self.image = f"paperteam-python:{python_version}-tools" if build_tools else f"python:{python_version}-slim"
        self.build_tools = build_tools
        self.python_version = python_version
        self.broker = broker
        self.records = []
        self.datasets = datasets or []

    def _host(self, argv, timeout=120):
        start = time.monotonic()
        try:
            result = subprocess.run(argv, capture_output=True, timeout=timeout, encoding="utf-8", errors="replace")
            return {"exit_code": result.returncode, "stdout": result.stdout[-60000:], "stderr": result.stderr[-20000:],
                    "seconds": round(time.monotonic() - start, 3)}
        except subprocess.TimeoutExpired:
            return {"exit_code": -1, "stdout": "", "stderr": "command timeout", "seconds": timeout, "timed_out": True}

    def __enter__(self):
        if self.build_tools and self._host(["docker", "image", "inspect", self.image])["exit_code"]:
            context = Path(__file__).resolve().parents[1] / "docker"
            build = self._host(["docker", "build", "--file", str(context / "python-tools.Dockerfile"),
                                "--build-arg", "PYTHON_VERSION=" + self.python_version,
                                "--tag", self.image, str(context)], timeout=600)
            (self.output / "image_build.json").write_text(json.dumps(build, ensure_ascii=False, indent=2), encoding="utf-8")
            if build["exit_code"]:
                raise RuntimeError("文本环境构建工具镜像未就绪；详见 image_build.json")
        args = ["docker", "run", "-d", "--rm", "--name", self.name, "--read-only",
                "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit", "192", "--memory", "4g", "--cpus", "2",
                "--tmpfs", "/tmp:rw,exec,size=2g", "--tmpfs", "/work:rw,exec,size=1g",
                "--mount", f"type=bind,src={self.repo},dst=/source,readonly",
                "--mount", f"type=bind,src={self.output},dst=/results", "--workdir", "/work",
                "--env", "PYTHONUNBUFFERED=1", "--env", "PIP_DISABLE_PIP_VERSION_CHECK=1",
                "--env", "HOME=/tmp/home", "--env", "PIP_CACHE_DIR=/tmp/pip-cache", "--env", "XDG_CACHE_HOME=/tmp/cache",
                "--env", "JUPYTER_CONFIG_DIR=/tmp/jupyter", "--env", "IPYTHONDIR=/tmp/ipython",
                "--env", "HF_HUB_OFFLINE=1", "--env", "TRANSFORMERS_OFFLINE=1"]
        for resource in self.datasets:
            path = Path(resource["host_path"]).resolve()
            data_root = Path(__file__).resolve().parents[1] / "external/datasets"
            if not path.is_dir() or not path.is_relative_to(data_root.resolve()):
                raise ValueError("实验数据必须来自项目中已核验的 external/datasets")
            name = resource["name"]
            if not re.fullmatch(r"[a-z][a-z0-9-]*", name) or resource["container_path"] != "/datasets/" + name:
                raise ValueError("非法数据挂载名称")
            args += ["--mount", f"type=bind,src={path},dst=/datasets/{name},readonly"]
        if self.broker:
            endpoint = f"http://host.docker.internal:{self.broker.port}/v1"
            args += ["--add-host", "host.docker.internal:host-gateway", "--env", "OPENAI_API_KEY=" + self.broker.token,
                     "--env", "OPENAI_BASE_URL=" + endpoint, "--env", "OPENAI_API_BASE=" + endpoint]
        args += [self.image, "sleep", "1800"]
        result = self._host(args, timeout=300)
        if result["exit_code"]:
            self._host(["docker", "rm", "-f", self.name])
            raise RuntimeError("容器启动失败：" + result["stderr"][-1200:])
        image = self._host(["docker", "image", "inspect", "--format", "{{.Id}}", self.image])
        (self.output / "environment.json").write_text(json.dumps({"image": self.image, "image_id": image["stdout"].strip(),
                  "cpus": 2, "memory": "4g", "root_readonly": True, "container": self.name, "build_tools": self.build_tools,
                  "relative_data_alias": "/work/datasets -> /datasets" if self.datasets else None,
                  "datasets": [{k: v for k, v in r.items() if k != "host_path"} for r in self.datasets]}, indent=2), encoding="utf-8")
        setup = "cp -a /source/. /work/ && python -m venv /tmp/venv"
        if self.datasets:
            setup += " && ln -s /datasets /work/datasets"
        result = self._host(["docker", "exec", self.name, "sh", "-c", setup])
        if result["exit_code"]:
            self.__exit__()
            raise RuntimeError("容器工作目录初始化失败")
        return self

    def command(self, argv: list[str], cwd=".", timeout=300, label="run"):
        safe_relative(cwd)
        if not argv or argv[0] not in {"python", "python3", "pip", "pip3", "pytest", "bash", "sh"}:
            raise ValueError("实验命令只允许 Python、pip、pytest 或仓库 shell 脚本")
        if argv[0] in {"python", "python3"}:
            argv = ["/tmp/venv/bin/python", *argv[1:]]
        elif argv[0] in {"pip", "pip3"}:
            argv = ["/tmp/venv/bin/python", "-m", "pip", *argv[1:]]
        elif argv[0] == "pytest":
            argv = ["/tmp/venv/bin/python", "-m", "pytest", *argv[1:]]
        host = ["docker", "exec", "--workdir", "/work" + ("/" + cwd if cwd != "." else ""),
                "--env", "PATH=/tmp/venv/bin:/usr/local/bin:/usr/bin:/bin", self.name, *argv]
        result = self._host(host, timeout=min(timeout, 600))
        record = {"phase": label, "argv": argv, "cwd": cwd, **result}
        if self.broker:
            record = json.loads(json.dumps(record).replace(self.broker.token, "<temporary-token>"))
        self.records.append(record)
        (self.output / "commands.json").write_text(json.dumps(self.records, ensure_ascii=False, indent=2), encoding="utf-8")
        if result.get("timed_out"):
            self._host(["docker", "kill", self.name])
        return record

    def patch(self, path: str, old: str, new: str):
        path = safe_relative(path)
        if Path(path).suffix not in {".py", ".ipynb", ".sh", ".yaml", ".yml", ".json"}:
            raise ValueError("补丁只允许实验脚本或运行配置")
        patched = self.output / "patches" / path
        original = patched if patched.exists() else self.repo / path
        if not original.is_file():
            raise ValueError("补丁目标不是原仓库文件")
        text = original.read_text(encoding="utf-8")
        # Write only a patched copy in the run artifacts; immutable source stays intact.
        patched.parent.mkdir(parents=True, exist_ok=True)
        patched.write_text(patched_text(text, Path(path).suffix, old, new), encoding="utf-8")
        relative = patched.relative_to(self.output).as_posix()
        result = self._host(["docker", "exec", self.name, "cp", "/results/" + relative, "/work/" + path])
        if result["exit_code"]:
            raise RuntimeError("应用兼容性补丁失败")

    def collect(self, paths: list[str]):
        collected = []
        records = []
        for path in paths[:12]:
            path = safe_relative(path)
            check = self._host(["docker", "exec", self.name, "python", "-c",
                                "import pathlib,sys;p=pathlib.Path(sys.argv[1]);sys.exit(0 if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(pathlib.Path('/work')) and p.stat().st_size<=10000000 else 1)", "/work/" + path])
            if check["exit_code"]:
                records.append({"path": path, "status": "not_found", "detail": check["stderr"]})
                continue
            target = self.output / "artifacts" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            # Docker's archive API cannot reliably read tmpfs on Docker Desktop.
            # Copy inside the container to the already mounted output directory.
            result = self._host(["docker", "exec", self.name, "cp", "--", "/work/" + path, "/results/artifacts/" + path])
            records.append({"path": path, "exit_code": result["exit_code"], "stderr": result["stderr"]})
            if result["exit_code"] == 0 and target.exists():
                collected.append(str(target.relative_to(self.output)).replace("\\", "/"))
        (self.output / "collection.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        return collected

    def __exit__(self, *args):
        self._host(["docker", "rm", "-f", self.name], timeout=30)
