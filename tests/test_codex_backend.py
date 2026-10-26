import json
from pathlib import Path
import subprocess

import pytest

from paperteam import codex_backend, repository_experiments as experiments
from paperteam.codex_backend import CodexPlanner, codex_environment, strict_schema
from paperteam.repository_experiments import ExperimentPlan


def blocked_plan():
    return ExperimentPlan(runnable=False, scope="blocked", reason="Missing official dataset",
                          data_source="README requires official dataset", missing_requirements=["dataset"],
                          uses_llm=False, install=[], run=[], artifacts=[], differences_from_paper=[])


def test_codex_schema_covers_nested_defaulted_properties():
    schema = strict_schema(ExperimentPlan)
    assert schema["required"] == list(schema["properties"])
    assert schema["additionalProperties"] is False
    command = schema["$defs"]["ExperimentCommand"]
    assert "cwd" in command["required"] and "timeout_seconds" in command["required"]
    assert command["additionalProperties"] is False
    assert "default" not in command["properties"]["cwd"]


def test_cli_does_not_inherit_api_secrets_or_parent_session(monkeypatch):
    monkeypatch.setenv("API_KEY", "private-school-key")
    monkeypatch.setenv("OPENAI_API_KEY", "private-project-key")
    monkeypatch.setenv("CODEX_THREAD_ID", "parent-thread")
    monkeypatch.setenv("HTTPS_PROXY", "http://localhost:1234")
    environment = codex_environment()
    assert not {"API_KEY", "OPENAI_API_KEY", "CODEX_THREAD_ID"}.intersection(environment)
    assert environment["HTTPS_PROXY"] == "http://localhost:1234"


def fake_process(monkeypatch, payload, returncode=0, timeout=False):
    processes = []

    class Process:
        pid = 1234567

        def __init__(self, argv, **options):
            self.argv, self.options = argv, options
            self.returncode = returncode
            processes.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def communicate(self, instruction=None, timeout=None):
            self.instruction = instruction
            if timeout is not None and self.raise_timeout:
                raise subprocess.TimeoutExpired("codex", timeout)
            path = Path(self.argv[self.argv.index("--output-last-message") + 1])
            path.write_text(payload, encoding="utf-8")
            return json.dumps({"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 8}}), "private-stderr"

        raise_timeout = timeout

    monkeypatch.setattr(codex_backend, "find_codex", lambda: "codex-native-test")
    monkeypatch.setattr(codex_backend.subprocess, "Popen", Process)
    return processes


def test_cli_validates_response_and_records_separate_usage(tmp_path, monkeypatch):
    processes = fake_process(monkeypatch, blocked_plan().model_dump_json())
    client = CodexPlanner(tmp_path, max_calls=1)
    result = client.structured("experiment plan", "untrusted input", ExperimentPlan)
    process = processes[0]
    assert not result.runnable
    assert "untrusted input" in process.instruction and "untrusted input" not in process.argv
    assert process.argv[process.argv.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in process.argv and "shell_tool" in process.argv
    assert process.options.get("shell") is not True
    assert client.records[0]["usage"] == {"input_tokens": 20, "output_tokens": 8}
    assert "private-stderr" not in (tmp_path / "codex_usage.jsonl").read_text(encoding="utf-8")
    with pytest.raises(RuntimeError, match="调用上限"):
        client.structured("experiment plan", "second call", ExperimentPlan)
    assert len(processes) == 1


def test_invalid_codex_response_is_not_an_executable_plan(tmp_path, monkeypatch):
    fake_process(monkeypatch, '{"runnable":true}')
    client = CodexPlanner(tmp_path)
    with pytest.raises(RuntimeError, match="结构校验"):
        client.structured("plan", "material", ExperimentPlan)
    assert client.records[0]["status"] == "failed"


def test_cli_error_does_not_leak_private_stderr(tmp_path, monkeypatch):
    fake_process(monkeypatch, "", returncode=1)
    with pytest.raises(RuntimeError, match="未回退") as error:
        CodexPlanner(tmp_path).structured("plan", "material", ExperimentPlan)
    assert "private-stderr" not in str(error.value)


def test_codex_timeout_stops_invocation_and_records_failure(tmp_path, monkeypatch):
    processes = fake_process(monkeypatch, "", timeout=True)
    stopped = []
    monkeypatch.setattr(codex_backend, "stop_process", lambda process: stopped.append(process))
    client = CodexPlanner(tmp_path, timeout=1)
    with pytest.raises(RuntimeError, match="超时"):
        client.structured("plan", "material", ExperimentPlan)
    assert stopped == processes and client.records[0]["status"] == "failed"


def test_workflow_routes_planning_to_codex_and_retains_missing_data_block(tmp_path, monkeypatch):
    class Github:
        def discover(self, paper, llm, output):
            assert llm is api
            return {"url": "https://github.com/author/paper"}, {}

        def download(self, *args):
            return tmp_path, {"commit": "test-commit"}

    class Planner:
        def structured(self, role, prompt, schema, **kwargs):
            self.role = role
            return blocked_plan()

    api, planner = object(), Planner()
    monkeypatch.setattr(experiments, "inspect_repository", lambda path: {"files": [], "contents": {}})
    monkeypatch.setattr(experiments, "docker_available", lambda: pytest.fail("blocked plan must not execute"))
    graph = experiments.build_repository_workflow(api, Github(), tmp_path / "run", planner=planner)
    state = graph.invoke({"paper": {"filename": "paper.pdf"}, "events": []})
    assert state["status"] == "blocked" and state["stage"] == "plan"
    assert planner.role == "复现/原仓库实验规划"


def test_workflow_uses_codex_for_repair_and_api_for_assessment(tmp_path, monkeypatch):
    plan = blocked_plan().model_copy(update={"runnable": True, "scope": "benchmark_subset",
        "missing_requirements": [], "reason": "available",
        "run": [experiments.ExperimentCommand(argv=["python", "evaluate.py"], source_file="README.md",
                                                source_quote="python evaluate.py", purpose="original evaluation")],
        "artifacts": ["scores.json"],
        "metrics": [experiments.MetricSpec(name="accuracy", artifact="scores.json", json_path="accuracy")]})
    inventory = {"files": ["README.md"], "contents": {"README.md": "python evaluate.py"}}

    class Github:
        def discover(self, *args):
            return {"url": "https://github.com/author/paper"}, {}

        def download(self, *args):
            return tmp_path, {"commit": "test-commit"}

    class Planner:
        roles = []

        def structured(self, role, prompt, schema, **kwargs):
            self.roles.append(role)
            return plan

    class Api:
        roles = []

        def structured(self, role, prompt, schema):
            self.roles.append(role)
            return experiments.ExperimentAssessment(summary="fixture only", next_steps=[], limitations=[])

    class Runtime:
        attempts = 0

        def __init__(self, repo, output, *args):
            self.output = output

        def __enter__(self):
            Runtime.attempts += 1
            return self

        def __exit__(self, *args):
            pass

        def command(self, *args):
            return {"exit_code": 1 if Runtime.attempts == 1 else 0, "output": "fixture log"}

        def collect(self, paths):
            folder = self.output / "artifacts"
            folder.mkdir(parents=True)
            (folder / "scores.json").write_text('{"accuracy":0.5}', encoding="utf-8")
            return ["artifacts/scores.json"]

    monkeypatch.setattr(experiments, "inspect_repository", lambda path: inventory)
    monkeypatch.setattr(experiments, "docker_available", lambda: True)
    monkeypatch.setattr(experiments, "DockerRuntime", Runtime)
    api, planner = Api(), Planner()
    graph = experiments.build_repository_workflow(api, Github(), tmp_path / "run", planner=planner)
    state = graph.invoke({"paper": {"filename": "paper.pdf"}, "events": []})
    assert state["status"] == "completed" and state["attempts"] == 1
    assert state["metrics"][0]["value"] == 0.5
    assert planner.roles == ["复现/原仓库实验规划", "复现/根据运行错误修复"]
    assert api.roles == ["复现/实验结果核对"]
