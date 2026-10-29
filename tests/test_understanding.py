import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from paperteam import runner, workflow
from paperteam.config import Settings
from paperteam.understanding import (PaperExplanation, ExplanationReview, explanation_claims,
    explanation_markdown, generate_explanations, method_dot, reading_context, section_coverage,
    verify_explanation, restore_extracted_quotes)
from paperteam.reading_view import source_records, source_page


QUOTE = 'A parser selects a schema and then generates a query.'
PAPER = {'paper_id': 'p', 'filename': 'fixture.pdf', 'pages': 1,
         'github_urls': [], 'chunks': [{'paper_id': 'p', 'chunk_id': 'p:p1:c0', 'page': 1, 'text': QUOTE}]}


@pytest.fixture
def draft():
    fact = {'text': '根据自然语言生成查询', 'evidence': [{'chunk_id': 'p:p1:c0', 'quote': QUOTE}]}
    step = {'step_id': 'S1', 'name': '选择结构', 'phase': 'inference', 'receives': '问题和表结构',
            'does': '选择需要的数据表', 'produces': '筛选后的表结构', 'evidence': fact['evidence']}
    data = {k: copy.deepcopy(fact) for k in ('task', 'inputs', 'outputs', 'prior_approach', 'bottleneck')}
    data.update(steps=[step, {**step, 'step_id': 'S2', 'name': '生成查询'}],
                links=[{'source': 'S1', 'target': 'S2', 'relation': '传递筛选结构', 'evidence': fact['evidence']}],
                improvements=[{'name': '结构筛选', 'before': fact, 'after': fact}],
                reported_results=[fact], limitations=[fact],
                glossary=[{'term': 'Schema', 'meaning': fact}],
                teaching_example={'input': '假设要查询学生数量', 'walkthrough': ['筛选学生表', '生成计数查询'], 'output': '返回计数查询语句'})
    return PaperExplanation.model_validate(data)


class ReadabilityLLM:
    def structured(self, role, prompt, schema, **kwargs):
        assert schema is ExplanationReview
        return ExplanationReview(readable=True, example_consistent=True)


def accept_all(llm, claims, papers, semantic):
    return claims, []


def ready_guide(draft):
    return verify_explanation(ReadabilityLLM(), draft, PAPER, accept_all)


def test_invalid_steps_and_edges_rejected(draft):
    data = draft.model_dump()
    data['steps'][1]['step_id'] = 'S1'
    with pytest.raises(ValidationError, match='重复'):
        PaperExplanation.model_validate(data)
    data = draft.model_dump()
    data['links'][0]['target'] = 'S99'
    with pytest.raises(ValidationError, match='真实步骤'):
        PaperExplanation.model_validate(data)


def test_unverified_steps_invalidate_edges_and_fictional_example(draft):
    def reject_step(llm, claims, papers, semantic):
        bad = [c for c in claims if c['guide_key'] == 'steps.S2']
        return [c for c in claims if c not in bad], [{'claim': c, 'reason': '无原文支持'} for c in bad]
    guide = verify_explanation(ReadabilityLLM(), draft, PAPER, reject_step)
    assert not guide['links'][0]['verified']
    assert not guide['teaching_example']['verified']
    assert not method_dot(guide)
    report = explanation_markdown(guide)
    assert 'S2 · 生成查询' not in report and '假设要查询学生数量' not in report
    assert '方法步骤' in guide['coverage']['missing']


def test_exact_quote_gate_hides_wrong_facts_and_before_after(draft):
    data = draft.model_dump()
    data['task'] = {'text': '不应展示的任务', 'evidence': [{'chunk_id': 'p:p1:c0', 'quote': 'a fabricated quote'}]}
    data['improvements'][0]['before'] = {'text': '不应展示的旧做法', 'evidence': [{'chunk_id': 'other:p1:c0', 'quote': QUOTE}]}
    def local_checker(llm, claims, papers, semantic):
        return workflow.check_claims(llm, claims, papers, semantic=False)
    guide = verify_explanation(ReadabilityLLM(), PaperExplanation.model_validate(data), PAPER, local_checker)
    report = explanation_markdown(guide)
    assert '不应展示' not in report
    assert '任务' in guide['coverage']['missing'] and '具体改进对照' in guide['coverage']['missing']
    assert not any(r['label'].startswith('改进') or r['label'] == '研究任务' for r in source_records(guide))


def test_every_displayed_step_and_arrow_gets_semantic_review(draft):
    guide = ready_guide(draft)
    claims, _ = explanation_claims(copy.deepcopy(guide), 'p')
    by_key = {c['guide_key']: c for c in claims}
    assert all(text in by_key['steps.S1']['text'] for text in ('推理/使用', '选择结构', '问题和表结构', '选择需要的数据表', '筛选后的表结构'))
    assert '选择结构到生成查询' in by_key['links.0']['text']
    assert any(r['label'] == '结论边界 1' for r in source_records(guide))
    report = explanation_markdown(guide, refs=lambda e: '[source]')
    assert '教学推演：不是论文原例，也未实际执行' in report
    assert '流程连接依据：[source]' in report
    assert guide['coverage']['ready'] == 8


def test_graph_escapes_labels_and_requires_both_endpoints(draft):
    guide = ready_guide(draft)
    guide['steps'][0]['name'] = '模块"; <x>\\test'
    diagram = method_dot(guide)
    assert 'S1 -> S2' in diagram and 'cluster_inference' in diagram
    assert '\\"' in diagram and '\\\\test' in diagram
    guide['steps'][0]['verified'] = False
    assert method_dot(guide) == ''


def test_example_consistency_is_independent_of_fact_acceptance(draft):
    class BadExample(ReadabilityLLM):
        def structured(self, *args, **kwargs):
            return ExplanationReview(readable=True, example_consistent=False, issues=['输出不是查询语句'])
    guide = verify_explanation(BadExample(), draft, PAPER, accept_all)
    assert guide['audit']['accepted_facts'] > 0
    assert '假设要查询学生数量' not in explanation_markdown(guide)
    assert guide['status'] == 'partial'


def test_explanation_repairs_with_feedback_and_bounded_attempts(draft, tmp_path):
    class DraftLLM(ReadabilityLLM):
        prompts = []
        def structured(self, role, prompt, schema, **kwargs):
            if schema is PaperExplanation:
                self.prompts.append(prompt)
                return draft
            return super().structured(role, prompt, schema, **kwargs)
    calls = []
    def checker(llm, claims, papers, semantic):
        calls.append(1)
        bad = [c for c in claims if c['guide_key'] == 'task'] if len(calls) == 1 else []
        return [c for c in claims if c not in bad], [{'claim': c, 'reason': '请核实任务输入'} for c in bad]
    client = DraftLLM()
    guides = generate_explanations(client, [PAPER], '解释任务', checker=checker, output=tmp_path)
    assert len(client.prompts) == 2 and '请核实任务输入' in client.prompts[1]
    assert guides[0]['status'] == 'ready' and len(guides[0]['history']) == 2
    assert (tmp_path / 'p/guide-attempt-1.json').is_file()


def test_question_specific_retrieval_includes_method_and_experiments():
    paper = copy.deepcopy(PAPER)
    texts = ['Abstract task input output', 'Introduction problem',
             'framework overview architecture method algorithm pipeline training inference stages components',
             'previous methods limitations contributions unlike existing approaches we propose improve novel contribution',
             'main results baseline ablation benchmark dataset evaluation metric limitations discussion comparison experimental setup']
    paper['chunks'] = [{'paper_id': 'p', 'chunk_id': f'p:p{i}:c0', 'page': i, 'text': text}
                       for i, text in enumerate(texts, 1)]
    context = reading_context(paper)
    assert context['pages_included'] == [1, 2, 3, 4, 5]
    assert {'opening', 'method', 'experiments', 'improvements'} <= {q for c in context['chunks'] for q in c['reading_questions']}
    assert sum(len(c['text']) for c in context['chunks']) <= 52000


def test_saved_upgrade_preserves_experiments_and_rejects_changed_pdf(draft, tmp_path, monkeypatch):
    folder = tmp_path / 'output/saved'
    folder.mkdir(parents=True)
    pdf = tmp_path / 'source.pdf'
    pdf.write_bytes(b'original pdf bytes')
    paper = {**PAPER, 'path': str(pdf), 'sha256': hashlib.sha256(pdf.read_bytes()).hexdigest()}
    other = {**paper, 'paper_id': 'q', 'filename': 'other.pdf',
             'chunks': [{**PAPER['chunks'][0], 'paper_id': 'q', 'chunk_id': 'q:p1:c0'}]}
    other_guide = json.loads(json.dumps(ready_guide(draft)).replace('p:p1:c0', 'q:p1:c0'))
    other_guide.update(paper_id='q', filename='other.pdf')
    reproduction = {'status': 'completed', 'samples': 3, 'baseline_ex': .5, 'multi_ex': .75, 'database_variants': 1, 'source': {'commit': 'abc'}, 'limitations': []}
    state = {'question': 'test', 'papers': [paper, other], 'accepted': [], 'reproduction': reproduction, 'paper_guides': [other_guide]}
    manifest = {'api_calls': 8, 'total_tokens': 90, 'models_used': []}
    for name, data in [('state.json', state), ('manifest.json', manifest)]:
        (folder / name).write_text(json.dumps(data), encoding='utf-8')
    (folder / 'report.md').write_text('old report', encoding='utf-8')
    class Client:
        model = 'fixture'
        calls = 2
        records = [{'usage': {'total_tokens': 30}}]
        models_used = [{'provider': 'fixture', 'model': 'fixture'}]
        switches = []
        def __init__(self, *args): pass
        def close(self): pass
    monkeypatch.setattr(runner, 'LLM', Client)
    def generate(client, papers, *a, **kw):
        assert [p['paper_id'] for p in papers] == ['p']
        return [ready_guide(draft)]
    monkeypatch.setattr(runner, 'generate_explanations', generate)
    runner.explain_saved(Settings(root=tmp_path), folder, paper_ids=['p'])
    saved = json.loads((folder / 'state.json').read_text(encoding='utf-8'))
    assert saved['reproduction'] == reproduction
    assert saved['paper_guides'][1] == other_guide
    assert (folder / 'report.before-explanations.md').read_text(encoding='utf-8') == 'old report'
    assert json.loads((folder / 'manifest.json').read_text())['api_calls'] == 10
    pdf.write_bytes(b'changed')
    with pytest.raises(ValueError, match='内容发生变化'):
        runner.explain_saved(Settings(root=tmp_path), folder)


def test_page_preview_rejects_pdf_mismatch_even_with_cached_image(tmp_path):
    pdf = tmp_path / 'test.pdf'
    pdf.write_bytes(b'changed')
    paper = {**PAPER, 'path': str(pdf), 'sha256': 'a' * 64}
    with pytest.raises(ValueError, match='内容与当前讲解不一致'):
        source_page(paper, 1, tmp_path)
    with pytest.raises(ValueError, match='越界'):
        source_page(paper, 2, tmp_path)


def test_literal_quote_restoration_handles_pdf_wrapping_but_not_word_changes():
    paper = copy.deepcopy(PAPER)
    paper['chunks'][0]['text'] = 'The system uses a se- lection algorithm. It does not run now here.'
    claims = [{'paper_id': 'p', 'guide_key': 'task', 'evidence': [{'chunk_id': 'p:p1:c0', 'quote': quote}]}
              for quote in ('the system uses a selection algorithm.', 'It does not run nowhere.', 'The system uses a selection model.')]
    restored = restore_extracted_quotes(claims, paper)
    assert len(restored) == 1
    assert claims[0]['evidence'][0]['quote'] == 'The system uses a se- lection algorithm.'
    assert claims[1]['evidence'][0]['quote'] == 'It does not run nowhere.'


def test_explanation_graph_runs_after_verifier_without_rerunning_experiments(draft, tmp_path, monkeypatch):
    from tests.test_workflow import FakeLLM
    monkeypatch.setattr(workflow, 'ingest', lambda *a: [PAPER])
    monkeypatch.setattr(workflow, 'generate_explanations', lambda *a, **kw: [ready_guide(draft)])
    client = FakeLLM()
    client.bad_first = False
    # This fixture's source differs from FakeLLM quotes; skip repairs so routing
    # and the new report integration can be tested independently of extraction.
    result = workflow.build_workflow(client, tmp_path, tmp_path, max_repairs=0, explain=True).invoke(
        {'paths': ['fixture.pdf'], 'question': 'test', 'events': []})
    order = [e['node'] for e in result['events']]
    assert order.index('verifier') < order.index('paper_explainer') < order.index('reproducer') < order.index('reporter')
    assert result['reproduction']['status'] == 'disabled'
    assert '输入怎样变成输出' in result['report']
