"""Discover paper repositories; download immutable GitHub source snapshots."""
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import time
import subprocess
from urllib.parse import urlsplit
import zipfile

import httpx
from pydantic import BaseModel, Field

from .corpus import normalize


class PaperSearch(BaseModel):
    title: str
    authors: list[str]
    queries: list[str] = Field(min_length=1, max_length=3)


class RepositoryChoice(BaseModel):
    repository: str | None
    confidence: str
    readme_quote: str
    reason: str


def repo_name(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2 or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts[:2]):
        return None
    if parts[0].lower() in {"search", "topics", "features", "settings", "login"}:
        return None
    return "/".join(parts[:2]).removesuffix(".git")


def github_proxy():
    """Use Windows' active proxy for GitHub only; do not change global Git config."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            proxy = winreg.QueryValueEx(key, "ProxyServer")[0]
        if enabled and re.fullmatch(r"(?:127\.0\.0\.1|localhost):\d+", proxy):
            return "http://" + proxy
    except (ImportError, OSError):
        pass
    return None


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def unpack_snapshot(raw: bytes, destination: Path, max_unpacked=600_000_000):
    root = destination.resolve()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sum(item.file_size for item in archive.infolist()) > max_unpacked:
            raise ValueError("源码解压体积超过限制")
        validated = []
        for item in archive.infolist():
            if "\\" in item.filename:
                raise ValueError("非法压缩包路径")
            parts = PurePosixPath(item.filename).parts
            if len(parts) < 2:
                continue
            if any(p in {"..", ".git"} for p in parts) or parts[0] == "/":
                raise ValueError("非法压缩包路径")
            target = root.joinpath(*parts[1:]).resolve()
            if not target.is_relative_to(root) or ((item.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("拒绝越界路径或符号链接")
            validated.append((item, target))
        for item, target in validated:
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))


class GitHub:
    def __init__(self, root: Path):
        self.root = root
        self.cache = root / ".cache/github"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.client = httpx.Client(timeout=45, trust_env=False, proxy=github_proxy(),
                                   headers={"User-Agent": "PaperTeam/0.2", "Accept": "application/vnd.github+json"})

    def close(self):
        self.client.close()

    def json(self, path, params=None):
        cached = self.cache / (hashlib.sha256(json.dumps([path, params], sort_keys=True).encode()).hexdigest() + ".json")
        if cached.exists() and time.time() - cached.stat().st_mtime < 3600:
            return json.loads(cached.read_text(encoding="utf-8"))
        response = self.client.get("https://api.github.com" + path, params=params)
        if response.status_code in {403, 429}:
            raise RuntimeError("GitHub 搜索或公共 API 额度受限；搜索记录已保留，可稍后重试。")
        response.raise_for_status()
        data = response.json()
        save_json(cached, data)
        return data

    def candidate(self, name: str):
        metadata = self.json("/repos/" + name)
        readme = self.client.get(f"https://api.github.com/repos/{name}/readme",
                                 headers={"Accept": "application/vnd.github.raw+json"})
        readme.raise_for_status()
        return {"repository": metadata["full_name"], "url": metadata["html_url"],
                "description": metadata.get("description"), "fork": metadata.get("fork", False),
                "default_branch": metadata["default_branch"], "size_kb": metadata.get("size"),
                "readme": readme.text[:18000]}

    def discover(self, paper, llm, run_dir: Path):
        cache = self.cache / ("discovery-" + paper["paper_id"] + ".json")
        if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
            evidence = json.loads(cache.read_text(encoding="utf-8"))
            choice = evidence.get("selection", {}).get("repository")
            selected = next((c for c in evidence.get("candidates", []) if c["repository"].lower() == (choice or "").lower()), None)
            if selected and normalize(evidence["selection"]["readme_quote"]) in normalize(selected["readme"]):
                evidence["cache_reused"] = True
                save_json(run_dir / "discovery.json", evidence)
                return selected, evidence
        metadata = llm.structured("复现/论文代码搜索", "从论文标题与摘要确定准确标题、作者与 GitHub 搜索词。"
                                  "给出最多两个 query：优先独特的方法名 in:name，再用精确论文标题 in:readme。"
                                  "不要搜索通用的 agent、LLM 等关键词。\n文件：" + paper["filename"] +
                                  "\n正文：" + paper["chunks"][0]["text"], PaperSearch)
        names, searches = [], []
        for url in paper.get("github_urls", []):
            name = repo_name(url)
            if name and name not in names:
                names.append(name)
        # Always search: PDF URLs may refer to dependencies rather than the authors' code.
        for query in metadata.queries[:2]:
            try:
                found = self.json("/search/repositories", {"q": query, "per_page": 4})
                rows = [{"repository": i["full_name"], "description": i.get("description")} for i in found.get("items", [])]
                searches.append({"query": query, "results": rows})
                for row in rows:
                    if row["repository"] not in names:
                        names.append(row["repository"])
            except Exception as error:
                searches.append({"query": query, "error_type": type(error).__name__})
            if len(names) >= 5:
                break
        candidates, errors = [], []
        for name in names[:7]:
            try:
                candidates.append(self.candidate(name))
            except Exception as error:
                errors.append({"repository": name, "error_type": type(error).__name__})
        evidence = {"paper": metadata.model_dump(), "pdf_urls": paper.get("github_urls", []),
                    "searches": searches, "candidates": candidates, "lookup_errors": errors}
        save_json(run_dir / "discovery.json", evidence)
        if not candidates:
            return None, {**evidence, "reason": "未获取到可核验的候选仓库；网络失败不能等同于论文没有开源代码。"}
        choice = llm.structured("复现/仓库对应核验", "选择与论文标题和作者对应的作者代码仓库。"
                                "排除论文引用的依赖库、同名项目、仅列出该论文的资料列表与无关 fork。"
                                "必须从候选 README 逐字摘录能证明该仓库实现该论文的连续原文 readme_quote。"
                                "不能确定则 repository=null。\n" + json.dumps({"paper": metadata.model_dump(),
                                "candidates": [{**c, "readme": c["readme"][:7000]} for c in candidates]}, ensure_ascii=False), RepositoryChoice)
        evidence["selection"] = choice.model_dump()
        selected = next((c for c in candidates if c["repository"].lower() == (choice.repository or "").lower()), None)
        if selected and (len(choice.readme_quote) < 12 or normalize(choice.readme_quote) not in normalize(selected["readme"])):
            selected = None
            evidence["selection_error"] = "对应关系证明不是候选 README 的连续原文"
        save_json(run_dir / "discovery.json", evidence)
        if selected:
            save_json(cache, evidence)
        return selected, evidence

    def download(self, candidate, run_dir: Path, max_mb=150):
        name = candidate["repository"]
        base = self.root / "external/repos" / name.replace("/", "--")
        cached_sources = sorted(base.glob("*/.paperteam-source.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if cached_sources and time.time() - cached_sources[0].stat().st_mtime < 86400:
            saved = json.loads(cached_sources[0].read_text(encoding="utf-8"))
            destination = cached_sources[0].parent
            for relative, digest in saved["files"].items():
                path = destination / relative
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ValueError("缓存源码被修改：" + relative)
            save_json(run_dir / "source.json", saved)
            return destination, saved
        try:
            commit = self.json(f"/repos/{name}/commits/{candidate['default_branch']}")["sha"]
        except (RuntimeError, httpx.HTTPError):
            # Git's read-only ref lookup does not consume shared REST API quotas.
            proxy = github_proxy() or ""
            result = subprocess.run(["git", "-c", "http.proxy=" + proxy, "ls-remote", "https://github.com/" + name + ".git", "HEAD"],
                                    capture_output=True, text=True, timeout=40)
            commit = result.stdout.split()[0] if result.returncode == 0 and result.stdout.split() else ""
            if not re.fullmatch(r"[0-9a-f]{40}", commit):
                raise RuntimeError("无法核实仓库提交：GitHub API 和 Git ref 查询均失败") from None
        destination = self.root / "external/repos" / name.replace("/", "--") / commit[:12]
        marker = destination / ".paperteam-source.json"
        if marker.exists():
            saved = json.loads(marker.read_text(encoding="utf-8"))
            if saved["commit"] != commit:
                raise ValueError("缓存提交与目标不符")
            return destination, saved
        if destination.exists():
            raise ValueError("源码目录下载未完成，请使用新的下载目录重试")
        url = f"https://codeload.github.com/{name}/zip/{commit}"
        raw = bytearray()
        with self.client.stream("GET", url, timeout=120) as response:
            response.raise_for_status()
            for block in response.iter_bytes():
                raw.extend(block)
                if len(raw) > max_mb * 1024 * 1024:
                    raise ValueError(f"源码压缩包超过 {max_mb} MB 上限")
        unpack_snapshot(bytes(raw), destination)
        files = {str(p.relative_to(destination)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in destination.rglob("*") if p.is_file()}
        source = {"repository": name, "url": candidate["url"], "commit": commit,
                  "archive_sha256": hashlib.sha256(raw).hexdigest(), "archive_bytes": len(raw),
                  "transport": "github_commit_archive", "files": files}
        save_json(marker, source)
        save_json(run_dir / "source.json", source)
        return destination, source


def inspect_repository(repo: Path):
    paths = []
    for p in repo.rglob("*"):
        if p.is_file() and not any(x.startswith(".") for x in p.relative_to(repo).parts):
            paths.append(p.relative_to(repo).as_posix())
    def priority(path):
        name = Path(path).name.lower()
        if "/" not in path and name.startswith(("readme", "requirements", "environment")):
            return 100
        if path in {"run/configs/CHESS_IR_CG_UT.yaml", "src/llm/engine_configs.py", "src/llm/models.py",
                    "src/runner/database_manager.py", "src/database_utils/db_catalog/preprocess.py",
                    "src/database_utils/db_values/preprocess.py"}:
            return 98
        if "/" not in path and name in {"pyproject.toml", "setup.py"}:
            return 95
        if path in {"core/api_config.py", "core/llm.py", "core/chat_manager.py", "core/agents.py"}:
            return 94
        if "/" not in path and name.endswith((".ipynb", ".py", ".sh", ".bat")):
            return 90
        if path.endswith((".py", ".sh")) and any(t in path.lower() for t in ("run_", "eval", "main", "infer")):
            return 80
        if name.startswith("readme"):
            return 10
        return 0
    important = sorted((p for p in paths if priority(p)), key=lambda p: (-priority(p), p))[:35]
    contents, used = {}, 0
    for path in dict.fromkeys(important):
        p = repo / path
        if p.stat().st_size > 250_000:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if p.suffix == ".ipynb":
            try:
                notebook = json.loads(text)
                text = "\n\n".join("".join(c.get("source", [])) for c in notebook["cells"])
            except (ValueError, KeyError):
                continue
        text = text[:10000]
        if used + len(text) > 65000:
            break
        contents[path] = text
        used += len(text)
    return {"files": paths[:1800], "contents": contents}
