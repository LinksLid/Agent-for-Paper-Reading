import hashlib
import json

from paperteam.config import Settings
from paperteam.repository_experiments import ExperimentPlan, ExperimentCommand
from paperteam import resume_experiment


def test_api_resume_does_not_construct_or_charge_codex(tmp_path, monkeypatch):
    commit = "a" * 40
    repo = tmp_path / "external/repos/owner--repo" / commit[:12]
    repo.mkdir(parents=True)
    text = "print('ok')\n"
    (repo / "run.py").write_text(text, encoding="utf-8")
    previous = tmp_path / "output/previous/p"
    previous.mkdir(parents=True)
    (previous / "result.json").write_text(json.dumps({"paper_id": "p", "filename": "paper.pdf"}), encoding="utf-8")
    source = {"url": "https://github.com/owner/repo", "repository": "owner/repo", "commit": commit,
              "files": {"run.py": hashlib.sha256((repo / "run.py").read_bytes()).hexdigest()}}
    (previous / "source.json").write_text(json.dumps(source), encoding="utf-8")
    plan = ExperimentPlan(runnable=True, scope="smoke_test", reason="fixture", data_source="fixture",
                          missing_requirements=[], uses_llm=False, install=[], artifacts=[], differences_from_paper=[],
                          run=[ExperimentCommand(argv=["python", "run.py"], source_file="run.py",
                                                 source_quote="print('ok')", purpose="fixture")])
    (previous / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")

    class FakeLLM:
        calls = 0
        records = []
        def __init__(self, *args):
            pass
        def close(self):
            pass

    class FakeGithub:
        def __init__(self, *args):
            pass
        def close(self):
            pass

    class Graph:
        def invoke(self, state):
            return {**state, "status": "executed", "stage": "assess"}

    def build(llm, *args, **kwargs):
        assert kwargs["planner"] is llm
        return Graph()

    def unexpected_codex(*args):
        raise AssertionError("API resume must not initialize Codex")

    monkeypatch.setattr(resume_experiment, "LLM", FakeLLM)
    monkeypatch.setattr(resume_experiment, "GitHub", FakeGithub)
    monkeypatch.setattr(resume_experiment, "CodexPlanner", unexpected_codex)
    monkeypatch.setattr(resume_experiment, "build_repository_workflow", build)
    summary, _ = resume_experiment.resume(previous, Settings(root=tmp_path), backend="api", progress=lambda _: None)
    assert summary["papers"][0]["planning_backend"] == "api"
    assert summary["codex_calls"] == 0
