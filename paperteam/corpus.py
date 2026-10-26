import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from pypdf import PdfReader


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.lower())


def ingest(paths: list[Path], cache_dir: Path) -> list[dict]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    papers = []
    for path in paths:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        cached = cache_dir / (digest + ".json")
        if cached.exists():
            paper = json.loads(cached.read_text(encoding="utf-8"))
            paper["path"] = str(path.resolve())
        else:
            reader = PdfReader(path)
            chunks = []
            for page_num, page in enumerate(reader.pages, 1):
                text = normalize(page.extract_text() or "")
                for i, start in enumerate(range(0, len(text), 1500)):
                    block = text[start:start + 1800]
                    if block.strip():
                        chunks.append({"chunk_id": f"{digest[:10]}:p{page_num}:c{i}",
                                       "paper_id": digest[:10], "page": page_num, "text": block})
            if not chunks:
                raise ValueError(f"{path.name} 无可提取文本；当前版本需要文本型 PDF。")
            full = "\n".join(c["text"] for c in chunks)
            urls = sorted(set(u.rstrip('.,;:)') for u in re.findall(
                r"https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full)))
            paper = {"paper_id": digest[:10], "sha256": digest, "filename": path.name,
                     "path": str(path.resolve()), "pages": len(reader.pages),
                     "chunks": chunks, "github_urls": urls}
            cached.write_text(json.dumps(paper, ensure_ascii=False, indent=2), encoding="utf-8")
        papers.append(paper)
    return papers


def retrieve(paper: dict, queries: list[str], k: int = 8) -> list[dict]:
    """BM25-style local retrieval, no embedding service or full-PDF API upload."""
    chunks = paper["chunks"]
    docs = [Counter(tokens(c["text"])) for c in chunks]
    df = Counter(t for d in docs for t in d)
    avg = sum(sum(d.values()) for d in docs) / max(len(docs), 1)
    query = set(tokens(" ".join(queries)))
    scored = []
    for i, d in enumerate(docs):
        length = sum(d.values())
        score = sum(math.log(1 + (len(docs) - df[t] + .5) / (df[t] + .5)) *
                    d[t] * 2.2 / (d[t] + 1.2 * (.25 + .75 * length / max(avg, 1)))
                    for t in query if d[t])
        scored.append((score, i))
    ranked = sorted(scored, reverse=True)
    # Always include the opening abstract as context.
    indices = [0] + [i for _, i in ranked if i != 0][:max(0, k - 1)]
    return [chunks[i] for i in indices]


def evidence_errors(claim: dict, chunks: dict[str, dict], allowed_papers: set[str] | None = None) -> list[str]:
    errors = []
    if allowed_papers is not None and claim["paper_id"] not in allowed_papers:
        errors.append("未知 paper_id")
    if not claim.get("evidence"):
        errors.append("没有证据")
    if any(phrase in claim["text"] for phrase in ("所给材料", "所提供材料", "当前材料", "本次输出", "本次检索", "无法在本次")):
        errors.append("输入材料的证据缺口应列为待研究问题，不能当成论文事实")
    for evidence in claim.get("evidence", []):
        chunk = chunks.get(evidence["chunk_id"])
        if not chunk:
            errors.append("不存在的引用块")
        elif normalize(evidence["quote"]) not in normalize(chunk["text"]):
            errors.append("quote 不是原文连续片段")
        elif claim["kind"] != "comparison" and chunk["paper_id"] != claim["paper_id"]:
            errors.append("引用属于另一篇论文")
    return errors
