"""自然语言记账解析器。

把「午饭花了35」「打车花了20.5元」「发工资了8000块」这类口语消息解析成
结构化账目。纯本地实现（正则 + 中文数字），不依赖大模型；复杂语句可以
配合 LLM 兜底解析（见 build_parse_prompt / parse_llm_response）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

TYPE_EXPENSE = "expense"
TYPE_INCOME = "income"

# --------------------------------------------------------------------- #
# 中文数字
# --------------------------------------------------------------------- #

_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def cn_to_int(text: str) -> int | None:
    """中文数字转整数，如「三十五」->35、「两百」->200。解析失败返回 None。"""
    total = section = num = 0
    seen = False
    for ch in text:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
            seen = True
        elif ch == "万":
            if not seen:
                return None
            section = (section + num) * 10000
            total += section
            section = num = 0
        elif ch in _CN_UNITS:
            if not seen:
                num = 1  # 「十」开头，如「十五」
                seen = True
            section += num * _CN_UNITS[ch]
            num = 0
        else:
            return None
    result = total + section + num
    return result if seen and result > 0 else None


# --------------------------------------------------------------------- #
# 类型 / 分类关键词
# --------------------------------------------------------------------- #

# 命中任意一个则判定为收入（未命中默认支出）
INCOME_TYPE_KEYWORDS = [
    "工资",
    "薪水",
    "年终奖",
    "奖金",
    "收入",
    "到账",
    "到帐",
    "到手",
    "进账",
    "退款",
    "退回",
    "报销",
    "分红",
    "利息",
    "收益",
    "稿费",
    "佣金",
    "兼职",
    "外快",
    "私活",
    "副业",
    "压岁钱",
    "中奖",
    "赚",
    "挣",
    "收到",
    "收红包",
    "发了工资",
    "发工资",
]

CATEGORY_RULES: list[tuple[str, list[str]]] = [
    (
        "餐饮",
        [
            "吃饭",
            "午饭",
            "午餐",
            "早饭",
            "早餐",
            "晚饭",
            "晚餐",
            "夜宵",
            "外卖",
            "奶茶",
            "咖啡",
            "可乐",
            "雪糕",
            "冰淇淋",
            "饮料",
            "零食",
            "火锅",
            "烧烤",
            "串",
            "米线",
            "米粉",
            "拉面",
            "面条",
            "麻辣烫",
            "冒菜",
            "汉堡",
            "披萨",
            "寿司",
            "食堂",
            "饭店",
            "馆子",
            "干饭",
            "吃饭了",
            "饿了么",
            "美团",
            "麦当劳",
            "肯德基",
            "星巴克",
            "瑞幸",
            "蜜雪",
            "喜茶",
            "茶百道",
            "海底捞",
            "点外卖",
            "下午茶",
            "蛋糕",
            "面包",
        ],
    ),
    (
        "交通",
        [
            "打车",
            "滴滴",
            "出租",
            "地铁",
            "公交",
            "巴士",
            "单车",
            "骑行",
            "哈啰",
            "加油",
            "油费",
            "油钱",
            "火车",
            "高铁",
            "动车",
            "机票",
            "飞机",
            "船票",
            "停车",
            "过路",
            "高速",
            "代驾",
            "拼车",
            "充公交",
            "加油费",
        ],
    ),
    (
        "购物",
        [
            "淘宝",
            "京东",
            "拼多多",
            "天猫",
            "超市",
            "便利店",
            "罗森",
            "全家",
            "衣服",
            "裤子",
            "裙",
            "鞋",
            "化妆品",
            "护肤",
            "口红",
            "纸巾",
            "日用品",
            "购物",
            "囤货",
            "买菜",
            "水果",
        ],
    ),
    (
        "娱乐",
        [
            "电影",
            "影院",
            "游戏",
            "steam",
            "switch",
            "ps5",
            "xbox",
            "ktv",
            "k歌",
            "唱k",
            "剧本杀",
            "密室",
            "旅游",
            "景点",
            "门票",
            "演唱会",
            "音乐节",
            "氪金",
            "网吧",
            "网费",
            "手游",
            "原神",
            "王者",
            "抽卡",
            "视频会员",
            "会员",
            "追剧",
            "演出",
            "桌游",
        ],
    ),
    (
        "居住",
        [
            "房租",
            "水电",
            "水费",
            "电费",
            "燃气",
            "物业",
            "房贷",
            "住宿",
            "酒店",
            "民宿",
            "旅馆",
            "租房",
        ],
    ),
    (
        "通讯",
        [
            "话费",
            "流量",
            "宽带",
            "电话费",
            "运营商",
            "充话费",
        ],
    ),
    (
        "医疗",
        [
            "药",
            "医院",
            "看病",
            "挂号",
            "打针",
            "输液",
            "体检",
            "牙",
            "眼科",
            "感冒",
            "发烧",
            "创可贴",
            "口罩",
        ],
    ),
    (
        "学习",
        [
            "课本",
            "书",
            "网课",
            "课",
            "学费",
            "培训",
            "考试",
            "报名费",
            "文具",
            "打印",
            "复印",
            "资料",
            "知识付费",
        ],
    ),
    (
        "人情",
        [
            "份子",
            "随礼",
            "礼金",
            "礼物",
            "请客",
            "生日",
            "红包",
        ],
    ),
    (
        "宠物",
        [
            "猫粮",
            "狗粮",
            "猫砂",
            "宠物",
            "驱虫",
            "绝育",
            "猫罐头",
            "狗罐头",
        ],
    ),
    (
        "数码",
        [
            "手机",
            "电脑",
            "笔记本",
            "平板",
            "耳机",
            "键盘",
            "鼠标",
            "相机",
            "充电器",
            "数据线",
            "硬盘",
            "内存条",
            "显示器",
            "屏幕",
        ],
    ),
    (
        "美容理发",
        [
            "理发",
            "剪头",
            "烫头",
            "染发",
            "美甲",
            "美睫",
            "按摩",
            "洗浴",
            "搓澡",
        ],
    ),
]

INCOME_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("工资收入", ["工资", "薪水", "年终奖", "奖金", "发薪"]),
    ("兼职外快", ["兼职", "外快", "私活", "副业", "稿费", "佣金"]),
    ("红包转账", ["红包", "压岁钱", "转账"]),
    ("理财收益", ["利息", "基金", "股票", "理财", "分红", "收益"]),
    ("退款报销", ["退款", "退货", "报销", "退回"]),
]

DEFAULT_CATEGORY = {"expense": "其他", "income": "其他收入"}

# --------------------------------------------------------------------- #
# 金额提取
# --------------------------------------------------------------------- #

_NUM = r"\d+(?:\.\d{1,2})?"

# 「三块五」「五毛三」这类口语：先转成小数形式
_RE_JIAO_CN = re.compile(
    r"([一二两三四五六七八九])\s*[块元]\s*([一二三四五六七八九])(?![\d块元毛])"
)
# 「三十五块」「两百元」：中文数字 + 单位
_RE_CN_MONEY = re.compile(
    r"([零一二两三四五六七八九十百千万]+)\s*(块钱|人民币|大洋|元|块)"
)

_RE_CNY_PREFIX = re.compile(rf"[¥￥$＄]\s*({_NUM})")
# 「3块5」=3.5，需在普通「数字+单位」之前匹配，避免丢掉角位
_RE_MONEY_JIAO = re.compile(r"(\d+)\s*[块元]\s*([1-9])(?!\d)")
_RE_MONEY_UNIT = re.compile(rf"({_NUM})\s*(?:块钱|人民币|大洋|元|块|米)")

# 数字后面跟量词时不是金额（「买了3个」「跑了5公里」）
_COUNTER_UNITS = (
    "个|次|斤|kg|KM|公里|千米|米|分|秒|小时|时|天|日|楼|号|人|岁|瓶|杯|张|"
    "件|台|部|只|条|篇|局|场|折|%|％|倍|星|关"
)
_RE_COUNTER_NUM = re.compile(rf"{_NUM}\s*(?:{_COUNTER_UNITS})(?![\d.])")

# 无货币单位时的兜底：必须紧跟明确的记账动词/名词
_RE_BARE_INCOME = re.compile(
    rf"(?:收入|工资|到账|到帐|到手|发工资|赚了?|挣了?|收了?|退了?)"
    rf"\s*(?:人民币)?\s*({_NUM})(?!\s*(?:{_COUNTER_UNITS}))"
)
_RE_BARE_EXPENSE = re.compile(
    rf"(?:记(?:账|个账|一笔|下|了)|花(?:费|钱)?了?|消费了?|支付了?|付了?|付钱|充值|氪了?|买了?|支出)"
    rf"\s*(?:人民币)?\s*({_NUM})(?!\s*(?:{_COUNTER_UNITS}))"
)

# 整句就是「名词 数字」结构（「猫粮 129」「午饭 35」）
_RE_ITEM_NUMBER = re.compile(r"^([一-龥A-Za-z]{1,10})\s*(\d+(?:\.\d{1,2})?)\s*[块钱]?$")

# 裸数字兜底的触发词：句中只有一个数字且命中这些词时才认为在记账
EXPENSE_TRIGGERS = (
    "花",
    "买",
    "付",
    "消费",
    "充值",
    "充了",
    "氪",
    "支出",
    "记账",
    "记了",
    "记一笔",
    "发红包",
    "随礼",
    "份子",
    "打车",
    "外卖",
    "房租",
    "话费",
    "加油",
)

# 用于备注清洗：金额相关 token（含残留单位）
_RE_MONEY_ANY = re.compile(
    rf"[¥￥$＄]\s*{_NUM}|{_NUM}\s*(?:块钱|人民币|大洋|元|块|毛|角|分|米)|[零一二两三四五六七八九十百千万]+(?:块|元)"
)

# --------------------------------------------------------------------- #
# 备注清洗
# --------------------------------------------------------------------- #

_FILLERS = [
    "我们",
    "我",
    "今天",
    "昨天",
    "前天",
    "大前天",
    "刚刚",
    "刚才",
    "刚",
    "早上",
    "上午",
    "中午",
    "下午",
    "晚上",
    "凌晨",
    "然后",
    "一个",
    "一共",
    "总共",
    "自己",
    "又",
    "再",
    "还",
    "在",
    "去",
    "了",
    "的",
    "吧",
    "呢",
    "啊",
    "呀",
    "哦",
    "哈",
    "嗯",
    "啦",
    "诶",
    "唉",
    "呜",
    "嘿嘿",
    "发的",
    "到手",
    "收了",
    "退了",
    "记",
    "记账",
    "记个账",
    "记一笔",
    "记下",
    "记了",
    "花",
    "花费",
    "花钱",
    "消费",
    "支付",
    "付",
    "付钱",
    "充值",
    "氪",
    "买",
    "买了",
    "支出",
    "收入",
    "到账",
    "到帐",
    "赚",
    "挣",
    "收到",
    "工资",
    "发了",
    "发",
    "给",
    "上",
    "吃",
    "喝",
    "看",
    "块",
    "元",
]

_DAYS_AGO_WORDS = [("大前天", 3), ("前天", 2), ("昨天", 1)]


@dataclass
class ParsedTransaction:
    """一条解析完成的账目。"""

    type: str  # expense / income
    amount: float
    category: str
    note: str
    days_ago: int = 0
    raw: str = ""


def _normalize_cn_money(text: str) -> str:
    """把中文数字金额统一成阿拉伯数字，方便后续正则处理。"""

    def jiao_repl(m: re.Match) -> str:
        a, b = cn_to_int(m.group(1)), cn_to_int(m.group(2))
        return f"{a}.{b}块" if a and b else m.group(0)

    text = _RE_JIAO_CN.sub(jiao_repl, text)

    def full_repl(m: re.Match) -> str:
        val = cn_to_int(m.group(1))
        return f"{val}{m.group(2)}" if val else m.group(0)

    return _RE_CN_MONEY.sub(full_repl, text)


def _find_amount(text: str, rtype: str) -> tuple[float, str] | None:
    """按优先级提取金额，返回 (金额, 命中的原文片段)。"""
    for pattern in (
        _RE_CNY_PREFIX,
        _RE_MONEY_JIAO,
        _RE_MONEY_UNIT,
        _RE_BARE_INCOME,
        _RE_BARE_EXPENSE,
    ):
        m = pattern.search(text)
        if not m:
            continue
        if pattern is _RE_MONEY_JIAO:
            amount = int(m.group(1)) + int(m.group(2)) * 0.1
        else:
            try:
                amount = float(m.group(1))
            except ValueError:
                continue
        if 0 < amount <= 10_000_000:
            return round(amount, 2), m.group(0)

    # 兜底 1：整句是「名词 数字」
    m = _RE_ITEM_NUMBER.search(text)
    if m:
        amount = round(float(m.group(2)), 2)
        if 0 < amount <= 10_000_000:
            return amount, m.group(2)

    # 兜底 2：全句只有一个数字 + 明确触发词（排除量词计数）
    if _RE_COUNTER_NUM.search(text):
        return None
    tokens = re.findall(_NUM, text)
    if len(tokens) != 1:
        return None
    triggers = INCOME_TYPE_KEYWORDS if rtype == TYPE_INCOME else EXPENSE_TRIGGERS
    if not any(t in text for t in triggers):
        return None
    try:
        amount = round(float(tokens[0]), 2)
    except ValueError:
        return None
    if 0 < amount <= 10_000_000:
        return amount, tokens[0]
    return None


def _extract_note(text: str, rtype: str) -> str:
    """从原句里抠出简短备注（物品/事项名）。"""
    cleaned = _RE_MONEY_ANY.sub("", text)
    cleaned = re.sub(r"\d+(?:\.\d+)?", " ", cleaned)  # 去掉裸数字（金额）
    for w in _FILLERS:
        cleaned = cleaned.replace(w, " ")
    # 只保留中文、字母
    cleaned = re.sub(r"[^一-龥A-Za-z]+", " ", cleaned)
    tokens = [t for t in cleaned.split() if t]
    if tokens:
        max_len = max(len(t) for t in tokens)
        # 等长时取靠后的 token（宾语通常在动词之后，如「给同事发红包」-> 红包）
        note = next(t for t in reversed(tokens) if len(t) == max_len)
    else:
        note = ""
    if len(note) > 15:
        note = note[:15]
    return note if note else ""


def infer_category(text: str, rtype: str) -> str:
    rules = INCOME_CATEGORY_RULES if rtype == TYPE_INCOME else CATEGORY_RULES
    low = text.lower()
    for category, keywords in rules:
        for kw in keywords:
            if kw in low:
                return category
    return DEFAULT_CATEGORY.get(rtype, "其他")


def detect_type(text: str) -> str:
    return (
        TYPE_INCOME if any(kw in text for kw in INCOME_TYPE_KEYWORDS) else TYPE_EXPENSE
    )


def looks_like_question(text: str) -> bool:
    return ("?" in text) or ("？" in text) or ("多少" in text)


def parse(text: str) -> ParsedTransaction | None:
    """解析一条消息。不是记账语句时返回 None。"""
    text = text.strip()
    if not text or len(text) > 80 or looks_like_question(text):
        return None

    normalized = _normalize_cn_money(text)
    rtype = detect_type(normalized)
    found = _find_amount(normalized, rtype)
    if found is None:
        return None
    amount, _matched = found

    days_ago = next((d for word, d in _DAYS_AGO_WORDS if word in text), 0)
    note = _extract_note(text, rtype)
    category = infer_category(normalized, rtype)
    return ParsedTransaction(
        type=rtype,
        amount=amount,
        category=category,
        note=note,
        days_ago=days_ago,
        raw=text,
    )


def plausible_for_llm(text: str) -> bool:
    """是否值得交给 LLM 兜底解析（粗过滤，避免每条消息都打模型）。"""
    text = text.strip()
    if not text or looks_like_question(text):
        return False
    if text.startswith(("记账", "记个账", "记一笔", "记下")) or "记账" in text:
        return True
    has_number = any(ch.isdigit() for ch in text) or any(
        ch in "零一二两三四五六七八九十百千万" for ch in text
    )
    has_keyword = any(kw in text for kw in INCOME_TYPE_KEYWORDS) or any(
        kw in text for kw in ("花", "买", "付", "消费", "充", "氪", "支出", "贵")
    )
    return has_number and has_keyword


# --------------------------------------------------------------------- #
# LLM 兜底解析
# --------------------------------------------------------------------- #

VALID_EXPENSE_CATEGORIES = {cat for cat, _ in CATEGORY_RULES} | {"其他"}
VALID_INCOME_CATEGORIES = {cat for cat, _ in INCOME_CATEGORY_RULES} | {"其他收入"}

PARSE_PROMPT = (
    "你是记账插件的解析器。判断下面这条聊天消息是否在记账（花钱/消费）或记收入。"
    "如果是，提取信息并只输出一个 JSON 对象，不要任何其他文字：\n"
    '{"type":"expense或income","amount":数字,"category":"分类","note":"不超过8字的备注","days_ago":0}\n'
    "category 规则：支出从[餐饮,交通,购物,娱乐,居住,通讯,医疗,学习,人情,宠物,数码,美容理发,其他]中选；"
    "收入从[工资收入,兼职外快,红包转账,理财收益,退款报销,其他收入]中选。\n"
    "days_ago 是消息提到的日期距今天数（昨天=1，前天=2），未提及则为 0。\n"
    "如果消息不是在记账也不是记收入，只输出 null。\n"
    "消息：{text_placeholder}"
)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```")
_JSON_OBJECT = re.compile(r"\{[\s\S]*\}")


def build_parse_prompt(text: str) -> str:
    return PARSE_PROMPT.replace("{text_placeholder}", text)


def parse_llm_response(content: str) -> ParsedTransaction | None:
    """解析 LLM 的返回。非 JSON、null 或校验失败均返回 None。"""
    content = (content or "").strip()
    if not content or content.lower() in ("null", "none"):
        return None
    m = _JSON_FENCE.search(content) or _JSON_OBJECT.search(content)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("type") not in (
        TYPE_EXPENSE,
        TYPE_INCOME,
    ):
        return None
    try:
        amount = round(float(data.get("amount", 0)), 2)
    except (TypeError, ValueError):
        return None
    if not 0 < amount <= 10_000_000:
        return None
    rtype = data["type"]
    valid = (
        VALID_INCOME_CATEGORIES if rtype == TYPE_INCOME else VALID_EXPENSE_CATEGORIES
    )
    category = str(data.get("category") or "").strip()
    if category not in valid:
        category = DEFAULT_CATEGORY.get(rtype, "其他")
    note = str(data.get("note") or "").strip()[:15]
    try:
        days_ago = max(0, min(int(data.get("days_ago", 0) or 0), 30))
    except (TypeError, ValueError):
        days_ago = 0
    return ParsedTransaction(
        type=rtype,
        amount=amount,
        category=category,
        note=note,
        days_ago=days_ago,
        raw="",
    )
