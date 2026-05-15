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
├── weibo_hot_tracker.py  # 主脚本 (482 行)
└── references/           # 参考文档
    ├── weibo-api-header.md   # 微博 API Header 要求
    └── weibo-script-timing.md # LLM 性能分析
```

## 脚本路径
`weibo_hot_tracker.py` — 主脚本，可直接执行或通过 cron 调度

## 配置文件
`config.yaml` — 本地配置文件，优先级高于 `~/.hermes/config.yaml`

配置项：
- `llm.model`: LLM 模型名（默认 `Qwen3.6-35B-A3B`）
- `llm.max_tokens`: 最大输出 tokens（默认 2000）
- `llm.timeout`: LLM 调用超时秒数（默认 120）
- `llm.temperature`: LLM 温度参数（默认 0.1）
- `llm.base_url`: LLM API 地址（默认空，从环境变量 `MINIMAX_BASE_URL` 读取）
- `feishu.chat_id`: 飞书群聊 ID（可被 `FEISHU_HOME_CHANNEL` 环境变量覆盖）
- `feishu.retry_times`: 推送重试次数（默认 5）
- `feishu.retry_delay`: 重试初始延迟秒数（默认 5）
- `filter.exclude_categories`: 排除的话题分类列表
- `filter.star_keywords.level_3`: ★★★ 重点关键词
- `filter.star_keywords.level_2`: ★★ 要闻关键词
- `performance.empty_result_skip`: 空结果是否跳过 LLM 调用（默认 true）

环境变量：
- `WEIBO_TRACKER_LOG_LEVEL`: 日志级别（DEBUG/INFO/WARNING/ERROR，默认 INFO）

## API
`https://weibo.com/ajax/statuses/hot_band` — 无需登录，直接返回52条热榜

## 过滤逻辑（2026-04-24 重构：LLM 语义判断）

### 核心原则
**只推★★★重点，没有则不推送**（不发送任何消息）

### 判断标准（★★★ 重点）

**AI/技术**：AI大模型（GPT/Claude/DeepSeek/豆包/千问/Qwen/通义/文心/ERNIE/智谱）、芯片（禁令/代工突破/设备）、新能源（固态电池/钠电池/碳化硅/重磅电动车）

**军事与地缘**：军事冲突（战争/武装对峙/导弹袭击）、重大装备（航母/隐身战机/战略核潜艇/新型导弹）

**经济政策**：国内政策（国务院/部委重要文件）、宏观指标（GDP/CPI/PPI/PMI/进出口/失业率）、美联储（FOMC/鲍威尔/美元利率）、房地产（重磅调控/房企爆雷/政府救市）

### 输出格式（Feishu 卡片消息）
```
┌──────────────────────────────────────┐
│  🔴 📊 微博热榜 · YYYY年MM月DD日 HH:MM  │  ← 红色标题栏
├──────────────────────────────────────┤
│ 共 N 条★★★重点，政经科技类            │  ← bold 摘要
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

### 技术实现
- 预筛：过滤娱乐分类（明星/演出/电影/美食）及 rank=0 无效条目
- LLM 判断：从 config.yaml 的 `model.default` 读取模型名（默认 `Qwen3.6-35B-A3B`），按序号返回 `1:【重点】\n2:【要闻】...`
- 解析：按序号映射，非★★★话题直接丢弃
- LLM 失败：fallback 到旧版 category 规则，仍只推送★★★结果
- HTTP 客户端：Feishu 推送用 curl_cffi Session（DNS 稳定）

**HTTP 客户端**：Feishu 推送用 curl_cffi Session（DNS 更稳定），微博抓取用 requests

**Feishu 消息格式原则**：
- 飞书对 Markdown 表格渲染为空白 → 输出列表时不用表格，用单行 ` | ` 分隔各字段，或用卡片消息（msg_type=interactive）
- 飞书消息内容含独立空行 `\n\n` 会被当作消息分隔符 → 单行 + ` | ` 分隔各字段可避免拆包
- 卡片消息适合结构化内容：header（标题+颜色）+ elements（div/hr/link）

**API Key 模板变量**：`config.yaml` 里 `${CUSTOM_API_KEY}` 格式的 key 需要代码里手动解析环境变量展开，不能直接用 yaml 加载的值

**Cron 任务环境变量加载**：cron 任务启动的是独立 Python 进程，**不会自动加载 `.env` 文件**，导致 `${CUSTOM_API_KEY}` 展开后是空字符串。必须在脚本开头手动读取并设置环境变量：

```python
# 在 call_llm_judge() 函数或脚本入口处加这段
env_path = Path.home() / ".hermes" / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")
```

**Cron 任务 prompt 注意事项**：`2>/dev/null` 会吞掉错误信息，导致失败时无任何日志；脚本内 `print()` 输出应写到 stderr，避免被 cron 捕获时混淆

**⚠️ 常见坑**：
- API key 若是 `${ENV_VAR}` 模板变量（config.yaml 中），需要手动从环境变量读取展开，直接 yaml.load() 不会解析
- LLM 返回格式用序号匹配（`1:【重点】`）而非话题名匹配——话题名含 # 号时容易匹配失败
- max_tokens 需设 3000 以上，否则 40 条话题的判断结果会被截断
- MiniMax API base_url 优先从 config.yaml 读取 `model.base_url`，fallback 到 `http://192.168.124.18:13080`（注意 `host.docker.internal` 仅在 Docker 容器内有效）

## 报告格式
```
📊 微博热榜 YYYY年MM月DD日 HH:MM（政治/经济/科技类）
共 N 条相关话题

#排名 话题名称 [分类] 🔥热度
#1 #话题# [财经] 🔥131.7万
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

## Pitfalls（经验教训）
- **不能用话题名称匹配 LLM 输出**：LLM 返回时可能省略 `#`、截断或改写话题名，导致匹配失败 → **必须用序号**
- **max_tokens 已优化为 2000**：从 8192 降低，足够容纳 40 条话题的判断结果，减少 LLM 响应时间
- **空结果提前返回**：预筛选后无话题时跳过 LLM 调用，节省约 48 秒
- **使用 logging 模块**：结构化日志输出，支持 DEBUG/INFO/WARNING/ERROR 级别
- **容器内 DNS 不稳定**：requests 的 urllib 在 Docker 里 DNS 解析失败 → **Feishu 推送用 curl_cffi Session(impersonate="chrome131")**
- **LLM 返回 content=None**：API 异常时 message.content 为 None，不做容错会 crash → **加 if not content: fallback**
- **LLM 调用无超时**：endpoint 不可达时 `requests` 会挂起数分钟 → **始终给 HTTP 调用设 timeout**
- **微博 API 需要 Referer header**：不带 `Referer: https://weibo.com` 则返回 0 条结果（空列表），不是网络问题。见 `references/weibo-api-header.md`
- **LLM 响应慢**：Qwen3.6-35B-A3B 通过本地 endpoint 评估 40 条话题需 ~48 秒。Cron 任务应设足够超时（脚本内 120s），避免长时间阻塞。见 `references/weibo-script-timing.md`

