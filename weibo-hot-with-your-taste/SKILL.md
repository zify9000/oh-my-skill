---
name: weibo-hot-with-your-taste
description: 抓取微博热榜，根据用户偏好定制化筛选热点话题（预设为政治/经济/科技类），通过飞书向用户推送。支持用户通过对话即时反馈喜欢/不喜欢的话题，支持开展偏好调研，支持根据用户反馈自优化筛选标准和规则，以更好地匹配用户口味。关键词：微博热点/个性化推荐/用户反馈/自优化
category: rss
---

# 微博热榜追踪

## 目录结构

```
weibo-hot-with-your-taste/
├── SKILL.md                  # Skill 说明文档
├── scripts/
│   ├── push.py               # 主脚本：抓取 → 规则过滤 → 规则反写 → LLM核校 → 推送
│   ├── survey.py             # 调研脚本：LLM 从未推送话题中召回候选
│   ├── feedback.py           # 反馈记录：将用户反馈写入 tasted_topics.jsonl
│   ├── config/
│   │   ├── base.yaml         # 基础配置（LLM参数、飞书webhook、性能开关）
│   │   ├── rule.yaml         # 规则配置（category_exclude分类排除、keyword_recall关键词反写）
│   │   └── prompt.yaml       # LLM 判断 prompt 模板
│   ├── data/
│   │   ├── category.json     # 分类词库（自动维护，记录微博API返回的category）
│   │   ├── all_topics.jsonl  # 原始全量抓取数据（每次push追加）
│   │   ├── pushed_topics.jsonl # 已推送新闻记录
│   │   └── tasted_topics.jsonl # 用户品味档案（反馈+调研结果合并）
│   ├── log/                  # 运行日志（按日滚动）
│   └── self-optimization/
│       ├── optimize_prompt.py  # Prompt优化：根据品味数据优化判断标准
│       └── optimize_rules.py   # 规则优化：发现未归类分类，LLM预判归属
└── references/               # 参考文档
    └── weibo-api-header.md   # 微博 API Header 要求
```

## 核心脚本

| 脚本 | 触发方式 | 职责 |
|------|---------|------|
| `push.py` | agent 调用 | 抓取 → 规则过滤 → 反写 → LLM核校 → 推送飞书 |
| `feedback.py` | agent 调用 | 接收 --word/--liked 参数，写入 tasted_topics.jsonl |
| `survey.py` | agent 调用 | 计算差集，LLM 召回候选，输出 JSON 到 stdout |
| `optimize_prompt.py` | agent 调用 | 分析 tasted_topics.jsonl → LLM 优化 prompt，输出 diff |
| `optimize_rules.py` | agent 调用 | 发现 category.json 未归类分类 → LLM 预判归属，输出建议 |

## 业务流

### 1. 推送流（push.py）

```
抓取微博热榜 → 规则过滤(category字段匹配category_exclude) → 规则反写(word字段匹配keyword_recall) → LLM核校 → 推送飞书卡片
        ↓                    ↓                                                                          ↓
   all_topics.jsonl       category.json                                                        pushed_topics.jsonl
```

飞书卡片末尾自带提示："💬 回复序号评价本次推送，如\"1,3感兴趣\""

### 2. 反馈流（对话触发）

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

### 3. 调研流（对话触发）

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

### 4. Prompt 优化流（对话触发）

**触发词**：`优化prompt` / `prompt优化` / `优化判断标准`

**工作流**：

1. 执行 `python3 scripts/self-optimization/optimize_prompt.py`，获取 JSON
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

### 5. 规则优化流（对话触发）

**触发词**：`优化规则` / `规则优化` / `检查分类规则`

**工作流**：

1. 执行 `python3 scripts/self-optimization/optimize_rules.py`，获取 JSON
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

## 配置更新原则

- 修改 `prompt.yaml` 或 `rule.yaml` 前必须备份（`.bak` 后缀）
- 备份命令：`cp scripts/config/rule.yaml scripts/config/rule.yaml.bak`
- 更新后告知用户备份位置

## 数据文件说明

### tasted_topics.jsonl

用户品味档案，由 `feedback.py` 唯一写入。

```json
{"ts": "2026-05-18T10:00:00", "word": "#小米YU7#", "liked": true, "category": "科技", "recorded_at": "2026-05-18T10:05:00"}
```

### pushed_topics.jsonl

每次推送的记录。

```json
{"ts": "2026-05-18T10:00:00", "total_fetched": 50, "topics": [{"word": "...", "category": "..."}]}
```

## 运行日志

日志按日写入 `scripts/log/` 目录，同时输出到 stderr：

- `feedback_YYYYMMDD.log` — 反馈记录日志
- `push_YYYYMMDD.log` — 推送流日志
- `survey_YYYYMMDD.log` — 调研流日志
- `optimize_prompt_YYYYMMDD.log` — Prompt 优化日志
- `optimize_rules_YYYYMMDD.log` — 规则优化日志
