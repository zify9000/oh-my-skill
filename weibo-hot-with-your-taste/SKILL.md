---
name: weibo-hot-with-your-taste
description: 抓取微博热榜，根据用户偏好定制化筛选热点话题（预设为政治/经济/科技类），通过飞书向用户推送。支持用户通过对话即时反馈喜欢/不喜欢的话题，支持开展偏好调研，支持根据用户反馈自优化筛选标准和规则，以更好地匹配用户口味。关键词：微博热点/个性化推荐/用户反馈/自优化
---

# 微博热榜追踪

## 目录结构

```
weibo-hot-with-your-taste/
├── SKILL.md                  # Skill 说明文档
├── scripts/
│   ├── common.py             # 公共工具：配置加载、日志、格式化
│   ├── fetch.py              # 抓取热点：抓取 → 规则过滤 → LLM核校 → 缓存
│   ├── push.py               # 推送热点：读缓存 → 去重 → LLM二次过滤 → 推送飞书卡片
│   ├── survey.py             # 反馈推送：LLM 从未推送话题中召回候选
│   ├── feedback.py           # 调研偏好：将用户反馈写入 tasted_topics.jsonl
│   ├── env/
│   │   ├── .llm.env          # LLM 配置（llm_model / llm_base_url / llm_api_key）
│   │   ├── .llm.env.example
│   │   ├── .feishu.env       # 飞书凭据（仅 feishu_credential_source=env 时需要）
│   │   └── .feishu.env.example
│   ├── config/
│   │   ├── base.yaml         # 基础配置（LLM参数、llm_credential_source、feishu_credential_source）
│   │   ├── rule.yaml         # 规则配置（category_exclude分类排除、keyword_recall关键词反写）
│   │   └── prompt.yaml       # LLM prompt 模板（judge_prompt + second_filter_prompt）
│   ├── data/
│   │   ├── topic_category.json     # 分类词库（自动维护，记录微博API返回的category）
│   │   ├── all_topics.jsonl  # 原始全量抓取数据（每次fetch追加）
│   │   ├── cached_topics.jsonl # 缓存池（fetch写入重要话题，push后清空）
│   │   ├── pushed_topics.jsonl # 已推送新闻记录
│   │   └── tasted_topics.jsonl # 用户品味档案（反馈+调研结果合并）
│   ├── log/                  # 运行日志（按日滚动）
│   └── self-optimization/
│       ├── prompt.py  # Prompt优化：根据品味数据优化判断标准
│       └── rule.py   # 规则优化：发现未归类分类，LLM预判归属
└── references/               # 参考文档
    └── weibo-api-header.md   # 微博 API Header 要求
```

## 核心脚本

| 脚本 | 职责 |
|------|------|
| `fetch.py` | 抓取微博热榜 → 规则过滤 → 反写 → LLM核校 → 缓存重要话题到 `cached_topics.jsonl` |
| `push.py` | 读缓存 → 按 word 去重 → LLM 二次精选 → 飞书卡片推送。推送后清空 cached_topics.jsonl 缓存。飞书凭据来源由 `feishu_credential_source` 控制（`env` / `agent`） |
| `feedback.py` | 接收 --word/--liked 参数，写入 tasted_topics.jsonl |
| `survey.py` | 计算差集，LLM 召回候选，输出 JSON 到 stdout |
| `prompt.py` | 分析 tasted_topics.jsonl → LLM 优化 prompt，输出 diff |
| `rule.py` | 发现 topic_category.json 未归类分类 → LLM 预判归属，输出建议 |

## 业务流

### 1. 抓取与推送

**抓取和推送解耦为两个独立脚本**，一天中可多次抓取，一次性推送：

```
fetch.py:  抓取微博热榜 → 规则过滤 → 规则反写 → LLM核校 → 缓存重要话题到 cached_topics.jsonl
                ↓              ↓
           all_topics.jsonl  topic_category.json

push.py:   读 cached_topics.jsonl → 按 word 去重 → (多次抓取) LLM 二次精选 → 飞书卡片 → 清空缓存
                                                              ↓
                                                       pushed_topics.jsonl
                                          (仅一次抓取) 跳过二次过滤，直接推送
```

**⚠️ 推送必须通过 `push.py` 完成，禁止使用 `send_message` 等工具替代。** `push.py` 发送的是飞书卡片消息（带红色标题栏、分类标签、序号），不是纯文本。

**agent 工作流（严格按顺序执行，禁止跳过）：**

```
步骤 1：读取 scripts/config/base.yaml，记录两个值：
         llm_credential_source = ______
         feishu_credential_source = ______

步骤 2：根据上面两个值，逐项构建参数：

         如果 llm_credential_source == "env"：
             → 检查 scripts/env/.llm.env 是否存在，不存在则报错退出
             → llm_args = ""（凭据自动从文件读取）

         如果 llm_credential_source == "agent"：
             → llm_args = "--llm-model <模型名> --llm-base-url <API地址> --llm-api-key <API密钥>"
             → 尖括号内替换为 agent 当前使用的 LLM 配置

         如果 feishu_credential_source == "env"：
             → 检查 scripts/env/.feishu.env 是否存在，不存在则报错退出
             → feishu_args = ""

         如果 feishu_credential_source == "agent"：
             → feishu_args = "--feishu-app-id <app_id> --feishu-app-secret <secret> --feishu-chat-id <chat_id>"
             → 尖括号内替换为 agent 当前使用的飞书凭据

步骤 3：运行 python3 scripts/fetch.py {llm_args}
         等待执行完成，检查是否成功写入 cached_topics.jsonl

步骤 4：运行 python3 scripts/push.py {llm_args} {feishu_args}
         等待执行完成

禁止行为：
- 禁止跳过步骤 1 直接运行脚本
- 禁止用 send_message 等工具替代 push.py
- 禁止在步骤 3 之前运行步骤 4
```

**凭据参数速查：**

| 来源 | LLM 参数 | 飞书参数 |
|------|---------|---------|
| `env` | 自动从 `scripts/env/.llm.env` 读取 | 自动从 `scripts/env/.feishu.env` 读取 |
| `agent` | `--llm-model` `--llm-base-url` `--llm-api-key` | `--feishu-app-id` `--feishu-app-secret` `--feishu-chat-id` |

### 2. 反馈

**触发词**：`推送反馈` / `反馈` / 直接给序号评价如 `1,3感兴趣` / `1和4不错，2不关心`

**工作流**：

1. 用户说"推送反馈"或直接给序号评价
2. 读取 `scripts/data/pushed_topics.jsonl`，取最后一条记录
3. 如果用户只说了"推送反馈"而未给评价 → 提示："本次推送共N条，请告诉我序号偏好，如'1,3感兴趣，2不感兴趣'"
4. 如果用户已给序号评价 → 解析自然语言，映射序号到话题：
   - `1,3感兴趣` → 话题1和3 liked=true
   - `2和5不感兴趣` → 话题2和5 liked=false
   - `1和2不错，其他一般` → 话题1和2 liked=true，其余 liked=false
5. 逐条调用 `python3 scripts/feedback.py --word "话题名" --liked true/false --ts "推送时间戳"`
6. 回复："已记录：👍 x2, 👎 x3"

**注意**：如果用户未明确表态的序号（如只说"1和3感兴趣"但共有8条），不要猜测，不记录未提及的条目。

### 3. 调研

**触发词**：`偏好调研` / `调研` / `有什么我可能错过的新闻`

**工作流**：

1. 执行 `python3 scripts/survey.py`，获取 JSON：
   - `ready=false` → 告诉用户数据不足（如"未推送新闻不足5条，无法调研"）
   - `ready=true` → 得到 `{candidates, total_unpushed, pushed_count}`
2. 按以下格式展示候选话题（LLM 推荐项用 🔹 标记）：

```
今天有 45 条未推送新闻，LLM 筛选出以下可能与你相关的：

1. #话题1#  `科技`
2. 🔹 #话题2#  `国内时政`
3. #话题3#  `财经`
4. 🔹 #话题4#  `互联网`
...

你对哪些感兴趣？例如："1和2感兴趣"
```

3. 用户回复后，解析自然语言，调用 `feedback.py` 逐条写入。未提及的条目不写入。

### 4. Prompt 优化

**触发词**：`优化prompt` / `prompt优化` / `优化判断标准`

**工作流**：

1. 执行 `python3 scripts/self-optimization/prompt.py`，获取 JSON
2. 如果 `ready=false`（反馈数据不足）→ 告知用户："反馈数据不足（当前N条，需至少M条），请先积累反馈"
3. 如果 `ready=true` → 展示：

```
**变更摘要：**
1. 【新增】xxx标准
2. 【删除】yyy
3. 【调整】zzz范围

**Diff:**
- 旧行内容
+ 新行内容

确认更新 prompt.yaml？（回复"可以"/"确认"/"算了"）
```

4. 用户确认 → 备份 `prompt.yaml` → 写入新 prompt → 告知"prompt.yaml 已更新，旧文件备份为 prompt.yaml.bak"
5. 用户拒绝 → 告知"已放弃，prompt 未修改"

### 5. 规则优化

**触发词**：`优化规则` / `规则优化` / `检查分类规则`

**工作流**：

1. 执行 `python3 scripts/self-optimization/rule.py`，获取 JSON
2. 如果 `ready=false` → 告知用户具体原因
3. 如果 `ready=true` → 展示：

```
发现 3 个新分类待处理：

1. 社会新闻 → LLM 推荐: 排除
2. 科普 → LLM 推荐: 重要
3. 设计 → LLM 推荐: 跳过

逐条确认或批量处理：回复如"社会新闻→排除，科普→重要，设计跳过"
```

4. 用户确认后 → 备份 `rule.yaml` → 更新 → 告知变更结果
5. 支持批量语法：
   - `全部按推荐` → 全部采用 LLM 推荐
   - `社会新闻→排除，其他按推荐` → 混合处理

## 运行日志

日志按日写入 `scripts/log/` 目录，同时输出到 stderr：

- `fetch_YYYYMMDD.log` — 抓取日志
- `push_YYYYMMDD.log` — 推送日志
- `feedback_YYYYMMDD.log` — 反馈记录日志
- `survey_YYYYMMDD.log` — 调研日志
- `prompt_YYYYMMDD.log` — Prompt 优化日志
- `rule_YYYYMMDD.log` — 规则优化日志
