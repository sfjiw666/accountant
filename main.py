"""AstrBot 记账插件：自然语言记账 + 幽默点评 + WebUI 可视化账本。

- 直接发送「午饭花了35」「发工资了8000块」即可记账，无需指令；
- 记账后机器人会用幽默（可调毒舌程度）的语言点评这笔开销或收入；
- 支持任意消息平台（只用 AstrBot 平台无关 API）；
- WebUI 插件详情页提供可视化账本（统计图表 + 明细管理）。
"""

from __future__ import annotations

import csv
import io
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, register
from astrbot.api.web import error_response, file_response, json_response, request
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from . import commenter
from .ledger import LedgerStore, day_window, prev_window
from .parser import (
    TYPE_EXPENSE,
    TYPE_INCOME,
    VALID_EXPENSE_CATEGORIES,
    VALID_INCOME_CATEGORIES,
    ParsedTransaction,
    build_parse_prompt,
    infer_category,
    parse,
    parse_llm_response,
    plausible_for_llm,
)

PLUGIN_NAME = "astrbot_plugin_accountant"

_HELP_TEXT = """📖 AI 记账管家 · 使用指南

💡 最简单的用法——直接说话：
  「午饭花了35」「打车 20.5元」「昨天买奶茶 22」
  「发工资了 8000块」「收到红包50」

指令方式：
  /book stats [天数]  查看账单统计（默认 7 天）
  /book list [天数]   查看最近明细（含序号）
  /book del <序号>    删除一条自己的记录
  /book add <金额> [收入] [备注]  手动记账
  /book help          查看本帮助

📊 网页版账本：WebUI → 插件 → AI 记账管家 → 可视化账本"""

# 判定为指令的消息前缀（避免劫持其他插件的指令）
_COMMAND_PREFIXES = ("/", "!", "#", "！", "＃", "\\")


def _fmt_money(symbol: str, value: float) -> str:
    return f"{symbol}{value:,.2f}"


@register(
    PLUGIN_NAME,
    "sfjiw",
    "自然语言记账 + 幽默点评 + WebUI 可视化账本",
    "1.0.0",
)
class AccountantPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.store = LedgerStore(
            Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME / "ledger.db"
        )
        # 极短时间内同一用户重复发送相同消息的去重缓存
        self._recent: dict[str, float] = {}

        # ---- WebUI 可视化页面后端 API ----
        context.register_web_api(
            f"/{PLUGIN_NAME}/users", self.api_users, ["GET"], "List ledger users"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/stats", self.api_stats, ["GET"], "Ledger statistics"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/records", self.api_records, ["GET"], "List ledger records"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/records/<record_id>/delete",
            self.api_record_delete,
            ["POST"],
            "Delete a ledger record",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/export", self.api_export, ["GET"], "Export ledger"
        )

    async def initialize(self):
        await self.store.init()
        logger.info(f"[{PLUGIN_NAME}] 账本已就绪：{self.store.db_path}")

    async def terminate(self):
        await self.store.close()

    # ================================================================= #
    # 自然语言记账（核心入口）
    # ================================================================= #

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_natural_language(self, event: AstrMessageEvent):
        """自然语言记账：无需指令，直接发送「午饭花了35」这类消息即可记账。"""
        if not self.config.get("nl_enabled", True):
            return
        text = (event.message_str or "").strip()
        if not text or len(text) > int(self.config.get("nl_max_length", 60)):
            return
        if text.startswith(_COMMAND_PREFIXES):
            return

        parsed = parse(text)

        # 本地规则失败时，交给大模型兜底
        if parsed is None and self.config.get("llm_parse_enabled", True):
            in_group = bool(event.get_group_id())
            allow_llm = (not in_group) or self.config.get("llm_parse_in_group", False)
            if allow_llm and plausible_for_llm(text):
                parsed = await self._llm_parse(event, text)

        if parsed is None:
            return

        # 8 秒内完全相同的消息不重复记账（平台重试/手抖连发）
        key = f"{event.get_sender_id()}|{text}"
        now = time.time()
        if now - self._recent.get(key, 0.0) < 8:
            return
        self._recent[key] = now
        if len(self._recent) > 500:
            self._recent = {k: v for k, v in self._recent.items() if now - v < 60}

        try:
            record_id = await self._save_record(parsed, event)
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] 记账失败: {e}")
            return
        logger.info(
            f"[{PLUGIN_NAME}] 用户 {event.get_sender_name()} 记账 #{record_id}: "
            f"{parsed.type} {parsed.amount} [{parsed.category}] {parsed.note}"
        )

        # 群聊可选择静默记账
        if event.get_group_id() and not self.config.get("reply_in_group", True):
            event.stop_event()
            return

        reply = await self._build_reply(parsed, event)
        yield event.plain_result(reply)
        # 已确认是记账消息，停止传播，避免 LLM 再重复回复
        event.stop_event()

    async def _llm_parse(
        self, event: AstrMessageEvent, text: str
    ) -> ParsedTransaction | None:
        """让大模型从消息中提取账目，失败或未配置模型时返回 None。"""
        try:
            provider_id = await self.context.get_current_chat_provider_id(
                umo=event.unified_msg_origin
            )
            if not provider_id:
                return None
            resp = await self.context.llm_generate(
                chat_provider_id=provider_id, prompt=build_parse_prompt(text)
            )
            parsed = parse_llm_response(resp.completion_text or "")
            if parsed is not None:
                parsed.raw = text
            return parsed
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] LLM 解析记账消息失败: {e}")
            return None

    # ================================================================= #
    # 记录与回复
    # ================================================================= #

    async def _save_record(
        self, parsed: ParsedTransaction, event: AstrMessageEvent
    ) -> int:
        created_at = datetime.now()
        if parsed.days_ago:
            created_at -= timedelta(days=parsed.days_ago)
        return await self.store.add_record(
            user_id=event.get_sender_id(),
            user_name=event.get_sender_name() or event.get_sender_id(),
            platform=event.get_platform_name() or "",
            rtype=parsed.type,
            amount=parsed.amount,
            category=parsed.category,
            note=parsed.note,
            raw_text=parsed.raw,
            created_at=created_at.timestamp(),
        )

    async def _make_comment(
        self, parsed: ParsedTransaction, event: AstrMessageEvent, today_expense: float
    ) -> str:
        mode = self.config.get("comment_mode", "local")
        budget = float(self.config.get("daily_budget", 0) or 0)
        if mode == "off":
            return ""
        if mode == "llm":
            try:
                provider_id = await self.context.get_current_chat_provider_id(
                    umo=event.unified_msg_origin
                )
                if provider_id:
                    resp = await self.context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=commenter.build_comment_prompt(
                            rtype=parsed.type,
                            amount=parsed.amount,
                            category=parsed.category,
                            note=parsed.note,
                            today_expense=today_expense,
                            daily_budget=budget,
                        ),
                    )
                    text = (resp.completion_text or "").strip().strip('"“” \n')
                    if text:
                        return text[:80]
            except Exception as e:
                logger.warning(f"[{PLUGIN_NAME}] LLM 点评失败，回退本地模板: {e}")
        return commenter.make_comment(
            rtype=parsed.type,
            amount=parsed.amount,
            category=parsed.category,
            today_expense=today_expense,
            daily_budget=budget,
            level=self.config.get("snark_level", commenter.NORMAL),
        )

    async def _build_reply(
        self, parsed: ParsedTransaction, event: AstrMessageEvent
    ) -> str:
        symbol = self.config.get("currency_symbol", "¥")
        is_income = parsed.type == TYPE_INCOME
        today0 = (
            datetime.now()
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )
        summary = await self.store.summary(user_id=event.get_sender_id(), start=today0)
        today_expense = summary.get("expense", 0.0)

        lines = [
            f"{'💰' if is_income else '🧾'} 记好啦！{'收入' if is_income else '支出'} "
            f"{_fmt_money(symbol, parsed.amount)}"
            + (
                f"（{parsed.category}"
                + (f"·{parsed.note}" if parsed.note else "")
                + "）"
            )
        ]
        date_str = ""
        if parsed.days_ago == 1:
            date_str = "（昨天）"
        elif parsed.days_ago == 2:
            date_str = "（前天）"
        if date_str:
            lines[0] += date_str

        budget = float(self.config.get("daily_budget", 0) or 0)
        if budget > 0:
            lines.append(
                f"今日已支出 {_fmt_money(symbol, today_expense)} / 预算 {_fmt_money(symbol, budget)}"
            )
        else:
            lines.append(f"今日已支出 {_fmt_money(symbol, today_expense)}")

        comment = await self._make_comment(parsed, event, today_expense)
        if comment:
            lines.append(f"💬 {comment}")
        return "\n".join(lines)

    # ================================================================= #
    # 指令（自然语言之外的保底方式）
    # ================================================================= #

    @filter.command_group("book", alias={"记账"})
    def book(self):
        """记账指令组"""

    @book.command("help", alias={"帮助", "菜单"})
    async def book_help(self, event: AstrMessageEvent):
        """查看记账插件使用帮助"""
        yield event.plain_result(_HELP_TEXT)

    @book.command("stats", alias={"统计", "账单"})
    async def book_stats(self, event: AstrMessageEvent):
        """查看账单统计，如 /book stats 30"""
        days = self._first_int(event.message_str, default=7, lo=1, hi=365)
        start, end = day_window(days)
        summary = await self.store.summary(
            user_id=event.get_sender_id(), start=start, end=end
        )
        by_category = await self.store.by_category(
            user_id=event.get_sender_id(), start=start, end=end
        )
        symbol = self.config.get("currency_symbol", "¥")
        net = summary["income"] - summary["expense"]
        lines = [
            f"📊 最近 {days} 天账单（共 {int(summary['count'])} 笔）",
            (
                f"支出 {_fmt_money(symbol, summary['expense'])} ｜ "
                f"收入 {_fmt_money(symbol, summary['income'])} ｜ "
                f"结余 {'+' if net >= 0 else '-'}{_fmt_money(symbol, abs(net))}"
            ),
        ]
        top = by_category.get(TYPE_EXPENSE, [])[:3]
        if top:
            parts = " · ".join(
                f"{row['category']} {_fmt_money(symbol, row['total'])}" for row in top
            )
            lines.append(f"🔴 支出 Top：{parts}")
        if summary["count"] == 0:
            lines.append("这段时间还没有记录，快说句话记一笔吧～")
        else:
            lines.append("明细：/book list ｜ 删除：/book del 序号")
        yield event.plain_result("\n".join(lines))

    @book.command("list", alias={"明细"})
    async def book_list(self, event: AstrMessageEvent):
        """查看最近明细，如 /book list 7"""
        days = self._first_int(event.message_str, default=7, lo=1, hi=365)
        start, end = day_window(days)
        records, total = await self.store.list_records(
            user_id=event.get_sender_id(), start=start, end=end, limit=10, offset=0
        )
        if not records:
            yield event.plain_result(f"📒 最近 {days} 天还没有记录～")
            return
        symbol = self.config.get("currency_symbol", "¥")
        lines = [f"📒 最近 {days} 天明细（共 {total} 笔，最新 10 条）"]
        for r in records:
            ts = datetime.fromtimestamp(r["created_at"]).strftime("%m-%d %H:%M")
            sign = "+" if r["type"] == TYPE_INCOME else "-"
            note = f"·{r['note']}" if r["note"] else ""
            lines.append(
                f"#{r['id']} {ts} {sign}{_fmt_money(symbol, r['amount'])} "
                f"{r['category']}{note}"
            )
        lines.append("删除某条：/book del 序号")
        yield event.plain_result("\n".join(lines))

    @book.command("del", alias={"删除"})
    async def book_del(self, event: AstrMessageEvent):
        """删除一条自己的记录，如 /book del 12"""
        record_id = 0
        for token in (event.message_str or "").split()[2:]:
            if token.isdigit():
                record_id = int(token)
                break
        if record_id <= 0:
            yield event.plain_result("用法：/book del 序号（序号见 /book list）")
            return
        ok = await self.store.delete_record(record_id, user_id=event.get_sender_id())
        if ok:
            yield event.plain_result(f"🗑️ 已删除记录 #{record_id}")
        else:
            yield event.plain_result(f"没有找到属于你的 #{record_id} 记录")

    @book.command("add", alias={"记", "记一笔"})
    async def book_add(self, event: AstrMessageEvent):
        """手动记账，如 /book add 35 午饭 或 /book add 8000 收入 工资"""
        parts = (event.message_str or "").split()[2:]  # 跳过指令本身
        if not parts:
            yield event.plain_result(
                "用法：/book add <金额> [收入] [备注]\n例：/book add 35 午饭"
            )
            return
        try:
            amount = round(float(parts[0]), 2)
        except ValueError:
            yield event.plain_result("第一个参数要是金额数字哦，例：/book add 35 午饭")
            return
        rest = parts[1:]
        rtype = TYPE_EXPENSE
        if rest and rest[0] in ("收入", "收", "income"):
            rtype = TYPE_INCOME
            rest = rest[1:]
        note = " ".join(rest)[:15]
        if note:
            category = infer_category(note, rtype)
        else:
            category = "其他收入" if rtype == TYPE_INCOME else "其他"
        parsed = ParsedTransaction(
            type=rtype,
            amount=amount,
            category=category,
            note=note,
            raw=event.message_str,
        )
        try:
            await self._save_record(parsed, event)
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] 手动记账失败: {e}")
            yield event.plain_result("记账失败了，请查看日志")
            return
        reply = await self._build_reply(parsed, event)
        yield event.plain_result(reply)

    @staticmethod
    def _first_int(message: str, default: int, lo: int, hi: int) -> int:
        """从消息里取第一个整数参数（跳过指令词），并夹到 [lo, hi]。"""
        for token in (message or "").split():
            if token.lstrip("-").isdigit():
                try:
                    return max(lo, min(int(token), hi))
                except ValueError:
                    return default
        return default

    # ================================================================= #
    # LLM 工具：让对话 Agent 也能读写账本
    # ================================================================= #

    @filter.llm_tool(name="record_transaction")
    async def tool_record_transaction(
        self,
        event: AstrMessageEvent,
        amount: float,
        transaction_type: str,
        category: str = "其他",
        note: str = "",
    ) -> MessageEventResult:
        """记录一笔收支到用户的账本。当用户在对话中表达了一笔花销或收入时调用。

        Args:
            amount(number): 金额，正数
            transaction_type(string): 类型，"expense"（支出）或 "income"（收入）
            category(string): 分类。支出可选：餐饮/交通/购物/娱乐/居住/通讯/医疗/学习/人情/宠物/数码/美容理发/其他；收入可选：工资收入/兼职外快/红包转账/理财收益/退款报销/其他收入
            note(string): 简短备注，不超过10个字
        """
        rtype = (
            transaction_type
            if transaction_type in (TYPE_EXPENSE, TYPE_INCOME)
            else TYPE_EXPENSE
        )
        try:
            amount = round(abs(float(amount)), 2)
        except (TypeError, ValueError):
            amount = 0.0
        if not 0 < amount <= 10_000_000:
            yield event.plain_result("金额不太对，这笔账没有记上")
            return
        note = (note or "")[:15]
        category = (category or "").strip()
        valid = (
            VALID_INCOME_CATEGORIES
            if rtype == TYPE_INCOME
            else VALID_EXPENSE_CATEGORIES
        )
        if category not in valid:
            # 模型给了不认识的分类时，从备注重新推断
            category = infer_category(note, rtype)
        parsed = ParsedTransaction(
            type=rtype, amount=amount, category=category, note=note, raw=""
        )
        await self._save_record(parsed, event)
        reply = await self._build_reply(parsed, event)
        yield event.plain_result(reply)

    @filter.llm_tool(name="query_ledger")
    async def tool_query_ledger(
        self, event: AstrMessageEvent, days: int = 7
    ) -> MessageEventResult:
        """查询用户自己最近 N 天的账目统计（总支出、总收入、笔数、支出分类汇总）。当用户想看账单、问自己花了多少钱时调用。

        Args:
            days(number): 查询最近多少天，默认 7
        """
        days = max(1, min(int(days or 7), 365))
        start, end = day_window(days)
        uid = event.get_sender_id()
        summary = await self.store.summary(user_id=uid, start=start, end=end)
        by_category = await self.store.by_category(user_id=uid, start=start, end=end)
        top = by_category.get(TYPE_EXPENSE, [])[:5]
        symbol = self.config.get("currency_symbol", "¥")
        parts = "、".join(
            f"{row['category']} {_fmt_money(symbol, row['total'])}（{row['count']}笔）"
            for row in top
        )
        text = (
            f"最近 {days} 天：支出 {_fmt_money(symbol, summary['expense'])}，"
            f"收入 {_fmt_money(symbol, summary['income'])}，共 {int(summary['count'])} 笔。"
        )
        if parts:
            text += f"支出分类：{parts}。"
        yield event.plain_result(text)

    # ================================================================= #
    # WebUI 可视化页面后端
    # ================================================================= #

    async def api_users(self):
        try:
            return json_response({"users": await self.store.list_users()})
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] api_users: {e}")
            return error_response("failed to list users", status_code=500)

    async def api_stats(self):
        try:
            days = request.query.get("days", 30, type=int) or 30
            days = max(1, min(days, 3650))
            user_id = (request.query.get("user_id") or "").strip() or None
            start, end = day_window(days)
            p_start, p_end = prev_window(days)
            summary = await self.store.summary(user_id=user_id, start=start, end=end)
            prev = await self.store.summary(user_id=user_id, start=p_start, end=p_end)
            by_category = await self.store.by_category(
                user_id=user_id, start=start, end=end
            )
            by_day = await self.store.by_day(user_id=user_id, start=start, end=end)
            return json_response(
                {
                    "days": days,
                    "currency": self.config.get("currency_symbol", "¥"),
                    "summary": summary,
                    "prev_summary": prev,
                    "by_category": by_category,
                    "by_day": by_day,
                }
            )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] api_stats: {e}")
            return error_response("failed to load stats", status_code=500)

    async def api_records(self):
        try:
            page = max(1, request.query.get("page", 1, type=int) or 1)
            page_size = request.query.get("page_size", 20, type=int) or 20
            page_size = max(10, min(page_size, 100))
            user_id = (request.query.get("user_id") or "").strip() or None
            rtype = request.query.get("type") or None
            category = (request.query.get("category") or "").strip() or None
            days = request.query.get("days", 30, type=int) or 30
            days = max(1, min(days, 3650))
            start, end = day_window(days)
            records, total = await self.store.list_records(
                user_id=user_id,
                start=start,
                end=end,
                rtype=rtype,
                category=category,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
            return json_response(
                {
                    "records": records,
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                }
            )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] api_records: {e}")
            return error_response("failed to load records", status_code=500)

    async def api_record_delete(self, record_id: str):
        try:
            rid = int(record_id)
        except (TypeError, ValueError):
            return error_response("invalid record id", status_code=400)
        try:
            ok = await self.store.delete_record(rid)
            return json_response({"deleted": ok})
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] api_record_delete: {e}")
            return error_response("failed to delete record", status_code=500)

    async def api_export(self):
        try:
            fmt = (request.query.get("format") or "csv").lower()
            if fmt not in ("csv", "json"):
                return error_response("format must be csv or json", status_code=400)
            days = max(1, min(request.query.get("days", 30, type=int) or 30, 3650))
            user_id = (request.query.get("user_id") or "").strip() or None
            start, end = day_window(days)
            records, _total = await self.store.list_records(
                user_id=user_id, start=start, end=end, limit=100000, offset=0
            )
            export_dir = self.store.db_path.parent / "exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            if fmt == "json":
                path = export_dir / "ledger_export.json"
                path.write_text(
                    json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                return file_response(
                    path, filename="ledger_export.json", content_type="application/json"
                )
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(
                [
                    "序号",
                    "时间",
                    "用户",
                    "平台",
                    "类型",
                    "金额",
                    "分类",
                    "备注",
                    "原始消息",
                ]
            )
            for r in records:
                writer.writerow(
                    [
                        r["id"],
                        datetime.fromtimestamp(r["created_at"]).strftime(
                            "%Y-%m-%d %H:%M"
                        ),
                        r["user_name"],
                        r["platform"],
                        "收入" if r["type"] == TYPE_INCOME else "支出",
                        r["amount"],
                        r["category"],
                        r["note"],
                        r["raw_text"],
                    ]
                )
            path = export_dir / "ledger_export.csv"
            # utf-8-sig 让 Excel 正确识别中文
            path.write_text(buf.getvalue(), encoding="utf-8-sig")
            return file_response(
                path, filename="ledger_export.csv", content_type="text/csv"
            )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] api_export: {e}")
            return error_response("failed to export ledger", status_code=500)
