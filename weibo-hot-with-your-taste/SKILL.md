---
name: weibo-hot-with-your-taste
description: 抓取微博热榜，根据用户偏好定制化筛选热点话题，通过飞书向用户推送。支持初始化配置用户偏好，支持即时反馈推送话题，支持迭代调研用户偏好，支持筛选过滤特征规则的自优化。关键词：微博热点/个性化推荐/基于反馈的自优化
---

# 微博热榜追踪

## 使用指南
1. 直接扔到hermes等agent框架的skills目录；
2. agent配置凭证，如“配置微博热点skill”,按照提示完成初始化配置（提供LLM和飞书凭据，扫码登陆微博，配置用户偏好形成特征规则）；
3. agent配置定时任务，如每小时执行一次爬取，12点05，18点05各执行一次推送（推荐使用no_agent模式）。
4. 对话agent触发式调用，如“推送微博热点”，“进行一轮微博热点偏好调研”

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
│   ├── init/
│   │   ├── feature.py        # 偏好初始化：设置领域关键词→设置喜欢/不喜欢的话题类型→设置召回关键词→生成特征规则
│   │   ├── llm_feishu.py      # LLM/飞书凭据配置：写入 .llm.env / .feishu.env
│   │   ├── weibo_get_qr.py     # 微博登录步骤1：获取二维码，浏览器保持运行
│   │   └── weibo_wait_login.py  # 微博登录步骤2：等待扫码，保存 Cookie
│   ├── env/
│   │   ├── .llm.env          # LLM 配置（llm_model / llm_base_url / llm_api_key）
│   │   ├── .llm.env.example
│   │   ├── .feishu.env       # 飞书应用凭据
│   │   ├── .feishu.env.example
│   │   ├── .weibo.env        # 微博 Cookie（weibo_sub + weibo_cookies_json）
│   │   └── .weibo.env.example
│   ├── config/
│   │   ├── base.yaml         # 基础配置（LLM参数、飞书重试策略）
│   │   ├── rule.yaml         # 规则配置（category_exclude分类排除、recall_keywords召回关键词）
│   │   ├── prompt.yaml       # LLM prompt 模板（judge_prompt + second_filter_prompt）
│   │   └── .initialized      # 初始化标记（存在即已初始化）
│   ├── data/
│   │   ├── topic_category.json     # 分类词库（自动维护，记录微博API返回的category）
│   │   ├── all_topics.jsonl  # 原始全量抓取数据（每次fetch追加）
│   │   ├── ruleChecked_topics.jsonl # 规则过滤后候选（每次fetch追加，LLM核校前）
│   │   ├── cached_fetch_meta.jsonl # 缓存元信息（每轮抓取的LLM状态和统计）
│   │   ├── cached_fetch_topics.jsonl # 缓存池（fetch写入话题，push后清空）
│   │   ├── pushed_topics.jsonl # 已推送新闻记录
│   │   ├── tasted_topics.jsonl # 用户品味档案（反馈+调研结果合并）
│   ├── log/                  # 运行日志（按日滚动，保留7天）
│   └── self-optimization/
│       ├── prompt.py  # Prompt优化：根据品味数据优化判断标准
│       └── rule.py   # 规则优化：发现未归类分类，LLM预判归属
└── references/               # 参考文档
    ├── weibo-api-header.md   # 微博公开 API 的 Header 要求
    └── weibo-auth.md         # 微博登录态与 API 认证机制
```

## 核心脚本

| 脚本 | 职责 |
|------|------|
| `init/llm_feishu.py` | 将 LLM/飞书凭据写入 `.llm.env` / `.feishu.env` |
| `init/feature.py` | 偏好初始化 | rule.yaml + prompt.yaml |
| `init/weibo_get_qr.py` | 微博登录步骤1：启动 Chromium headless，获取二维码，浏览器保持运行 |
| `init/weibo_wait_login.py` | 微博登录步骤2：连接已有浏览器，等待扫码，保存 Cookie 到 `.weibo.env` |
| `fetch.py` | 抓取微博热榜 → 规则过滤 → 规则召回 → LLM核校 → 写入 `cached_fetch_meta.jsonl` + `cached_fetch_topics.jsonl`。LLM 成功时仅缓存 important 话题；LLM 失败时候选存入 meta，push 阶段补跑 judge |
| `push.py` | 读 meta + topics → 按 word 去重 → 任一 cycle 为 LLM failed 时补跑 judge → 飞书卡片推送。推送后清空两个缓存文件 |
| `feedback.py` | 接收 --word/--liked 参数，写入 tasted_topics.jsonl |
| `survey.py` | 计算差集，LLM 召回候选，输出 JSON 到 stdout |
| `prompt.py` | 分析 tasted_topics.jsonl → LLM 优化 prompt，输出 diff |
| `rule.py` | 发现 topic_category.json 未归类分类 → LLM 预判归属，输出建议 |

## 业务流

### 0. 初始化

#### 1. **凭据配置**

凭据文件统一在 `scripts/env/`：

| 文件 | 内容 |
|------|------|
| `.llm.env` | `llm_model` / `llm_base_url` / `llm_api_key` |
| `.feishu.env` | `feishu_app_id` / `feishu_app_secret` / `feishu_chat_id` |
| `.weibo.env` | 微博 Cookie（由 `init/weibo_get_qr.py` + `init/weibo_wait_login.py` 生成，含 SUB + cookies_json） |

agent 首次使用时应检查这 3 个文件是否存在，对缺失的逐一询问配置。

> **⚠️ agent 注意**：
1. `.llm.env`、`.feishu.env`、`.weibo.env` 是以 `.` 开头的隐藏文件。部分工具的 glob 匹配（如 `search_files(pattern='.llm.env')`）对隐藏文件支持有缺陷，可能返回假阴性。**用 `ls -la scripts/env/` 或直接 `Read` 目标路径确认**，不要单独依赖 glob 搜索结果。
2. Hermes 等agent框架会拦截终端中出现的 key 明文。**不要直接在终端 echo/cat/粘贴 key**，应写 Python 脚本从 Hermes 配置（`config.yaml`、环境变量）读取实际值后调用 `write_file` 写入 `.llm.env` / `.feishu.env`，避免 key 泄露到终端历史。


**LLM / 飞书配置**：

```
python3 scripts/init/llm_feishu.py \
  --llm-model <模型名> --llm-base-url <API地址> --llm-api-key <API密钥> \
  --feishu-app-id <app_id> --feishu-app-secret <secret> --feishu-chat-id <chat_id>
```
可只传 LLM 参数、只传飞书参数，或同时传入两者。

**微博 Cookie 配置**：

```
# 步骤1：获取二维码
python3 scripts/init/weibo_get_qr.py

# agent 读取 /tmp/weibo_login_qr.png 展示给用户扫码

# 步骤2：等待扫码并保存 Cookie
python3 scripts/init/weibo_wait_login.py
```

**直接执行上述命令，不要自己重写登录逻辑。** 步骤1以 headless 模式启动 Chromium，QR 图片保存至 `/tmp/weibo_login_qr.png`，浏览器进程保持运行。步骤2连接已有浏览器等待扫码完成，登录后保存 Cookie 至 `.weibo.env` 并关闭浏览器。

Cookie 过期后 fetch 阶段日志会输出警告，重新执行上述命令即可。

#### 2. **筛选特征配置**：

1. 检查 `scripts/config/.initialized` 是否存在
2. 不存在 → 提示用户："首次使用，需要设置你的偏好。请提供2-5个你最关注的领域关键词，如：科技、经济、国际时政"
3. 用户提供领域关键词 → 执行 `python3 scripts/init/feature.py domain-keywords --domain-kw "关键词1" "关键词2" ...`
4. 获取 JSON，展示 猜你喜欢/猜你不喜欢 的话题类型选项，提示各选至少5个
5. 用户选择后，询问："是否需要添加召回关键词？当话题属于用户不喜欢的话题类型但包含召回关键词时，会被救回。例如：'AI'、'芯片'。直接回复关键词即可，多个用逗号分隔。回复'不需要'跳过。"
6. 执行 `python3 scripts/init/feature.py choices --domain-kw "..." --liked "..." --disliked "..." --recall "..."`
7. 获取 JSON（`status: "pending_confirm"`），将 LLM 生成的 `yes_criteria` 和 `no_criteria` 展示给用户确认：

   ```
   **判断标准已生成，请确认：**

   **重要（yes）：**
   {yes_criteria 内容}

   **不重要（no）：**
   {no_criteria 内容}

   确认无误？你可以直接确认，或提出修改意见（如"yes里加上'涉及AI监管'，no里删掉'娱乐八卦'"）。
   ```

8. 用户确认或提出修改 → 将最终版本保存为 JSON 文件（与 choices 输出结构一致，替换用户修改的 criteria），执行：
   ```
   python3 scripts/init/feature.py confirm --file /tmp/feature_confirm.json
   ```
9. 初始化完成

**重新初始化：** 删除 `scripts/config/.initialized` 后再次使用即可

### 1. 抓取与推送

**抓取和推送解耦为两个独立脚本**，一天中可多次抓取，一次性推送：

```
fetch.py:  抓取微博热榜 → 规则过滤 → 写入 ruleChecked_topics.jsonl → 短话题摘要补充 → LLM核校
                ↓              ↓                                       │
           all_topics.jsonl  topic_category.json                       │
                                                                       │ LLM成功 → important → cached_fetch_topics.jsonl
                                                                       │ LLM失败 → candidates 存 meta，push 补跑 judge
                                                                       ▼
push.py:   读 meta + topics → 按 word 去重 → (多次抓取/有failed) LLM 二次精选 → 飞书卡片 → 清空缓存
                                                                                     ↓
                                                                              pushed_topics.jsonl
                                                                 (仅一次且全部ok) 跳过二次过滤
```

**跨周期去重**：推送时会识别当天已推送过的热点，将其放入卡片折叠区「今日已推送热点」展示，并标注热度变化（上涨红色↑，下跌绿色↓，变化阈值10%）。

**全重复处理**：如果本次推送候选全部为当天已推送热点，则发送文本消息「当前时段无新增微博热点」。

**短话题摘要补充**：规则过滤后，对 ≤5 字的短话题名（如"A股"），调用微博详情 API 获取热度最高的 10 条微博，LLM 生成 20 字以内摘要，在卡片中作为副行展示。

**⚠️ 推送必须通过 `push.py` 完成，禁止使用 `send_message` 等工具替代。** `push.py` 发送的是飞书卡片消息（带红色标题栏、分类标签、序号），不是纯文本。

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
5. 逐条调用 `python3 scripts/feedback.py --word "话题名" --liked true/false --ts "推送时间戳" --comment "用户原话"`
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

