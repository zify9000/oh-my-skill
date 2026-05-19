# 触发式脚本重构设计

## 背景

当前 weibo-hot-news-to-my-taste skill 有两套入口：`run.py`（一次性抓取推送）和 `server.py`（常驻飞书回调服务）。agent 调用 `run.py` 后不会主动启动 `server.py`，导致用户点击推送卡片中的 👍/👎 按钮后回调丢失，反馈流、调研流、优化流全部瘫痪。

**根本原因**：常驻服务模型与 agent skill 的触发式用法不匹配。

## 目标

- 删除 `server.py`，所有交互改为 agent 对话触发
- Python 脚本退为纯数据处理层，输入输出结构化 JSON
- Agent（Claude）作为交互中枢：识别触发词 → 调脚本 → 展示结果 → 解析用户自然语言 → 调写入

## 文件变更

### 删除
- `scripts/server.py` — 常驻飞书回调服务
- `scripts/recall_topics.py` — 合并到 survey.py
- `scripts/session_state.json` — 不再需要会话状态

### 新增
- `scripts/feedback.py` — 小型 CLI，接收 `--word`/`--liked`/`--category`/`--ts` 参数，追加写入 tasted_topics.jsonl

### 修改
- `scripts/run.py` — 推送卡片去掉 👍/👎 按钮，末尾加反馈提示
- `scripts/survey.py` — 合并 recall_topics 逻辑，去掉飞书卡片发送，输出 JSON 到 stdout
- `scripts/self-optimization/optimize_prompt.py` — 去掉飞书卡片和 session_state，输出 JSON 到 stdout
- `scripts/self-optimization/optimize_rules.py` — 同上
- `SKILL.md` — 重写，添加四个触发流的工作流指令

### 不变
- 配置文件：`base.yaml`、`rule.yaml`、`prompt.yaml`
- 数据文件：`all_topics.jsonl`、`pushed_topics.jsonl`、`tasted_topics.jsonl`、`category.json`

## 四个触发流

### 1. 推送反馈

触发词：`推送反馈` / `反馈` / `1和3感兴趣` 等自然语言

流程：
1. 用户直接给序号评价（如"1,3感兴趣，2不感兴趣"）→ agent 直接解析
2. 用户只说"推送反馈" → agent 提示"本次推送共N条，请告诉我序号偏好"
3. Agent（Claude）解析自然语言，映射序号到话题
4. 逐条调用 `feedback.py --word ... --liked true/false` 写入 tasted_topics.jsonl
5. 反馈"已记录：👍 xN, 👎 xN"

### 2. 偏好调研

触发词：`偏好调研` / `调研`

流程：
1. Agent 执行 `python3 survey.py`，获取 JSON：`{candidates, total_unpushed, pushed_count}`
2. 展示候选话题（LLM 推荐项用 🔹 标记）
3. 用户自然语言评价
4. Agent 解析 → 调用 feedback.py 写入

### 3. Prompt 优化

触发词：`优化prompt` / `prompt优化`

流程：
1. Agent 执行 `python3 optimize_prompt.py`，获取 JSON：`{ready, new_prompt, change_summary, diff}`
2. 展示变更摘要和 diff
3. 用户确认/拒绝
4. Agent 写入 prompt.yaml 或丢弃

### 4. 规则优化

触发词：`优化规则` / `规则优化`

流程：
1. Agent 执行 `python3 optimize_rules.py`，获取 JSON：`{ready, unclassified, recommendations}`
2. 展示未归类分类及 LLM 推荐
3. 用户逐个或批量决定 exclude/star/skip
4. Agent 更新 rule.yaml

## 脚本契约

### run.py

推送卡片改为纯展示，末尾加提示：`💬 回复序号评价本次推送，如"1,3感兴趣"`

返回结构化结果（stdout JSON 或退出码）：
```json
{"success": true, "pushed_count": 5}
```

### survey.py

```bash
python3 survey.py
```
输出：
```json
{
  "candidates": [
    {"word": "#话题#", "category": "科技", "llm_recommended": true}
  ],
  "total_unpushed": 45,
  "pushed_count": 5
}
```

### feedback.py

```bash
python3 feedback.py --word "#话题#" --liked true --category "科技" --ts "2026-05-19T14:30:00"
```
输出：`{"status": "ok"}`

### optimize_prompt.py

```bash
python3 optimize_prompt.py
```
输出：
```json
{
  "ready": true,
  "current_prompt_preview": "...",
  "new_prompt": "...",
  "change_summary": ["新增：xxx", "删除：yyy"],
  "diff": "- old\n+ new",
  "total_feedback": 12
}
```
反馈不足时：`{"ready": false, "total_feedback": 3, "min_required": 5}`

### optimize_rules.py

```bash
python3 optimize_rules.py
```
输出：
```json
{
  "ready": true,
  "unclassified": ["社会新闻", "科普"],
  "recommendations": {"社会新闻": "exclude", "科普": "star"}
}
```
无未归类时：`{"ready": false, "message": "所有分类已归类"}`

## 推送卡片格式

```
┌──────────────────────────────────────┐
│  📊 微博热榜 · 2026年05月19日 14:30  │
├──────────────────────────────────────┤
│ 共 5 条重要新闻，政经科技类           │
├──────────────────────────────────────┤
│ 1. #话题标题#  `分类`  热度          │
│ 2. #话题标题#  `分类`  热度          │
├──────────────────────────────────────┤
│ 💬 回复序号评价本次推送，如"1,3感兴趣"│
│ 🕐 2026年05月19日 14:30              │
└──────────────────────────────────────┘
```

## 关键原则

- Agent 用自身 LLM 能力解析用户自然语言，不再额外调用 LLM 做解析
- tasted_topics.jsonl 写入入口统一为 feedback.py
- 配置文件更新（prompt.yaml / rule.yaml）由 agent 直接执行，备份旧文件
- 优化脚本需要 `min_feedback_count` 检查，数据不足时输出 ready=false
