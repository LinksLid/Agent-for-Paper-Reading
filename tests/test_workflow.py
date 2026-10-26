import json
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
import pytest

from paperteam import workflow
from paperteam.models import Analysis, Claim, Evidence, Plan, ReportDraft, Review, Verdict


PAPER = {"paper_id": "p", "filename": "fixture.pdf", "pages": 1, "github_urls": [], "chunks": [
    {"paper_id": "p", "chunk_id": "p:p1:c0", "page": 1,
     "text": "A system uses specialized agents. The reported benchmark score is 42 percent."}]}


class FakeLLM:
    model = "test-fixture-no-api"
    calls = 0
    bad_first = True

    def structured(self, role, prompt, schema):
        self.calls += 1
        if schema is Plan:
            return Plan(focus="test", method_queries=["specialized agents"], experiment_queries=["benchmark score"], comparison_dimensions=["method"])
        if schema is Analysis:
            quote = "specialized agents" if "方法" in role else "benchmark score is 42 percent"
            if "方法" in role and self.bad_first:
                quote = "this quote does not exist"
                self.bad_first = False
            return Analysis(claims=[Claim(paper_id="p", kind="method" if "方法" in role else "experiment",
                                           text="测试夹具结论", evidence=[Evidence(chunk_id="p:p1:c0", quote=quote)])])
        if schema is Review:
            material = json.loads(prompt.split("\n", 1)[1])
            return Review(verdicts=[Verdict(claim_index=i, supported=True, reason="fixture only") for i in range(len(material))])
        if schema is ReportDraft:
            return ReportDraft(title="测试夹具报告（未调用 API）", comparisons=[], open_questions=["如何验证泛化？"])
        raise AssertionError(schema)


def test_parallel_analysis_repair_and_sqlite_checkpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "ingest", lambda *_: [PAPER])
    client = FakeLLM()
    with sqlite3.connect(":memory:", check_same_thread=False) as connection:
        saver = SqliteSaver(connection)
        graph = workflow.build_workflow(client, tmp_path, tmp_path, saver)
        config = {"configurable": {"thread_id": "test"}}
        result = graph.invoke({"paths": ["fixture.pdf"], "question": "test", "events": []}, config)
        assert len(result["accepted"]) == 2
        assert result["review_round"] == 2
        assert len(result["review_history"][0]["rejected"]) == 1
        assert result["rejected"] == []
        assert "[^S001]" in result["report"] and "PDF p.1" in result["report"]
        assert any(e["node"] == "targeted_repair" for e in result["events"])
        assert graph.get_state(config).next == ()
        assert len(list(graph.get_state_history(config))) > 5


def test_unresolved_claim_excluded_when_repair_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "ingest", lambda *_: [PAPER])
    result = workflow.build_workflow(FakeLLM(), tmp_path, tmp_path, max_repairs=0).invoke(
        {"paths": ["fixture.pdf"], "question": "test", "events": []})
    assert len(result["accepted"]) == 1 and len(result["rejected"]) == 1
    assert "this quote does not exist" not in result["report"]


def test_missing_semantic_verdict_stops_report():
    class Incomplete:
        def structured(self, *args):
            return Review(verdicts=[])
    claim = Claim(paper_id="p", kind="method", text="测试结论", evidence=[Evidence(chunk_id="p:p1:c0", quote="specialized agents")]).model_dump()
    with pytest.raises(RuntimeError, match="遗漏或重复"):
        workflow.semantic_review(Incomplete(), [claim], workflow.all_chunks([PAPER]))


def test_limitation_feedback_returns_to_its_producing_role(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "ingest", lambda *_: [PAPER])

    class LimitationClient(FakeLLM):
        def __init__(self):
            self.bad_first = False
            self.roles = []
            self.experiment_calls = 0

        def structured(self, role, prompt, schema):
            if schema is Analysis:
                self.roles.append(role)
                if role == "实验分析员":
                    self.experiment_calls += 1
                    quote = "missing limitation evidence" if self.experiment_calls == 1 else "benchmark score is 42 percent"
                    return Analysis(claims=[Claim(paper_id="p", kind="limitation", text="测试实验局限",
                                                  evidence=[Evidence(chunk_id="p:p1:c0", quote=quote)])])
            return super().structured(role, prompt, schema)

    llm = LimitationClient()
    result = workflow.build_workflow(llm, tmp_path, tmp_path).invoke(
        {"paths": ["fixture.pdf"], "question": "test", "events": []})
    assert llm.roles.count("方法分析员") == 1
    assert llm.roles.count("实验分析员") == 2
    assert len(result["accepted"]) == 2 and not result["rejected"]
