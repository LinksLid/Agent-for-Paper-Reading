from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_ui_renders_without_api_call():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=20)
    assert not app.exception
    assert app.title[0].value == "PaperTeam"
    assert app.button[0].label == "开始协作阅读"
    backend = next(select for select in app.selectbox if select.label == "复现实验规划与修复")
    backend.select("Codex CLI").run(timeout=20)
    assert not app.exception
    assert any("不计入 DeepSeek" in caption.value for caption in app.caption)
