from pathlib import Path

from paperteam.corpus import evidence_errors, normalize, retrieve


def test_retrieval_keeps_abstract_and_finds_experiment():
    paper = {"chunks": [
        {"chunk_id": "a", "text": "Introduction and abstract"},
        {"chunk_id": "b", "text": "unrelated text about vegetables"},
        {"chunk_id": "c", "text": "experiment benchmark execution accuracy baseline ablation"},
    ]}
    assert [c["chunk_id"] for c in retrieve(paper, ["experiment accuracy baseline"], 2)] == ["a", "c"]


def test_citation_requires_existing_source_exact_quote_and_matching_paper():
    chunks = {"a:p1": {"paper_id": "a", "text": "The execution accuracy is 59.59 percent."}}
    claim = {"paper_id": "a", "kind": "experiment", "text": "作者报告了实验结果。",
             "evidence": [{"chunk_id": "a:p1", "quote": "accuracy is 59.59 percent"}]}
    assert evidence_errors(claim, chunks, {"a"}) == []
    claim["evidence"][0]["quote"] = "accuracy is 99.99 percent"
    assert "quote 不是原文连续片段" in evidence_errors(claim, chunks)
    claim["evidence"][0] = {"chunk_id": "missing", "quote": "invented evidence"}
    assert "不存在的引用块" in evidence_errors(claim, chunks)
    claim["evidence"][0] = {"chunk_id": "a:p1", "quote": "execution accuracy"}
    claim["paper_id"] = "b"
    assert "引用属于另一篇论文" in evidence_errors(claim, chunks)


def test_normalization_handles_pdf_linebreaks_without_changing_words():
    assert normalize("score\n is   42") == "score is 42"
    assert normalize("4 2") != normalize("42")


def test_retrieval_gap_is_not_accepted_as_a_paper_fact():
    claim = {"paper_id": "a", "kind": "limitation", "text": "所给材料中没有另一篇论文，无法比较。",
             "evidence": [{"chunk_id": "a", "quote": "paper title"}]}
    errors = evidence_errors(claim, {"a": {"paper_id": "a", "text": "paper title"}})
    assert any("证据缺口" in error for error in errors)
