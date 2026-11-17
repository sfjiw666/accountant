# astrbot_plugin_accountant · AI 记账管家

一个 AstrBot 记账插件：**自然语言记账 + 幽默点评 + WebUI 可视化账本**。

**开发商**：梦创 ｜ **QQ 交流群**：167017318

> [!NOTE]
> 本插件基于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件模板 [helloworld](https://github.com/Soulter/helloworld) 开发，遵循 [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)。

## ✨ 功能特性

- 🗣️ **自然语言记账**：不需要记指令。直接对机器人说：
  - 「午饭花了 35」「打车 20.5 元」「昨天买奶茶 22」
  - 「发工资了 8000 块」「收到红包 50」「氪金 648」
  - 支持中文数字（「三十五块」「三块五」）、¥ 前缀、角位口语（「3块5」）
  - 本地规则秒解析；规则搞不定的复杂语句，自动交给大模型兜底（可关）
- 😄 **幽默点评**：每记一笔，机器人都会用诙谐的语言点评你的开销或收入
  - 三档毒舌程度：温柔 / 普通 / 毒舌
  - 识别分类专属吐槽（奶茶、氪金、理发 Tony 老师各有各的梗）
  - 可切换为 LLM 逐笔点评；配置每日预算后，超支会触发特别吐槽
- 🌍 **多平台**：只用 AstrBot 平台无关 API，QQ（OneBot v11 / 官方）、Telegram、微信、飞书、钉钉、Discord、Slack、KOOK 等 AstrBot 支持的平台全部可用
- 📊 **WebUI 可视化账本**：插件详情页内置「可视化账本」视图
  - 支出 / 收入 / 结余 / 笔数指标卡（含环比变化）
  - 支出、收入分类排行；每日收支趋势图（悬浮查看明细）
  - 全部用户、任意时间范围的明细表，支持筛选、删除、CSV / JSON 导出
  - 自适应亮 / 暗主题，支持中英文界面
- 🤖 **Agent 工具**：注册了 `record_transaction` / `query_ledger` 两个 LLM 工具，和机器人聊天时它也能顺手帮你记账、查账
- 💾 **SQLite 存储**：数据保存在 `data/plugin_data/astrbot_plugin_accountant/`，更新插件不丢数据

## 📦 安装

1. 将本仓库克隆到 AstrBot 的 `data/plugins/` 目录：

   ```bash
   cd AstrBot/data/plugins
   git clone https://github.com/yourname/astrbot_plugin_accountant
   ```

2. 安装依赖（AstrBot 会自动读取 `requirements.txt`，也可手动执行）：

   ```bash
   pip install -r astrbot_plugin_accountant/requirements.txt
   ```

3. 在 WebUI「插件」页重载/启用本插件。

> 可视化账本页面需要较新的 AstrBot 版本（建议 v4.13+）。自然语言记账与指令功能在更低版本也可使用。

## 🚀 使用

### 自然语言（推荐）

直接发消息，机器人自动识别并记账，例如：

```
你：午饭花了35
Bot：🧾 记好啦！支出 ¥35.00（餐饮·午饭）
     今日已支出 ¥35.00
     💬 35块钱也是钱，钱包表示：涓涓细流也是流。

你：发工资了8000块
Bot：💰 记好啦！收入 ¥8,000.00（工资收入）
     今日已支出 ¥0.00
     💬 工资到账三秒后，将被房租、花呗和外卖瓜分完毕。
```

支持说「昨天 / 前天」自动记账到对应日期；自动分类到餐饮、交通、购物、娱乐、居住、通讯、医疗、学习、人情、宠物、数码、美容理发等分类。

### 指令（保底方式）

| 指令 | 说明 |
| --- | --- |
| `/book help` | 查看帮助 |
| `/book stats [天数]` | 账单统计（默认 7 天） |
| `/book list [天数]` | 最近明细（含序号） |
| `/book del <序号>` | 删除一条自己的记录 |
| `/book add <金额> [收入] [备注]` | 手动记账，如 `/book add 35 午饭` |

指令组 `book` 有别名 `记账`，子指令也都有中文别名（`/记账 统计 30` 等效）。

### WebUI 可视化账本

WebUI → 插件 → **AI 记账管家** → **可视化账本**。可按时间范围（7 天 / 30 天 / 90 天 / 1 年）、用户筛选，查看图表与明细，删除误记的账目，或导出 CSV / JSON。

## ⚙️ 配置

在 WebUI 插件配置页即可修改：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `currency_symbol` | `¥` | 货币符号 |
| `nl_enabled` | `true` | 自然语言记账开关 |
| `nl_max_length` | `60` | 参与解析的消息最大长度 |
| `llm_parse_enabled` | `true` | 本地规则失败时用大模型兜底解析 |
| `llm_parse_in_group` | `false` | 群聊中是否也用大模型解析（默认关闭防止频繁调用） |
| `reply_in_group` | `true` | 群聊中是否回复记账结果（关闭则静默记账） |
| `comment_mode` | `local` | 点评模式：本地吐槽 / AI 点评 / 关闭 |
| `snark_level` | `normal` | 吐槽毒舌程度：温柔 / 普通 / 毒舌 |
| `daily_budget` | `0` | 每日预算，大于 0 时启用超支吐槽 |

## 🔧 工作原理

- `main.py` 插件主体：自然语言监听器、指令组、LLM 工具、Web API
- `parser.py` 自然语言解析：中文数字转换、金额提取（单位 / 角位 / 裸数字兜底）、分类推断、备注抽取
- `commenter.py` 幽默点评：分级模板 + 预算联动 + LLM 提示词
- `ledger.py` 存储层：aiosqlite 异步 SQLite，含按用户/时间/分类/类型的查询与汇总
- `views/ledger/` 可视化页面：无外部依赖的纯 HTML/CSS/JS，通过 `AstrBotPluginView` bridge 与后端通信

## 🧪 测试

```bash
python tests/test_parser.py   # 自然语言解析 & 点评生成
python tests/test_ledger.py   # 存储层（需要 aiosqlite）
```

## 📄 许可

继承模板的 MIT 许可，见 [LICENSE](LICENSE)。
