"""幽默点评生成器。

两种模式：
- local：内置吐槽模板（分级：温柔 / 普通 / 毒舌），零成本即时返回；
- llm：调用大模型逐笔点评（由 main.py 调用 build_comment_prompt 构造提示词）。
"""

from __future__ import annotations

import random

GENTLE, NORMAL, SAVAGE = "gentle", "normal", "savage"

# --------------------------------------------------------------------- #
# 支出点评（按金额档位）
# --------------------------------------------------------------------- #

_EXPENSE_SMALL = {  # < 20 元
    GENTLE: [
        "小额支出也记得这么清楚，是个会过日子的人～",
        "{amount}元的小确幸，钱包毫无痛感，放心享用～",
        "这点小钱，就当给生活加点甜～",
    ],
    NORMAL: [
        "{amount}块钱也是钱，钱包表示：涓涓细流也是流。",
        "小钱出逃成功！记住：奶茶自由的尽头是记账自由。",
        "积少成多预警：这种小钱一年下来能绕地球……好吧，半圈。",
    ],
    SAVAGE: [
        "{amount}块也好意思记？行吧，穷得明明白白也是一种境界。",
        "这么点钱都要跟机器人汇报，你的财务状况我大致有数了。",
        "小额消费警告：蚊子腿也是肉，你这剁手频率堪比缝纫机。",
    ],
}

_EXPENSE_MID = {  # 20 ~ 100 元
    GENTLE: [
        "{note_or_category}的花费已安全入库，注意劳逸结合哦～",
        "{amount}元，属于正常生活成本，不用有负担～",
        "记好啦，适当消费是对自己努力的奖励～",
    ],
    NORMAL: [
        "{amount}元，属于「咬咬牙不疼、但心里咯噔一下」区间。",
        "{note_or_category}的快乐是钱买来的，但快乐是真的！",
        "叮！{amount}元已离场，感谢您为 GDP 做出的贡献。",
    ],
    SAVAGE: [
        "{amount}元说没就没，建议下次消费前先问问钱包的意见。",
        "{note_or_category}？行，又一个「就这一次」。",
        "你的钱就像青春，{amount}元悄无声息地逝去了。",
    ],
}

_EXPENSE_LARGE = {  # 100 ~ 500 元
    GENTLE: [
        "{amount}元是一笔不小的开销，记得核对一下是否必要哦～",
        "大额支出已记录，接下来几天可以稍微收敛一点～",
        "花都花了，开心最重要，账本帮你记着呢～",
    ],
    NORMAL: [
        "叮！检测到 {amount} 元级消费，本月吃土进度条 +1。",
        "{note_or_category}：{amount}元。钱包已进入「贤者时间」。",
        "这波消费后，你的余额和头发一样，都在悄悄变少。",
    ],
    SAVAGE: [
        "{amount}元！你是嫌钱包太胖了吗？",
        "恭喜 {note_or_category}荣登本月「最肉疼支出」候选榜。",
        "花 {amount} 元的时候手速挺快，记账倒要机器人催。",
    ],
}

_EXPENSE_HUGE = {  # >= 500 元
    GENTLE: [
        "{amount}元的大额支出已记录，建议接下来理性消费哦～",
        "好大一笔！先深呼吸，账本会帮你盯着后续开支的～",
    ],
    NORMAL: [
        "叮！{amount}元大额消费！吃土倒计时已启动，请自备勺子。",
        "{amount}元……建议未来一周多看食堂、少看购物软件。",
        "这笔 {amount} 元花出去的瞬间，我仿佛听到了钱包的哀嚎。",
    ],
    SAVAGE: [
        "{amount}元？！你是和钱有仇吗？它带你如初恋，你甩它如敝履。",
        "大额消费警告：再这么买，下个月就得用花呗还花呗了。",
        "{amount}元，足够吃一百顿食堂，而你只换来了一句「真香」。",
    ],
}

# --------------------------------------------------------------------- #
# 收入点评
# --------------------------------------------------------------------- #

_INCOME = {
    GENTLE: [
        "收入到账！一分一毫都是努力的证明，继续保持～",
        "又进账 {amount}元，积少成多，未来可期～",
        "赚钱的能力 +1，记得善待努力的自己～",
    ],
    NORMAL: [
        "叮咚～{amount}元入账！钱包表示久旱逢甘霖。",
        "赚钱小能手就是你！不过小心，到账的钱还没焐热就要花出去了。",
        "{amount}元已到账，按惯例，它将在三天内神秘消失。",
    ],
    SAVAGE: [
        "{amount}元入账！按你的花钱速度，建议直接设定时转出。",
        "恭喜进账 {amount}元，你的钱包终于可以挺直腰杆……半天。",
        "赚 {amount}元靠实力，留住它靠奇迹。",
    ],
}

_INCOME_CATEGORY_JOKES = {
    "工资收入": "工资到账三秒后，将被房租、花呗和外卖瓜分完毕。",
    "兼职外快": "副业收入！主副业齐飞，早日财富自由～",
    "红包转账": "红包到手！缘分到了，钱也到了。",
    "理财收益": "理财有收益，巴菲特看了都点头（大概）。",
    "退款报销": "退款也是收入！该拿回来的钱一分都不能少。",
}

# --------------------------------------------------------------------- #
# 预算相关
# --------------------------------------------------------------------- #

_BUDGET_OVER = {
    GENTLE: "今日预算已超支 {over}元，接下来几天稍微控制一下会更稳哦～",
    NORMAL: "⚠️ 预算警报：今日已超支 {over}元，建议切换「吃土模式」。",
    SAVAGE: "预算？什么预算？你已经超了 {over}元了朋友，钱包正在ICU抢救。",
}

_BUDGET_LEFT = {
    GENTLE: "今日预算还剩 {left}元，控制得不错，继续保持～",
    NORMAL: "今日预算余额 {left}元，安全线以内，稳。",
    SAVAGE: "今日还能花 {left}元。注意是「还能」，不是「必须」。",
}

# --------------------------------------------------------------------- #
# 支出分类专属吐槽（命中时按概率替换默认点评）
# --------------------------------------------------------------------- #

_EXPENSE_CATEGORY_JOKES = {
    "餐饮": [
        "干饭人干饭魂，干饭都是人上人——但饭钱也是真金白银。",
        "吃饱了才有力气赚钱，这钱花得很有原则。",
    ],
    "交通": [
        "这钱花得值，毕竟能躺着到站也是一种本事。",
        "从 A 到 B，钱包贡献了最丝滑的一段。",
    ],
    "购物": [
        "买买买是快乐的，账单是诚实的。",
        "购物车清空了，账户余额也清空了，完美闭环。",
    ],
    "娱乐": [
        "快乐无价，但有发票。",
        "氪金一时爽，一直氪金……你也氪不起。",
    ],
    "居住": [
        "住的成本，打工人的宿命。",
        "房子是租来的，但生活不是——钱是实实在在没的。",
    ],
    "通讯": [
        "话费已缴，世界的喧嚣将继续向你涌来。",
        "为流量付费，为冲浪自由充值。",
    ],
    "医疗": [
        "身体是革命的本钱，这个钱不能省。",
        "给健康投资，回报率最高的一笔。",
    ],
    "学习": [
        "知识付费！今天的课，是明天的工资。",
        "脑袋充实了，钱包空了，等价交换。",
    ],
    "人情": [
        "人情往来，社会人的必修课。",
        "礼尚往来，来的是情，往的是钱。",
    ],
    "宠物": [
        "主子开心就好，铲屎官的钱包表示情绪稳定。",
        "猫狗双全的日子，钱包瘦成一道闪电。",
    ],
    "数码": [
        "数码产品：买前生产力，买后爱奇艺。",
        "又给科技树上浇了一次水。",
    ],
    "美容理发": [
        "Tony 老师的手艺，总是值这个价（吗）。",
        "头等大事，不能省。",
    ],
    "其他": [],
}

_LEVEL_POOLS_EXPENSE = [
    (20.0, _EXPENSE_SMALL),
    (100.0, _EXPENSE_MID),
    (500.0, _EXPENSE_LARGE),
    (float("inf"), _EXPENSE_HUGE),
]


def _pick(pool: dict[str, list[str]], level: str, **ctx: object) -> str:
    candidates = pool.get(level) or pool[NORMAL]
    text = random.choice(candidates)
    for key, val in ctx.items():
        text = text.replace("{" + key + "}", str(val))
    return text


def make_comment(
    *,
    rtype: str,
    amount: float,
    category: str,
    today_expense: float,
    daily_budget: float,
    level: str = NORMAL,
) -> str:
    """生成本地幽默点评。rtype: 'expense' / 'income'。"""
    if level not in (GENTLE, NORMAL, SAVAGE):
        level = NORMAL
    note_or_category = category

    if rtype == "income":
        if category in _INCOME_CATEGORY_JOKES and random.random() < 0.5:
            return _INCOME_CATEGORY_JOKES[category]
        return _pick(_INCOME, level, amount=f"{amount:g}")

    # 支出：预算超支时优先吐槽预算
    if daily_budget and daily_budget > 0:
        if today_expense > daily_budget:
            return _BUDGET_OVER[level].format(
                over=f"{today_expense - daily_budget:.2f}"
            )
        if random.random() < 0.3:
            left = max(daily_budget - today_expense, 0)
            return _BUDGET_LEFT[level].format(left=f"{left:g}")

    jokes = _EXPENSE_CATEGORY_JOKES.get(category) or []
    if jokes and random.random() < 0.4:
        return random.choice(jokes)

    for bound, pool in _LEVEL_POOLS_EXPENSE:
        if amount < bound:
            return _pick(
                pool, level, amount=f"{amount:g}", note_or_category=note_or_category
            )
    return _pick(
        _EXPENSE_MID, level, amount=f"{amount:g}", note_or_category=note_or_category
    )


def build_comment_prompt(
    *,
    rtype: str,
    amount: float,
    category: str,
    note: str,
    today_expense: float,
    daily_budget: float,
) -> str:
    """构造让大模型写幽默点评的提示词。"""
    kind = "收入" if rtype == "income" else "支出"
    budget_part = (
        f"，每日预算 {daily_budget:g} 元" if daily_budget and daily_budget > 0 else ""
    )
    return (
        f"你是记账机器人里负责吐槽的部分。用户刚记了一笔账：{kind} {amount:g} 元，"
        f"分类「{category}」，备注「{note or '无'}」。"
        f"今天已累计支出 {today_expense:g} 元{budget_part}。"
        "请用一句不超过 40 字的中文幽默点评这笔账，风格俏皮但友善，"
        "可以适度调侃但不要冒犯，直接输出点评内容本身，不要引号和任何多余文字。"
    )
