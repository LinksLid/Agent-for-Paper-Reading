"""Pinned MAC-SQL method-level reproduction, not a BIRD/Spider benchmark claim.

Use the authors' prompt strings, SQL parser, and EX scorer from verified sources.
The runner replaces old Azure API plumbing, uses a tiny local dataset, and skips
schema pruning on the small schema. Arbitrary model-generated shell is never run.
"""
import ast
from collections import Counter
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import time
from types import SimpleNamespace
from typing import TypedDict
import zipfile

import httpx
from langgraph.graph import StateGraph, START, END

from .llm import LLM
from .models import ReproductionPlan

SQL_SYSTEM = "You are a SQLite text-to-SQL assistant. Follow the supplied task. Return final SQL in a ```sql code block."
SCHEMA = """# Table: departments
[(id, department id. Primary key.), (name, department name. Values: ['CS', 'Math', 'History'].),
 (budget, annual budget.)]
# Table: students
[(id, student id. Primary key.), (name, student name.), (dept_id, department id.), (score, exam score.)]"""
FK = "students.dept_id = departments.id"
CASES = [
    {"id": "count", "question": "How many students have a score greater than 80?",
     "gold": "SELECT COUNT(*) FROM students WHERE score > 80"},
    {"id": "join", "question": "What are the names of students in the CS department?",
     "gold": "SELECT s.name FROM students s JOIN departments d ON s.dept_id=d.id WHERE d.name='CS'"},
    {"id": "group", "question": "List each department name and its average student score, excluding departments without students.",
     "gold": "SELECT d.name, AVG(s.score) FROM departments d JOIN students s ON d.id=s.dept_id GROUP BY d.id,d.name"},
    {"id": "nested", "question": "Which student names have a score higher than the average score of all students?",
     "gold": "SELECT name FROM students WHERE score > (SELECT AVG(score) FROM students)"},
    {"id": "empty_department", "question": "Which department names have no students?",
     "gold": "SELECT d.name FROM departments d LEFT JOIN students s ON d.id=s.dept_id WHERE s.id IS NULL"},
    {"id": "having", "question": "List department names that have at least two students with scores of 80 or above.",
     "gold": "SELECT d.name FROM departments d JOIN students s ON d.id=s.dept_id WHERE s.score >=80 GROUP BY d.id,d.name HAVING COUNT(*)>=2"},
]


def dump(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def verify_sources(repo: Path, lock: dict):
    for relative, digest in lock["files"].items():
        path = repo / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"MAC-SQL 源码校验失败：{relative}；请恢复锁定提交。")


def checkout(root: Path) -> tuple[Path, dict]:
    lock = json.loads((root / "config/macsql.lock.json").read_text(encoding="utf-8"))
    repo = root / "external/MAC-SQL"
    if repo.exists():
        verify_sources(repo, lock)
        return repo, lock
    repo.parent.mkdir(parents=True, exist_ok=True)
    # The commit archive is deterministic and works when Git transport is blocked.
    url = f"https://codeload.github.com/wbbeyourself/MAC-SQL/zip/{lock['commit']}"
    raw = bytearray()
    with httpx.Client(timeout=60, trust_env=False, follow_redirects=True) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            for block in response.iter_bytes():
                raw.extend(block)
                if len(raw) > 60_000_000:
                    raise ValueError("仓库压缩包超出大小上限。")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 200_000_000:
            raise ValueError("仓库解压大小超出上限。")
        # Validate every member before writing anything.
        members = []
        for item in archive.infolist():
            parts = PurePosixPath(item.filename).parts[1:]
            if not parts:
                continue
            target = repo.joinpath(*parts).resolve()
            if not target.is_relative_to(repo.resolve()) or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("拒绝不安全的仓库压缩包成员。")
            members.append((item, target))
        for item, target in members:
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))
    verify_sources(repo, lock)
    dump(repo / ".paperteam-source.json", {**lock, "transport": "github_commit_archive"})
    return repo, lock


def original_components(repo: Path, lock: dict):
    verify_sources(repo, lock)
    templates = {}
    for node in ast.parse((repo / "core/const.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    templates[target.id] = node.value.value
    # Execute only the two reviewed functions, never repository module imports.
    connections = []
    def tracked_connect(path):
        connection = readonly_connect(path)
        connections.append(connection)
        return connection
    namespace = {"re": re, "sqlite3": SimpleNamespace(connect=tracked_connect)}
    for relative, name in [("core/utils.py", "parse_sql_from_string"),
                           ("evaluation/evaluation_bird_ex.py", "execute_sql")]:
        tree = ast.parse((repo / relative).read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(repo / relative), "exec"), namespace)
    def score(*args):
        try:
            return namespace["execute_sql"](*args)
        finally:
            while connections:
                connections.pop().close()
    return templates, namespace["parse_sql_from_string"], score


def readonly_connect(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    started = time.monotonic()
    conn.set_progress_handler(lambda: int(time.monotonic() - started > 3), 1000)
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
    conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
    return conn


def query(path: Path, sql: str) -> tuple[list, str]:
    try:
        with closing(readonly_connect(path)) as conn:
            return conn.execute(sql).fetchmany(1000), ""
    except sqlite3.Error as error:
        return [], str(error)


def prepare_database(path: Path, variant: int = 0):
    if path.exists():
        return
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""CREATE TABLE departments(id INTEGER PRIMARY KEY, name TEXT, budget INTEGER);
        CREATE TABLE students(id INTEGER PRIMARY KEY, name TEXT, dept_id INTEGER, score REAL);
        INSERT INTO departments VALUES(1,'CS',120),(2,'Math',100),(3,'History',60);
        INSERT INTO students VALUES(1,'Alice',1,92),(2,'Bob',1,78),(3,'Carol',2,85),
        (4,'Dave',2,85),(5,'Eve',1,60),(6,'Frank',2,70);""")
        if variant:
            conn.executescript("UPDATE students SET score=score-12 WHERE dept_id=2; INSERT INTO students VALUES(7,'Grace',3,96);")
        conn.commit()


class SQLState(TypedDict, total=False):
    question: str
    sql: str
    error: str
    rows: list
    attempts: int
    trace: list


def sql_workflow(llm, templates, parser, database: Path):
    def generate(state):
        prompt = templates["decompose_template_bird"].format(
            query=state["question"], desc_str=SCHEMA, fk_str=FK, evidence="None")
        sql = parser(llm.chat("复现/Decomposer", prompt, max_tokens=2200, system=SQL_SYSTEM))
        return {"sql": sql, "attempts": 0, "trace": [{"node": "decomposer", "sql": sql}]}

    def execute(state):
        rows, error = query(database, state["sql"])
        return {"rows": rows, "error": error, "trace": state["trace"] + [{"node": "execute", "error": error}]}

    def refine(state):
        prompt = templates["refiner_template"].format(
            query=state["question"], evidence="None", desc_str=SCHEMA, fk_str=FK,
            sql=state["sql"], sqlite_error=state["error"], exception_class="sqlite3.Error")
        sql = parser(llm.chat("复现/Refiner", prompt, max_tokens=1600, system=SQL_SYSTEM))
        return {"sql": sql, "attempts": state["attempts"] + 1,
                "trace": state["trace"] + [{"node": "refiner", "sql": sql}]}

    graph = StateGraph(SQLState)
    graph.add_node("decomposer", generate)
    graph.add_node("execute", execute)
    graph.add_node("refiner", refine)
    graph.add_edge(START, "decomposer")
    graph.add_edge("decomposer", "execute")
    graph.add_conditional_edges("execute", lambda s: "refiner" if s["error"] and s["attempts"] < 2 else END)
    graph.add_edge("refiner", "execute")
    return graph.compile()


def run_macsql(llm: LLM, root: Path, run_dir: Path, samples: int = 3, plan_agent: bool = True) -> dict:
    if not 1 <= samples <= len(CASES):
        raise ValueError(f"samples 应在 1 到 {len(CASES)} 之间。")
    run_dir.mkdir(parents=True, exist_ok=True)
    repo, lock = checkout(root)
    templates, parser, scorer = original_components(repo, lock)
    readme = (repo / "README.md").read_text(encoding="utf-8")
    if plan_agent:
        plan = llm.structured("实验复现员", "根据 README 评估已确定的方案：复用官方提示词、SQL 解析和 EX 评估函数，"
                              "在自建 SQLite 数据上进行 API 推理；不下载模型或完整数据集，不复现论文榜单。"
                              "feasible 表示该小规模方案可否实施，列出与原论文差异。\n" + readme, ReproductionPlan)
        if not plan.feasible:
            result = {"status": "blocked", "plan": plan.model_dump(), "source": lock}
            dump(run_dir / "reproduction.json", result)
            return result
    else:
        plan = ReproductionPlan(feasible=True, reason="使用已实现的固定适配器", scope="MAC-SQL 方法级微型验证",
                                limitations=["自建数据；非 BIRD/Spider；模型与论文不同；小模式不启用 Selector"])
    databases = [run_dir / "toy.sqlite", run_dir / "toy_variant.sqlite"]
    for i, database in enumerate(databases):
        prepare_database(database, i)
    workflow = sql_workflow(llm, templates, parser, databases[0])
    (run_dir / "reproduction_graph.mmd").write_text(workflow.get_graph().draw_mermaid(), encoding="utf-8")
    records = []
    started = time.monotonic()
    first_call = llm.calls
    for case in CASES[:samples]:
        # The same schema and question go to both configurations; gold is evaluation-only.
        baseline_prompt = templates["baseline_template"].format(desc_str=SCHEMA, query=case["question"], evidence="None")
        baseline = parser(llm.chat("复现/单次基线", baseline_prompt, max_tokens=1400, system=SQL_SYSTEM))
        baseline_model = llm.model
        multi = workflow.invoke({"question": case["question"]})
        record = {"id": case["id"], "question": case["question"], "gold_sql": case["gold"],
                  "baseline_sql": baseline, "multi_sql": multi["sql"], "trace": multi["trace"],
                  "refinements": multi["attempts"], "baseline_model": baseline_model, "multi_model": llm.model}
        for label, sql in [("baseline", baseline), ("multi", multi["sql"])]:
            scores, strict_scores, errors = [], [], []
            for database in databases:
                predicted_rows, error = query(database, sql)
                gold_rows, gold_error = query(database, case["gold"])
                if gold_error:
                    raise RuntimeError("实验参考 SQL 执行失败。")
                scores.append(0 if error else scorer(sql, case["gold"], str(database)))
                strict_scores.append(int(not error and Counter(predicted_rows) == Counter(gold_rows)))
                errors.append(error)
            record[label + "_ex"] = int(all(scores))
            record[label + "_multiset_ex"] = int(all(strict_scores))
            record[label + "_errors"] = errors
        records.append(record)
        dump(run_dir / "cases.json", records)
    result = {"status": "completed", "scope": "method_microbenchmark", "full_paper_reproduced": False,
              "source": lock, "plan": plan.model_dump(), "model": llm.model, "samples": samples,
              "database_variants": len(databases), "dataset": "PaperTeam synthetic university database v1",
              "selector": "bypassed_small_schema", "api_calls": llm.calls - first_call,
              "baseline_ex": sum(r["baseline_ex"] for r in records) / samples,
              "multi_ex": sum(r["multi_ex"] for r in records) / samples,
              "baseline_multiset_ex": sum(r["baseline_multiset_ex"] for r in records) / samples,
              "multi_multiset_ex": sum(r["multi_multiset_ex"] for r in records) / samples,
              "seconds": round(time.monotonic() - started, 3), "cases": records,
              "limitations": ["不是 BIRD/Spider 正式基准，不能与论文 EX 数字比较。",
                              "使用当前 API 模型，未使用论文 GPT-4 或 SQL-Llama。",
                              "重用官方提示词、SQL 解析及 EX 函数；编排和 API 接口由本项目适配。",
                              "小型模式跳过 Selector，仅在 SQL 执行报错时触发 Refiner。",
                              "基线与多步方法的调用次数及提示长度不同，不构成等预算优劣证明。",
                              "官方 EX 使用结果集合；额外记录多重集比较；仍未覆盖排序语义。"]}
    dump(run_dir / "reproduction.json", result)
    return result
