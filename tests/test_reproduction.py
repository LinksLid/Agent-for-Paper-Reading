import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from paperteam.reproduction import CASES, prepare_database, query, verify_sources, sql_workflow


def test_generated_sql_cannot_write_or_attach(tmp_path):
    db = tmp_path / "test.sqlite"
    prepare_database(db)
    before, error = query(db, "SELECT COUNT(*) FROM students")
    assert not error
    assert query(db, "DELETE FROM students")[1]
    assert query(db, "ATTACH DATABASE ':memory:' AS other")[1]
    assert query(db, "SELECT load_extension('anything')")[1]
    after, error = query(db, "SELECT COUNT(*) FROM students")
    assert after == before and not error


def test_gold_questions_execute_on_both_database_variants(tmp_path):
    outputs = []
    for variant in range(2):
        db = tmp_path / f"v{variant}.sqlite"
        prepare_database(db, variant)
        outputs.append([])
        for case in CASES:
            rows, error = query(db, case["gold"])
            assert not error, case["id"]
            outputs[-1].append(rows)
    assert outputs[0] != outputs[1]


def test_tampered_source_is_rejected(tmp_path):
    path = tmp_path / "source.py"
    path.write_text("original", encoding="utf-8")
    lock = {"files": {"source.py": hashlib.sha256(b"original").hexdigest()}}
    verify_sources(tmp_path, lock)
    path.write_text("modified", encoding="utf-8")
    with pytest.raises(ValueError, match="源码校验失败"):
        verify_sources(tmp_path, lock)


def test_database_initialization_releases_file_handle(tmp_path):
    database = tmp_path / "test.sqlite"
    prepare_database(database)
    database.unlink()
    assert not database.exists()


def test_sql_error_routes_to_refiner_then_executes(tmp_path):
    db = tmp_path / "test.sqlite"
    prepare_database(db)

    class Client:
        def chat(self, role, prompt, **kwargs):
            return "SELECT absent FROM students" if "Decomposer" in role else "SELECT COUNT(*) FROM students"

    templates = {"decompose_template_bird": "{query} {desc_str} {fk_str} {evidence}",
                 "refiner_template": "{sql} {sqlite_error} {query}"}
    result = sql_workflow(Client(), templates, lambda text: text, db).invoke({"question": "Count students"})
    assert result["attempts"] == 1
    assert result["rows"] == [(6,)]
    assert not result["error"]
    assert [x["node"] for x in result["trace"]] == ["decomposer", "execute", "refiner", "execute"]


def test_sql_refinement_stops_at_two_attempts(tmp_path):
    db = tmp_path / "test.sqlite"
    prepare_database(db)

    class Client:
        def chat(self, *args, **kwargs):
            return "SELECT absent FROM students"

    templates = {"decompose_template_bird": "{query}", "refiner_template": "{sqlite_error}"}
    result = sql_workflow(Client(), templates, lambda text: text, db).invoke({"question": "Count students"})
    assert result["attempts"] == 2 and result["error"]
