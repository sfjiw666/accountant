"""账本存储层：基于 aiosqlite 的异步 SQLite 存储。

数据保存在 data/plugin_data/astrbot_plugin_accountant/ledger.db，
遵循 AstrBot 插件开发规范（持久化数据不放在插件自身目录）。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

TYPE_EXPENSE = "expense"
TYPE_INCOME = "income"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL CHECK (type IN ('expense', 'income')),
    amount REAL NOT NULL,
    category TEXT NOT NULL DEFAULT '其他',
    note TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    added_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_user_time ON records(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_records_time ON records(created_at);
"""


def day_window(days: int) -> tuple[float, float]:
    """返回最近 days 天（含今天）的起止时间戳。"""
    days = max(1, min(days, 3650))
    now = datetime.now()
    start = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start.timestamp(), (now.timestamp() + 86400.0)


def prev_window(days: int) -> tuple[float, float]:
    """返回再往前一个同样长度的对比窗口。"""
    start, _ = day_window(days)
    return start - days * 86400.0, start


class LedgerStore:
    """异步账本数据库。所有方法均为协程，异常向上抛出由调用方处理。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("LedgerStore 尚未初始化")
        return self._db

    @staticmethod
    def _where(
        user_id: str | None = None,
        start: float | None = None,
        end: float | None = None,
        rtype: str | None = None,
        category: str | None = None,
    ) -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []
        if user_id:
            conds.append("user_id = ?")
            params.append(user_id)
        if start is not None:
            conds.append("created_at >= ?")
            params.append(start)
        if end is not None:
            conds.append("created_at < ?")
            params.append(end)
        if rtype in (TYPE_EXPENSE, TYPE_INCOME):
            conds.append("type = ?")
            params.append(rtype)
        if category:
            conds.append("category = ?")
            params.append(category)
        sql = (" WHERE " + " AND ".join(conds)) if conds else ""
        return sql, params

    @staticmethod
    def _row_dict(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        d["amount"] = round(d["amount"], 2)
        return d

    # ------------------------------------------------------------------ #
    # 写操作
    # ------------------------------------------------------------------ #

    async def add_record(
        self,
        *,
        user_id: str,
        user_name: str,
        platform: str,
        rtype: str,
        amount: float,
        category: str,
        note: str,
        raw_text: str,
        created_at: float | None = None,
    ) -> int:
        now = time.time()
        cur = await self._conn().execute(
            """
            INSERT INTO records
                (user_id, user_name, platform, type, amount, category,
                 note, raw_text, created_at, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                user_name,
                platform,
                rtype,
                round(amount, 2),
                category,
                note,
                raw_text,
                created_at or now,
                now,
            ),
        )
        await self._conn().commit()
        return int(cur.lastrowid or 0)

    async def delete_record(self, record_id: int, user_id: str | None = None) -> bool:
        """删除一条记录。传入 user_id 时只能删除该用户自己的记录。"""
        if user_id:
            cur = await self._conn().execute(
                "DELETE FROM records WHERE id = ? AND user_id = ?", (record_id, user_id)
            )
        else:
            cur = await self._conn().execute(
                "DELETE FROM records WHERE id = ?", (record_id,)
            )
        await self._conn().commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    # 读操作
    # ------------------------------------------------------------------ #

    async def list_records(
        self,
        user_id: str | None = None,
        start: float | None = None,
        end: float | None = None,
        rtype: str | None = None,
        category: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        where, params = self._where(user_id, start, end, rtype, category)
        cur = await self._conn().execute(f"SELECT COUNT(*) FROM records{where}", params)
        total = (await cur.fetchone())[0]
        cur = await self._conn().execute(
            f"""
            SELECT * FROM records{where}
            ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        )
        rows = [self._row_dict(r) for r in await cur.fetchall()]
        return rows, total

    async def summary(
        self,
        user_id: str | None = None,
        start: float | None = None,
        end: float | None = None,
    ) -> dict[str, float]:
        """返回 {expense, income, count}。"""
        where, params = self._where(user_id, start, end)
        cur = await self._conn().execute(
            f"SELECT type, COUNT(*) AS c, SUM(amount) AS s FROM records{where} GROUP BY type",
            params,
        )
        result: dict[str, float] = {"expense": 0.0, "income": 0.0, "count": 0}
        for row in await cur.fetchall():
            result[row["type"]] = round(row["s"] or 0.0, 2)
            result["count"] += row["c"]
        return result

    async def by_category(
        self,
        user_id: str | None = None,
        start: float | None = None,
        end: float | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        where, params = self._where(user_id, start, end)
        cur = await self._conn().execute(
            f"""
            SELECT type, category, SUM(amount) AS s, COUNT(*) AS c
            FROM records{where}
            GROUP BY type, category ORDER BY s DESC
            """,
            params,
        )
        out: dict[str, list[dict[str, Any]]] = {TYPE_EXPENSE: [], TYPE_INCOME: []}
        for row in await cur.fetchall():
            out.setdefault(row["type"], []).append(
                {
                    "category": row["category"],
                    "total": round(row["s"] or 0.0, 2),
                    "count": row["c"],
                }
            )
        return out

    async def by_day(
        self,
        user_id: str | None = None,
        start: float | None = None,
        end: float | None = None,
    ) -> list[dict[str, Any]]:
        """按天汇总（本地时区），并在缺失的日期填零，方便前端画连续趋势图。"""
        where, params = self._where(user_id, start, end)
        cur = await self._conn().execute(
            f"""
            SELECT date(created_at, 'unixepoch', 'localtime') AS d,
                   SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) AS expense,
                   SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END) AS income
            FROM records{where} GROUP BY d
            """,
            params,
        )
        found = {
            row["d"]: {"expense": row["expense"] or 0.0, "income": row["income"] or 0.0}
            for row in await cur.fetchall()
        }
        days: list[dict[str, Any]] = []
        if start is None or end is None:
            for d, v in sorted(found.items()):
                days.append(
                    {
                        "date": d,
                        "expense": round(v["expense"], 2),
                        "income": round(v["income"], 2),
                    }
                )
            return days
        cur_date = datetime.fromtimestamp(start).date()
        last_date = datetime.fromtimestamp(end - 86400.0).date()
        while cur_date <= last_date:
            key = cur_date.isoformat()
            v = found.get(key, {"expense": 0.0, "income": 0.0})
            days.append(
                {
                    "date": key,
                    "expense": round(v["expense"], 2),
                    "income": round(v["income"], 2),
                }
            )
            cur_date += timedelta(days=1)
        return days

    async def list_users(self) -> list[dict[str, Any]]:
        """所有记过账的用户（用于可视化页面的筛选）。"""
        cur = await self._conn().execute(
            """
            SELECT user_id,
                   (SELECT user_name FROM records r2
                    WHERE r2.user_id = r1.user_id ORDER BY id DESC LIMIT 1) AS user_name,
                   (SELECT platform FROM records r2
                    WHERE r2.user_id = r1.user_id ORDER BY id DESC LIMIT 1) AS platform,
                   COUNT(*) AS cnt,
                   MAX(created_at) AS last_ts,
                   SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) AS expense_total,
                   SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END) AS income_total
            FROM records r1
            GROUP BY user_id ORDER BY last_ts DESC
            """
        )
        out = []
        for row in await cur.fetchall():
            out.append(
                {
                    "user_id": row["user_id"],
                    "user_name": row["user_name"] or row["user_id"],
                    "platform": row["platform"] or "",
                    "count": row["cnt"],
                    "last_ts": row["last_ts"],
                    "expense_total": round(row["expense_total"] or 0.0, 2),
                    "income_total": round(row["income_total"] or 0.0, 2),
                }
            )
        return out
