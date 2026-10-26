"""Verified public benchmark subsets; data is never committed with the code."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import time
import zipfile

import httpx

from .repositories import save_json, github_proxy

BIRD_URL = "https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip"


def dataset_context(resources):
    return [{"name": r["name"], "container_path": r["container_path"],
             "descriptor": {k: v for k, v in r["descriptor"].items() if k != "files"},
             "file_count": len(r["descriptor"].get("files", {})),
             "files_sample": list(r["descriptor"].get("files", {}))[:12]} for r in resources]


def digest(path: Path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download_bird(root: Path, samples=6, progress=print):
    if not 1 <= samples <= 100:
        raise ValueError("数据子集样本数必须在 1 到 100 之间")
    cache = root / "external/datasets/bird-dev"
    if not cache.resolve().is_relative_to(root.resolve()):
        raise ValueError("数据缓存不能越出项目目录")
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / "dev.zip"
    source_file = cache / "download.json"
    source = json.loads(source_file.read_text(encoding="utf-8")) if source_file.exists() else {}
    if not archive.exists() or source.get("sha256") != digest(archive):
        if shutil.disk_usage(cache).free < 2_000_000_000:
            raise RuntimeError("BIRD 数据准备需要至少 2GB 剩余空间")
        partial = cache / "dev.zip.partial"
        size, reported = 0, 0
        with httpx.Client(timeout=90, follow_redirects=True, trust_env=False) as client:
            with client.stream("GET", BIRD_URL) as response:
                response.raise_for_status()
                if int(response.headers.get("content-length", 0)) > 500_000_000:
                    raise RuntimeError("BIRD 官方压缩包超过 500MB 下载上限")
                with partial.open("wb") as stream:
                    for chunk in response.iter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > 500_000_000:
                            raise RuntimeError("BIRD 数据超过下载上限")
                        stream.write(chunk)
                        if size - reported >= 64 * 1024 * 1024:
                            progress(f"BIRD downloaded {size // (1024 * 1024)} MiB")
                            reported = size
        if not zipfile.is_zipfile(partial):
            raise RuntimeError("官方响应不是有效的 ZIP 数据包")
        partial.replace(archive)
        source = {"url": BIRD_URL, "sha256": digest(archive), "bytes": size, "downloaded_at": time.time()}
        save_json(source_file, source)
    progress("BIRD archive verified; preparing deterministic first-N subset")
    destination = cache / f"first-{samples}"
    if not destination.resolve().is_relative_to(cache.resolve()):
        raise ValueError("数据子集目录越界")
    marker = destination / "dataset.json"
    if marker.exists():
        descriptor = json.loads(marker.read_text(encoding="utf-8"))
        if descriptor.get("source_sha256") == source["sha256"] and descriptor.get("files") and all(
                (destination / name).is_file() and digest(destination / name) == sha
                for name, sha in descriptor.get("files", {}).items()):
            return destination, descriptor
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        names = [name for name in bundle.namelist() if "__MACOSX" not in name]
        question_name = next(name for name in names if PurePosixPath(name).name == "dev.json")
        table_name = next(name for name in names if PurePosixPath(name).name == "dev_tables.json")
        questions = json.loads(bundle.read(question_name))
        subset = questions[:samples]
        databases = {row["db_id"] for row in subset}
        save_json(destination / "dev.json", subset)
        save_json(destination / "dev_tables.json", json.loads(bundle.read(table_name)))
        (destination / "dev_gold.sql").write_text("\n".join(
            row["SQL"].replace("\n", " ") + "\t" + row["db_id"] for row in subset) + "\n", encoding="utf-8")
        nested_name = next((name for name in names if PurePosixPath(name).name == "dev_databases.zip"), None)
        if nested_name:
            nested = cache / "dev_databases.zip"
            if not nested.exists() or nested.stat().st_size != bundle.getinfo(nested_name).file_size:
                with bundle.open(nested_name) as src, nested.open("wb") as target:
                    shutil.copyfileobj(src, target, 1024 * 1024)
            database_zip = zipfile.ZipFile(nested)
        else:
            database_zip = bundle
        try:
            total = 0
            for info in database_zip.infolist():
                path = PurePosixPath(info.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in info.filename or any(":" in p for p in path.parts):
                    raise ValueError("数据压缩包包含非法路径")
                if "dev_databases" not in path.parts or info.is_dir():
                    continue
                index = path.parts.index("dev_databases")
                parts = path.parts[index:]
                if len(parts) < 3 or parts[1] not in databases:
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("数据包包含符号链接")
                if path.suffix.lower() not in {".sqlite", ".csv", ".txt", ".json"}:
                    continue
                total += info.file_size
                if total > 1_000_000_000:
                    raise RuntimeError("所选数据库解压超过 1GB 上限")
                target = destination.joinpath(*parts)
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ValueError("数据文件路径越界")
                target.parent.mkdir(parents=True, exist_ok=True)
                with database_zip.open(info) as src, target.open("wb") as sink:
                    shutil.copyfileobj(src, sink, 1024 * 1024)
        finally:
            if database_zip is not bundle:
                database_zip.close()
    for db_id in databases:
        if not (destination / "dev_databases" / db_id / (db_id + ".sqlite")).is_file():
            raise RuntimeError("官方数据包中缺少子集需要的数据库：" + db_id)
    descriptor = {"name": "bird-dev", "source_url": BIRD_URL, "source_sha256": source["sha256"],
                  "selection": "first-N in official dev.json; no outcome-based selection", "samples": len(subset),
                  "question_ids": [row.get("question_id", i) for i, row in enumerate(subset)],
                  "databases": sorted(databases), "full_split_samples": len(questions),
                  "files": {p.relative_to(destination).as_posix(): digest(p)
                            for p in destination.rglob("*") if p.is_file() and p.name != "dataset.json"}}
    save_json(marker, descriptor)
    return destination, descriptor


def available_datasets(root: Path, samples=6):
    """Only framework-prepared data under the fixed external/datasets directory."""
    resources = []
    directory = (root / "external/datasets").resolve()
    if not directory.is_relative_to(root.resolve()):
        raise ValueError("数据缓存不能越出项目目录")
    folder = directory / "bird-dev" / f"first-{samples}"
    marker = folder / "dataset.json"
    if marker.is_file() and folder.resolve().is_relative_to(directory):
        descriptor = json.loads(marker.read_text(encoding="utf-8"))
        if descriptor.get("files") and all((folder / name).resolve().is_relative_to(folder.resolve())
                and (folder / name).is_file() and digest(folder / name) == sha
                for name, sha in descriptor["files"].items()):
            resources.append({"name": "bird-dev", "host_path": str(folder), "container_path": "/datasets/bird-dev",
                              "descriptor": descriptor})
    folder = directory / "alfworld/valid-unseen"
    marker = folder / "dataset.json"
    if marker.is_file() and folder.resolve().is_relative_to(directory):
        descriptor = json.loads(marker.read_text(encoding="utf-8"))
        if descriptor.get("files") and all((folder / name).resolve().is_relative_to(folder.resolve())
                and (folder / name).is_file() and digest(folder / name) == sha
                for name, sha in descriptor["files"].items()):
            resources.append({"name": "alfworld", "host_path": str(folder), "container_path": "/datasets/alfworld",
                              "descriptor": descriptor})
    return resources


def download_alfworld(root: Path, progress=print):
    """Text-only official evaluation games; no detector or pretrained agent weights."""
    cache = root / "external/datasets/alfworld"
    destination = cache / "valid-unseen"
    if not destination.resolve().is_relative_to(root.resolve()):
        raise ValueError("ALFWorld 数据缓存越界")
    destination.mkdir(parents=True, exist_ok=True)
    sources = []
    for release, filename in [("0.2.2", "json_2.1.1_json.zip"), ("0.2.2", "json_2.1.1_pddl.zip"),
                              ("0.4.2", "json_2.1.3_tw-pddl.zip")]:
        url = f"https://github.com/alfworld/alfworld/releases/download/{release}/{filename}"
        archive = cache / filename
        marker = archive.with_suffix(".source.json")
        known = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
        if not archive.is_file() or digest(archive) != known.get("sha256"):
            progress("Downloading official ALFWorld " + filename)
            partial = archive.with_suffix(".partial")
            size = 0
            with httpx.Client(timeout=90, follow_redirects=True, trust_env=False, proxy=github_proxy()) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with partial.open("wb") as stream:
                        for chunk in response.iter_bytes(1024 * 1024):
                            size += len(chunk)
                            if size > 200_000_000:
                                raise RuntimeError("ALFWorld 单个文本数据包超过 200MB 上限")
                            stream.write(chunk)
            if not zipfile.is_zipfile(partial):
                raise RuntimeError("ALFWorld 官方数据不是 ZIP 文件")
            partial.replace(archive)
            known = {"url": url, "sha256": digest(archive), "bytes": archive.stat().st_size}
            save_json(marker, known)
        sources.append(known)
        with zipfile.ZipFile(archive) as bundle:
            size = 0
            for info in bundle.infolist():
                path = PurePosixPath(info.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in info.filename or any(":" in p for p in path.parts):
                    raise ValueError("ALFWorld 数据包路径非法")
                if "valid_unseen" not in path.parts or info.is_dir():
                    continue
                if "__MACOSX" in path.parts or path.suffix not in {".json", ".pddl", ".tw-pddl"}:
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("ALFWorld 数据包包含符号链接")
                index = path.parts.index("valid_unseen")
                parts = ("json_2.1.1", *path.parts[index:])
                target = destination.joinpath(*parts)
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ValueError("ALFWorld 数据文件路径越界")
                size += info.file_size
                if size > 250_000_000:
                    raise RuntimeError("ALFWorld 评测集解压超过 250MB")
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as src, target.open("wb") as sink:
                    shutil.copyfileobj(src, sink)
    games = sorted(destination.rglob("game.tw-pddl"))
    if not games:
        raise RuntimeError("未找到真实 ALFWorld 游戏文件")
    descriptor = {"name": "alfworld", "sources": sources, "split": "eval_out_of_distribution / valid_unseen",
                  "game_files": len(games), "data_root": "/datasets/alfworld",
                  "notes": "Official text games only. Set ALFWORLD_DATA=/datasets/alfworld; logic files are available from the installed alfworld package (alfworld.info.ALFRED_PDDL_PATH / ALFRED_TWL2_PATH). No detector is required. Use published alfworld 0.4.2 text-only package (master 0.5.0 is not on PyPI), get_environment(config['env']['type']) for its current API. Set use_cuda=false. Preserve the notebook's original action loop and prompts; cap outer episode loop, export actual rs/cnts and game names. Wikipedia HTTP 403 is not bypassed.",
                  "files": {p.relative_to(destination).as_posix(): digest(p)
                            for p in destination.rglob("*") if p.is_file() and p.name != "dataset.json"}}
    save_json(destination / "dataset.json", descriptor)
    progress(f"ALFWorld valid_unseen prepared: {len(games)} official game files")
    return destination, descriptor
