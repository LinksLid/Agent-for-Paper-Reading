from dataclasses import dataclass, field, replace
from pathlib import Path
import os
from urllib.parse import urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""
    max_calls: int = 80
    timeout: float = 120
    provider: str = "compatible"
    fallback: "Settings | None" = field(default=None, repr=False)
    deepseek_budget_cny: float = 20.0

    @classmethod
    def load(cls, root: Path = ROOT, model: str = "", provider: str = "auto"):
        if provider not in {"auto", "deepseek", "tongji"}:
            raise ValueError("provider 必须是 auto、deepseek 或 tongji。")
        school = cls.from_file(root / ".env", root, model if provider == "tongji" else "", "tongji")
        if provider == "tongji" or (provider == "auto" and not (root / "deepseek.env").exists()):
            return replace(school, model=model or school.model)
        primary = cls.from_file(root / "deepseek.env", root, model, "deepseek")
        if urlsplit(primary.base_url).hostname != "api.deepseek.com":
            raise ValueError("预算模式仅支持 DeepSeek 官方 api.deepseek.com 端点。")
        return replace(primary, fallback=school)

    @classmethod
    def from_file(cls, path: Path, root: Path, model: str = "", provider: str = "compatible"):
        values = {**dotenv_values(path), **os.environ}
        base = (values.get("OPENAI_BASE_URL") or values.get("BASE_URL")
                or values.get("Base_url") or "").strip().rstrip("/")
        key = values.get("OPENAI_API_KEY") or values.get("API_KEY") or ""
        if not base or not key:
            raise ValueError(f"请在 {path.name} 配置 Base_url 和 API_KEY。")
        parts = urlsplit(base)
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username:
            raise ValueError("Base_url 必须是有效的 HTTP(S) API 地址。")
        if not parts.path.strip("/"):
            base += "/v1"
        budget = min(float(values.get("DEEPSEEK_BUDGET_CNY", 20)), 20.0)
        if budget <= 0:
            raise ValueError("DEEPSEEK_BUDGET_CNY 必须大于零，且最大为 20。")
        return cls(root, base, key, model or values.get("MODEL") or values.get("OPENAI_MODEL") or "",
                   int(values.get("MAX_API_CALLS", 80)), float(values.get("API_TIMEOUT", 120)), provider,
                   deepseek_budget_cny=budget)
