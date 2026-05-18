---
name: weibo-hot-tracker
description: 抓取微博热榜，筛选政治/经济/科技类话题推送飞书
category: social-media
---

# 微博热榜追踪

## 目录结构
```
weibo-hot-tracker/
├── SKILL.md              # Skill 说明文档
├── config.yaml           # 本地配置文件
├── prompt.yaml           # LLM 判断 prompt（外置，可被 optimize_prompt.py 优化）
├── keyword.json          # 关键词库（自动维护，记录微博 API 返回的分类）
├── push_history.jsonl    # 推送历史记录（含全部话题，pushed 字段区分是否已推送）
├── daemon_state.json     # feedback_daemon 会话状态持久化
├── run.py                # 主脚本
├── optimize_rules.py     # 规则优化脚本（LLM 预判 + 飞书交互确认）
├── optimize_prompt.py    # Prompt 优化脚本（根据反馈优化判断标准）
├── recall_topics.py      # 召回脚本（从被排除话题中召回用户可能感兴趣的）
├── feedback_daemon.py    # 飞书回调守护进程（处理所有卡片交互回调）
└── references/           # 参考文档
    ├── weibo-api-header.md   # 微博 API Header 要求
    └── weibo-script-timing.md # LLM 性能分析
```

## 脚本路径
- `run.py` — 主脚本，可直接执行或通过 cron 调度
- `optimize_rules.py` — 规则优化脚本，发现未归类分类并通过飞书交互确认
- `optimize_prompt.py` — Prompt 优化脚本，根据用户反馈优化判断标准
- `recall_topics.py` — 召回脚本，从被排除话题中召回用户可能感兴趣的
- `feedback_daemon.py` — 飞书回调守护进程，常驻运行，处理所有卡片交互回调

## 配置文件
`config.yaml` — 本地配置文件，优先级高于 `~/.hermes/config.yaml`

配置项（实际值以 config.yaml 为准）：
- `llm.model`: LLM 模型名（默认 `Qwen3.6-35B-A3B-C`）
- `llm.max_tokens`: 最大输出 tokens（默认 40960）
- `llm.timeout`: LLM 调用超时秒数（默认 180）
- `llm.temperature`: LLM 温度参数（默认 0.1）
- `llm.base_url`: LLM API 地址（默认空，从环境变量读取）
- `llm.api_key`: API 密钥（由 `_resolve_api_credentials()` 从全局配置解析）
- `feishu.chat_id`: 飞书群聊 ID（可被 `FEISHU_HOME_CHANNEL` 环境变量覆盖）
- `feishu.retry_times`: 推送重试次数（默认 3）
- `feishu.retry_delay`: 重试初始延迟秒数（默认 10）
- `filter.exclude_categories`: 排除的话题分类列表
- `filter.star_keywords.important`: 重要关键词
- `performance.empty_result_skip`: 空结果是否跳过 LLM 调用（默认 true）

`prompt.yaml` — LLM 判断 prompt，包含判断标准和输出格式

环境变量：
- `WEIBO_TRACKER_LOG_LEVEL`: 日志级别（DEBUG/INFO/WARNING/ERROR，默认 INFO）

## API
`https://weibo.com/ajax/statuses/hot_band` — 无需登录，直接返回52条热榜

## 过滤逻辑（二元体系：重要 / 排除）

### 核心原则
**只推重要话题，没有则不推送**（不发送任何消息）

### 判断标准（重要）

**AI/技术**：AI大模型（GPT/Claude/DeepSeek/豆包/千问/Qwen/通义/文心/ERNIE/智谱）、芯片（禁令/代工突破/设备）、新能源（固态电池/钠电池/碳化硅/重磅电动车）

**军事与地缘**：军事冲突（战争/武装对峙/导弹袭击）、重大装备（航母/隐身战机/战略核潜艇/新型导弹）

**经济政策**：国内政策（国务院/部委重要文件）、宏观指标（GDP/CPI/PPI/PMI/进出口/失业率）、美联储（FOMC/鲍威尔/美元利率）、房地产（重磅调控/房企爆雷/政府救市）

**经济/金融**：股市重大波动、金融监管政策、贸易战、产业政策

**汽车/新能源**：重磅新车型发布、自动驾驶重大突破、新能源政策

### 输出格式（Feishu 卡片消息）
```
┌──────────────────────────────────────┐
│  🔴 📊 微博热榜 · YYYY年MM月DD日 HH:MM  │  ← 红色标题栏
├──────────────────────────────────────┤
│ 共 N 条重要话题，政经科技类              │  ← bold 摘要
├──────────────────────────────────────┤
│ 🔴 #话题标题1#                       │
│ 🔴 #话题标题2#                       │
│ ...                                   │
├──────────────────────────────────────┤
│ 🕐 YYYY年MM月DD日 HH:MM              │  ← 时间戳
└──────────────────────────────────────┘
```

**msg_type**: `interactive`（Feishu Card Message）
**header.template**: `red`
**receive_id**: 从 `FEISHU_HOME_CHANNEL` 环境变量读取

### 反馈卡片（紧跟热榜卡片发送）
```
┌──────────────────────────────────────┐
│  📝 推送反馈 · YYYY年MM月DD日 HH:MM    │  ← 蓝色标题栏
├──────────────────────────────────────┤
│ 请评价以下话题是否值得关注              │
├──────────────────────────────────────┤
│ 话题标题1                              │
│   [👍 感兴趣] [👎 不感兴趣]            │
├──────────────────────────────────────┤
│ 话题标题2                              │
│   [👍 感兴趣] [👎 不感兴趣]            │
├──────────────────────────────────────┤
│           [⏭️ 跳过全部]               │
└──────────────────────────────────────┘
```

用户点击按钮后，由 `feedback_daemon.py` 更新 `push_history.jsonl` 中的 feedback 字段。

### 技术实现
- 预筛：过滤排除分类（娱乐/明星/综艺/体育/幽默/艺人/剧集等）及 rank=0 无效条目
- LLM 判断：从 `prompt.yaml` 读取 prompt，按序号返回 `1:【重要】\n2:【不重要】...`
- 解析：按序号映射，重要话题推送，不重要话题记录到 push_history.jsonl（pushed=false）
- LLM 失败：fallback 到 `is_important()` 规则匹配
- HTTP 客户端：Feishu 推送用 curl_cffi Session（DNS 稳定）

**HTTP 客户端**：Feishu 推送用 curl_cffi Session（DNS 更稳定），微博抓取用 requests

**Feishu 消息格式原则**：
- 飞书对 Markdown 表格渲染为空白 → 输出列表时不用表格，用单行 ` | ` 分隔各字段，或用卡片消息（msg_type=interactive）
- 飞书消息内容含独立空行 `\n\n` 会被当作消息分隔符 → 单行 + ` | ` 分隔各字段可避免拆包
- 卡片消息适合结构化内容：header（标题+颜色）+ elements（div/hr/link）

**API Key 模板变量**：`config.yaml` 里 `${CUSTOM_API_KEY}` 格式的 key 由 `_resolve_api_credentials()` 在启动时自动解析环境变量展开

**Cron 任务环境变量加载**：cron 任务启动的是独立 Python 进程，**不会自动加载 `.env` 文件**。脚本通过 `_load_dotenv()` 在启动时自动加载 `~/.hermes/.env`，无需手动处理。

**Cron 任务 prompt 注意事项**：`2>/dev/null` 会吞掉错误信息，导致失败时无任何日志；脚本内 `print()` 输出应写到 stderr，避免被 cron 捕获时混淆

**⚠️ 常见坑**：
- API key 若是 `${ENV_VAR}` 模板变量（全局 config.yaml 中），由 `_resolve_api_credentials()` 自动解析，无需手动处理
- LLM 返回格式用序号匹配（`1:【重要】`）而非话题名匹配——话题名含 # 号时容易匹配失败
- max_tokens 需设 3000 以上，否则 40 条话题的判断结果会被截断
- 本地 config.yaml 优先于全局 `~/.hermes/config.yaml`——base_url 等字段以本地配置为准

## 推送历史

`push_history.jsonl` — 每次运行追加一条记录，包含全部话题

```json
{
  "ts": "2026-05-16T10:30:00",
  "topics": [
    {"word": "芯片禁令升级", "category": "国内时政", "pushed": true, "feedback": null},
    {"word": "iPhone降价", "category": "数码", "pushed": false, "feedback": null}
  ],
  "total_fetched": 52
}
```

- `pushed: true` → 重要话题，推送时收集反馈
- `pushed: false` → 被排除的话题，召回时 LLM 从中筛选
- `feedback` 初始为 `null`，用户打分后更新为 `1`（感兴趣）或 `0`（不感兴趣）
- run.py 只追加（append-only），feedback_daemon.py 负责更新 feedback 字段（使用 fcntl 文件锁）

## 关键词维护

关键词库位于 `config.yaml` 的 `filter.star_keywords.important` 下，按语义分组并附注释。

每次运行脚本会自动更新 `keyword.json`，记录微博 API 返回的所有 `category` 值。可用于：
- 发现微博新增的分类名（如出现新的 `category` 值）
- 确认哪些分类已稳定出现，值得加入 `config.yaml` 的关键词配置
- 对比 `keyword.json` 中的分类与 `exclude_categories` / `star_keywords`，发现遗漏

### 何时添加关键词

1. **LLM 判断为重要但规则未匹配**：说明规则遗漏了该话题的特征词，应添加到 `important` 组
2. **新出现的微博分类名**：如果微博 API 新增了分类（如"科技"改为"前沿科技"），需同步更新 `exclude_categories` 或 `star_keywords`

### 如何添加关键词

1. 确定关键词属于 `important`（必须推送）还是 `exclude_categories`（排除）
2. 在对应组的合适语义分组中添加（如 AI 类、政策类、人物类等）
3. 如果没有合适的分组，新建一个并加注释说明
4. 运行脚本验证匹配效果

### 关键词匹配规则

- 匹配方式为**子串包含**：关键词 "芯片" 可匹配 "芯片禁令"、"半导体芯片" 等
- 匹配字段为 `category` + `field_tag` + `word_scheme` 的拼接文本

## 报告格式
```
📊 微博热榜 YYYY年MM月DD日 HH:MM（政经科技）

【🔴 重要】
🔴 #话题1#
🔴 #话题2#
...
```

## 本地存储
`~/.hermes/skill_results/weibo_reports/YYYYMMDD_HHMM.json`

## Cron（均为北京时间 +00:30）
- 0630 → UTC 22:30
- 0830 → UTC 00:30
- 1030 → UTC 02:30
- 1230 → UTC 04:30
- 1430 → UTC 06:30
- 1630 → UTC 08:30
- 1830 → UTC 10:30
- 2030 → UTC 12:30
- 2230 → UTC 14:30

## 规则优化

`optimize_rules.py` — 发现未归类分类，LLM 预判归属，飞书交互确认后由 feedback_daemon 写入 config.yaml

### 使用方式

```bash
python3 optimize_rules.py
```

### 前置条件

- `feedback_daemon.py` 正在运行（处理飞书回调）
- 飞书应用已开启「长连接接收回调」
- 飞书应用已订阅 `card.action.trigger` 事件
- keyword.json 存在（先运行 run.py 至少一次）

### 流程

1. 读取 keyword.json 中未在 config.yaml 归类的分类
2. LLM 预判每个分类的归属（排除/重要/跳过）
3. 发送飞书交互式卡片，用户逐个调整
4. 用户点击「确认提交」→ feedback_daemon 处理回调，写入 config.yaml（带备份 config.yaml.bak）
5. 会话状态持久化到 daemon_state.json

## 飞书回调守护进程

`feedback_daemon.py` — 常驻进程，通过飞书长连接监听所有卡片回调

### 启动方式

```bash
python3 feedback_daemon.py
```

### 前置条件

- 飞书应用已开启「长连接接收回调」
- 飞书应用已订阅 `card.action.trigger` 事件

### 支持的回调类型

| source | 触发场景 | 处理逻辑 |
|--------|----------|----------|
| `feedback` | 用户点击反馈卡片按钮 | 更新 push_history.jsonl 的 feedback 字段 |
| `optimize_rules` | 用户点击规则优化卡片按钮 | 更新 config.yaml 的分类归属 |
| `optimize_prompt` | 用户点击 Prompt 优化确认/放弃 | 更新或放弃 prompt.yaml |
| `recall` | 用户点击召回反馈卡片按钮 | 更新 push_history.jsonl 被排除话题的 feedback 字段 |

### 回调路由

卡片按钮 value 包含 `source` 字段，feedback_daemon.py 根据 source 分发到对应的内联处理函数。

### 会话状态

活跃会话持久化到 `daemon_state.json`，daemon 重启后可恢复。确认提交后删除对应 session。

## Prompt 优化

`optimize_prompt.py` — 根据用户反馈优化 prompt.yaml 中的判断标准

### 使用方式

```bash
python3 optimize_prompt.py
```

### 前置条件

- `feedback_daemon.py` 正在运行
- push_history.jsonl 中有至少 5 条有反馈的记录

### 流程

1. 读取 push_history.jsonl 中有 feedback 的记录
2. 分析反馈模式（假阳性/真阳性/假阴性）
3. 调用 LLM 生成优化后的 prompt
4. 发送飞书交互式卡片，展示变更摘要（3-5 条要点）
5. 用户确认 → feedback_daemon 写入 prompt.yaml（带备份 prompt.yaml.bak）
6. 用户放弃 → 不修改

## 召回机制

`recall_topics.py` — 从被排除的话题中召回用户可能感兴趣的内容

### 使用方式

```bash
python3 recall_topics.py
```

### 前置条件

- `feedback_daemon.py` 正在运行
- push_history.jsonl 中当天至少有 5 条被排除的话题

### 流程

1. 读取当天 pushed=false 的话题（去重）
2. LLM 从中选出用户可能感兴趣的（数量与已推送相当）
3. 发送飞书交互式卡片，LLM 选中的话题前加 🔹 标记
4. 用户反馈由 feedback_daemon 处理，更新 feedback 字段

## Pitfalls（经验教训）
- **不能用话题名称匹配 LLM 输出**：LLM 返回时可能省略 `#`、截断或改写话题名，导致匹配失败 → **必须用序号**
- **空结果提前返回**：预筛选后无话题时跳过 LLM 调用，节省约 48 秒
- **使用 logging 模块**：结构化日志输出，支持 DEBUG/INFO/WARNING/ERROR 级别
- **容器内 DNS 不稳定**：requests 的 urllib 在 Docker 里 DNS 解析失败 → **Feishu 推送用 curl_cffi Session(impersonate="chrome131")**
- **LLM 返回 content=None**：API 异常时 message.content 为 None，不做容错会 crash → **加 if not content: fallback**
- **LLM 调用无超时**：endpoint 不可达时 `requests` 会挂起数分钟 → **始终给 HTTP 调用设 timeout**
- **微博 API 需要 Referer header**：不带 `Referer: https://weibo.com` 则返回 0 条结果（空列表），不是网络问题。见 `references/weibo-api-header.md`
- **LLM 响应慢**：Qwen3.6-35B-A3B-C 通过本地 endpoint 评估 40 条话题需 ~48 秒。Cron 任务应设足够超时（脚本内 180s），避免长时间阻塞。见 `references/weibo-script-timing.md`
- **配置合并顺序**：本地 config.yaml 优先于全局 `~/.hermes/config.yaml`，全局配置仅补充本地未定义的字段
- **push_history.jsonl 并发安全**：run.py 只追加（append-only），feedback_daemon.py 更新 feedback 时使用 fcntl 文件锁
