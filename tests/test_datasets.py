import io
import json
from pathlib import Path
import zipfile

import httpx
import pytest

from paperteam import repository_experiments as experiments
from paperteam.datasets import BIRD_URL, available_datasets, dataset_context, digest, download_bird
from paperteam.repository_experiments import ExperimentCommand, ExperimentPlan, validate_plan
from paperteam.repository_experiments import extract_metrics


def cached_bird(root, malicious=False):
    folder = root / "external/datasets/bird-dev"
    folder.mkdir(parents=True)
    data = [{"question_id": i, "db_id": "real_db", "question": f"Question {i}", "SQL": f"SELECT {i}"}
            for i in range(3)]
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as archive:
        archive.writestr("dev_databases/real_db/real_db.sqlite", b"fixture SQLite bytes")
        if malicious:
            archive.writestr(malicious if isinstance(malicious, str) else "dev_databases/../../escape.sqlite", b"bad")
    archive_path = folder / "dev.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dev_20240627/dev.json", json.dumps(data))
        archive.writestr("dev_20240627/dev_tables.json", "[]")
        archive.writestr("dev_20240627/dev_databases.zip", nested.getvalue())
    (folder / "download.json").write_text(json.dumps({"url": BIRD_URL, "sha256": digest(archive_path)}), encoding="utf-8")
    return data


def test_bird_subset_preserves_official_order_and_separates_gold(tmp_path):
    original = cached_bird(tmp_path)
    path, manifest = download_bird(tmp_path, samples=2, progress=lambda _: None)
    assert json.loads((path / "dev.json").read_text(encoding="utf-8")) == original[:2]
    assert (path / "dev_gold.sql").read_text(encoding="utf-8").splitlines() == ["SELECT 0\treal_db", "SELECT 1\treal_db"]
    assert manifest["question_ids"] == [0, 1] and manifest["full_split_samples"] == 3
    assert manifest["files"]["dev_databases/real_db/real_db.sqlite"] == digest(path / "dev_databases/real_db/real_db.sqlite")
    assert len(available_datasets(tmp_path, 2)) == 1


def test_tampered_dataset_is_not_mounted(tmp_path):
    cached_bird(tmp_path)
    path, _ = download_bird(tmp_path, 2, progress=lambda _: None)
    (path / "dev.json").write_text("[]", encoding="utf-8")
    assert available_datasets(tmp_path, 2) == []


@pytest.mark.parametrize("member", ["dev_databases/../../escape.sqlite", "dev_databases/real_db/C:/escape.sqlite"])
def test_dataset_archive_cannot_escape_cache(tmp_path, member):
    cached_bird(tmp_path, malicious=member)
    with pytest.raises(ValueError, match="非法路径"):
        download_bird(tmp_path, 2, progress=lambda _: None)
    assert not (tmp_path / "external/datasets/bird-dev/escape.sqlite").exists()


def test_planner_context_is_bounded_and_has_no_host_path(tmp_path):
    cached_bird(tmp_path)
    download_bird(tmp_path, 2, progress=lambda _: None)
    context = dataset_context(available_datasets(tmp_path, 2))[0]
    assert "host_path" not in context and "files" not in context["descriptor"]
    assert context["container_path"] == "/datasets/bird-dev" and context["file_count"] > 0


def test_real_python_json_export_is_not_rejected_as_a_placeholder():
    command = ExperimentCommand(argv=["python", "-c", "import json; print(json.dumps({'score': 0.5}))"],
                                source_file="README.md", source_quote="evaluate", purpose="export actual metrics")
    plan = ExperimentPlan(runnable=True, scope="benchmark_subset", reason="test", data_source="official data",
                          missing_requirements=[], uses_llm=False, install=[], run=[command], artifacts=[], differences_from_paper=[])
    validate_plan(plan, {"files": [], "contents": {"README.md": "evaluate"}})
    plan.run[0].argv = ["python", "{missing_script}"]
    with pytest.raises(ValueError, match="未填参数"):
        validate_plan(plan, {"files": [], "contents": {"README.md": "evaluate"}})


def test_wikipedia_403_cannot_be_reported_as_a_completed_benchmark(tmp_path, monkeypatch):
    (tmp_path / "wikienv.py").write_text("# fixture", encoding="utf-8")
    inventory = {"files": ["hotpotqa.ipynb"], "contents": {"README.md": "Run hotpotqa.ipynb"}}
    plan = ExperimentPlan(runnable=True, scope="benchmark_subset", reason="test", data_source="official",
                          missing_requirements=[], uses_llm=False, install=[], artifacts=[], differences_from_paper=[],
                          run=[ExperimentCommand(argv=["python", "hotpotqa.ipynb"], source_file="README.md",
                                                 source_quote="hotpotqa.ipynb", purpose="original experiment")])

    class Github:
        def discover(self, *args):
            return {"url": "https://github.com/ysymyth/ReAct"}, {}

        def download(self, *args):
            return tmp_path, {}

    class Planner:
        def structured(self, *args, **kwargs):
            return plan

    monkeypatch.setattr(experiments, "inspect_repository", lambda _: inventory)
    monkeypatch.setattr(experiments, "check_external_services", lambda _: {"wikipedia": {"available": False, "http_status": 403}})
    monkeypatch.setattr(experiments, "docker_available", lambda: pytest.fail("HTTP 403 must block before execution"))
    state = experiments.build_repository_workflow(Planner(), Github(), tmp_path / "run").invoke(
        {"paper": {"filename": "ReAct.pdf"}, "events": []})
    assert state["status"] == "blocked" and state["stage"] == "execute"
    assert "Wikipedia" in state["reason"] and not state.get("metrics")


def test_official_evaluation_rows_are_aggregated_without_dropping_failures(tmp_path):
    path = tmp_path / "artifacts/eval_result_dev.json"
    path.parent.mkdir()
    path.write_text('[{"res":1},{"res":0},{"res":1}]', encoding="utf-8")
    specs = [{"name": "EX", "artifact": "eval_result_dev.json", "json_path": "res", "aggregate": "mean"},
             {"name": "N", "artifact": "eval_result_dev.json", "json_path": "res", "aggregate": "count"}]
    metrics = extract_metrics(tmp_path, specs, ["artifacts/eval_result_dev.json"])
    assert metrics[0]["value"] == 2/3 and metrics[0]["sample_count"] == 3
    assert metrics[1]["value"] == 3
    path.write_text('[{"res":1},{"res":"missing"}]', encoding="utf-8")
    assert extract_metrics(tmp_path, specs, ["artifacts/eval_result_dev.json"]) == []


def test_saved_plan_cannot_run_after_source_changes(tmp_path):
    from paperteam.config import Settings
    from paperteam.resume_experiment import resume
    folder = tmp_path / "output/old/p"
    folder.mkdir(parents=True)
    repo = tmp_path / ("external/repos/author--paper/" + "a" * 12)
    repo.mkdir(parents=True)
    (repo / "run.py").write_text("original", encoding="utf-8")
    source = {"url": "https://github.com/author/paper", "repository": "author/paper", "commit": "a"*40,
              "files": {"run.py": digest(repo / "run.py")}}
    (folder / "source.json").write_text(json.dumps(source), encoding="utf-8")
    (folder / "result.json").write_text('{"paper_id":"p","filename":"paper.pdf"}', encoding="utf-8")
    (repo / "run.py").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="源码发生变化"):
        resume(folder, Settings(root=tmp_path))
