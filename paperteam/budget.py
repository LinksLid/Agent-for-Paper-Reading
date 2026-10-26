"""Persistent conservative CNY reservations shared across local processes.

Pricing snapshot: official DeepSeek CNY peak rates, 2026-10-26. Input bound uses
UTF-8 byte count plus protocol overhead, not a heuristic word/token ratio.
Actual account balance remains a second independent guard.
"""
from contextlib import closing
import hashlib
import sqlite3

from .config import Settings

RATES = {"deepseek-flash": (2.0, 8.0), "deepseek-chat": (2.0, 8.0),
         "deepseek-v4-flash": (2.0, 8.0), "deepseek-v4-pro": (9.0, 27.0)}


class BudgetExhausted(Exception):
    pass


class Budget:
    def __init__(self, settings: Settings):
        self.path = settings.root / ".cache/deepseek-budget.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.account = hashlib.sha256(settings.api_key.encode()).hexdigest()
        self.limit = settings.deepseek_budget_cny
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS accounts (account TEXT PRIMARY KEY, initial REAL, spent REAL, exhausted INTEGER DEFAULT 0)")
            conn.commit()

    def reserve(self, balance: float, model: str, input_bytes: int, max_tokens: int) -> float:
        if model not in RATES:
            raise BudgetExhausted("未配置该模型的预算费率")
        input_rate, output_rate = RATES[model]
        # A small margin accommodates protocol tokens and price rounding.
        reserve = ((input_bytes + 1024) * input_rate + max_tokens * output_rate) / 1_000_000 + .01
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO accounts(account,initial,spent) VALUES(?,?,0)", (self.account, balance))
            initial, spent, exhausted = conn.execute("SELECT initial,spent,exhausted FROM accounts WHERE account=?", (self.account,)).fetchone()
            # Account-wide balance drops count conservatively even if another client caused them.
            consumed = max(spent, initial - balance)
            if exhausted or consumed + reserve > self.limit or balance < reserve:
                conn.commit()
                raise BudgetExhausted("余额或项目 20 元预算不足以覆盖下一次请求")
            conn.execute("UPDATE accounts SET spent=? WHERE account=?", (consumed + reserve, self.account))
            conn.commit()
        return reserve

    def settle(self, reserve: float, usage: dict | None, model: str):
        # Unknown usage keeps the entire reservation (timeouts may still be billed).
        if usage is None:
            return
        input_rate, output_rate = RATES[model]
        charged_bound = (usage.get("prompt_tokens", 0) * input_rate + usage.get("completion_tokens", 0) * output_rate) / 1_000_000
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            conn.execute("UPDATE accounts SET spent=MAX(0,spent-?+?) WHERE account=?", (reserve, charged_bound, self.account))
            conn.commit()

    def exhaust(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("UPDATE accounts SET exhausted=1 WHERE account=?", (self.account,))
            conn.commit()

    def summary(self):
        with closing(sqlite3.connect(self.path)) as conn:
            row = conn.execute("SELECT initial,spent,exhausted FROM accounts WHERE account=?", (self.account,)).fetchone()
        return {"limit_cny": self.limit, "reserved_or_estimated_cny": round(row[1], 6) if row else 0,
                "exhausted": bool(row[2]) if row else False}
