# Prompt 优化 + 召回 设计（子项目 B）

## 前置依赖

子项目 A（二元重构 + 推送记录增强 + 反馈闭环）已完成，系统具备：
- 二元判断体系（important / excluded）
- `push_history.jsonl` 记录全部话题（含 `pushed` 和 `feedback` 字段）
- `feedback_daemon.py` 长连接守护进程（处理反馈回调）
- `prompt.yaml` 外置 prompt

## B1：Prompt 优化

### 新增 `optimize_prompt.py`

手动触发的脚本，根据用户反馈优化 `prompt.yaml` 中的判断标准。

### 流程

```
1. 读取 prompt.yaml 中的当前 prompt
2. 读取 push_history.jsonl 中有 feedback 的记录
3. 分析反馈模式：
   - feedback=0, pushed=true → 假阳性（用户不感兴趣但被推送）→ prompt 应收紧
   - feedback=1, pushed=true → 真阳性（用户感兴趣）→ prompt 正确识别
   - feedback=1, pushed=false → 假阴性（用户感兴趣但被排除，来自召回）→ prompt 应放宽
4. 调用 LLM 生成优化后的 prompt
5. 发送飞书交互式卡片，展示变更摘要
6. 用户确认 → 写入 prompt.yaml（带备份 prompt.yaml.bak）
7. 用户放弃 → 不修改
```

### LLM 优化 prompt 的输入

```
当前 prompt:
{current_prompt}

用户反馈数据（最近 N 条有反馈的记录）:

假阳性（被推送但用户不感兴趣）：
- "芯片禁令升级" (国内时政) → 👎
- "iPhone降价" (数码) → 👎

真阳性（被推送且用户感兴趣）：
- "美联储利率决议" (财经) → 👍
- "黄仁勋访华" (互联网) → 👍

假阴性（被排除但用户感兴趣）：
- "SpaceX星舰发射" (科技) → 👍

请根据用户反馈优化判断标准，使 prompt 更准确地匹配用户偏好。
只修改判断标准部分，不改变输出格式。
```

### 飞书卡片

展示变更摘要（3-5 条要点），非完整 diff：

```
┌──────────────────────────────────────────┐
│ 📋 Prompt 优化建议                        │
├──────────────────────────────────────────┤
│ 变更摘要：                                │
│ 1. 【新增重点】新增"数据安全/隐私泄露"     │
│ 2. 【收紧排除】将"航天/探月"降为排除       │
│ 3. 【细化排除】明确"演唱会"属于排除范围     │
├──────────────────────────────────────────┤
│        [✅ 确认更新] [❌ 放弃]             │
└──────────────────────────────────────────┘
```

- 完整的新旧 prompt 对比在终端日志中打印
- 卡片按钮 value 包含 `source: "optimize_prompt"` 和 `session_id`
- 用户确认后，feedback_daemon.py 处理回调，写入 prompt.yaml

### 关键约束

- 手动触发，不是 cron
- 优化只修改判断标准部分，不改变输出格式（序号:【重要】/【不重要】）
- 写入前备份 `prompt.yaml` 为 `prompt.yaml.bak`
- 单向优化可接受：如果用户从未运行 recall_topics.py，只有假阳性信号，优化方向是单向的（只收紧不放宽）。先用假阳性收紧，等召回后获得假阴性信号再放宽
- 反馈数据不足时（如少于 5 条有反馈的记录），提示用户先积累数据，不调用 LLM

### feedback_daemon.py 新增回调

收到 `source: "optimize_prompt"` 的回调时：
- `action: "confirm"` → 读取 daemon_state.json 中的新 prompt 内容，写入 prompt.yaml，更新卡片为"已完成"
- `action: "reject"` → 不修改，更新卡片为"已放弃"

daemon_state.json 中存储的 optimize_prompt 会话结构：

```json
{
  "sessions": {
    "2026-05-16T14:00:00": {
      "type": "optimize_prompt",
      "new_prompt": "优化后的 prompt 完整文本...",
      "message_id": "om_xxx"
    }
  }
}
```

---

## B2：召回机制

### 新增 `recall_topics.py`

手动触发的脚本，从被排除的话题中召回用户可能感兴趣的内容，收集假阴性信号。

### 流程

```
1. 读取 push_history.jsonl 中当天 pushed=false 的话题（去重）
2. 如果没有未评价的被排除话题，退出
3. 统计：全集 N 条，已推送 M 条
4. 从 N-M 条中让 LLM 选出用户可能感兴趣的（数量与 M 相当）
5. 发送飞书交互式卡片，每条话题带 👍/👎 按钮
6. 用户反馈由 feedback_daemon.py 处理，更新 pushed=false 记录的 feedback 字段
```

### LLM 召回 prompt

```
你是一个新闻重要性评估专家。以下微博热搜话题之前被判断为"不重要"而未推送。
请从中选出用户可能感兴趣的话题，数量约 {target_count} 条。

当前判断标准：
{current_prompt_criteria}

=== 被排除的话题列表 ===
{excluded_topics_text}

=== 输出格式 ===
每行格式："序号:选/不选"，严格按序号输出：
1:选
2:不选
3:选
...

必须包含全部 {len(excluded_topics)} 条话题的判断。
```

### 飞书卡片

```
┌──────────────────────────────────────────┐
│ 🔍 召回反馈 · 以下话题之前被排除           │
│ LLM 认为您可能对标记的话题感兴趣           │
├──────────────────────────────────────────┤
│ 🔹 SpaceX星舰发射 ✓                      │
│   [👍 感兴趣] [👎 不感兴趣]               │
├──────────────────────────────────────────┤
│ 某综艺热搜                               │
│   [👍 感兴趣] [👎 不感兴趣]               │
├──────────────────────────────────────────┤
│            [⏭️ 跳过全部]                  │
└──────────────────────────────────────────┘
```

- LLM 选中的话题前加 🔹 标记和 ✓
- 卡片按钮 value 包含 `source: "recall"`、`ts`、`word`
- 用户反馈由 feedback_daemon.py 处理，更新 push_history.jsonl 中 `pushed=false` 记录的 `feedback` 字段

### feedback_daemon.py 新增回调

收到 `source: "recall"` 的回调时：
- 👍 感兴趣 → 更新对应 pushed=false 记录的 feedback = 1
- 👎 不感兴趣 → 更新对应 pushed=false 记录的 feedback = 0
- ⏭️ 跳过全部 → 该批次所有未评价的 topic 的 feedback 保持 null

### 关键约束

- 手动触发，不是 cron
- 只读取当天的数据（按 ts 日期过滤）
- 被排除话题去重（同一话题可能在多次运行中出现，只保留最新一条）
- 如果当天被排除话题少于 5 条，提示数据不足，退出

---

## 文件变更

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `optimize_prompt.py` | 新增 | Prompt 优化脚本 |
| `recall_topics.py` | 新增 | 召回脚本 |
| `feedback_daemon.py` | 修改 | 新增 optimize_prompt 和 recall 两种回调处理 |

## 依赖

- `lark-oapi`：飞书长连接 SDK（feedback_daemon.py 已有）
- `openai`：LLM 调用（已有）
- `pyyaml`：读写 prompt.yaml（已有）

## 不在范围内

- 自动定期运行 optimize_prompt.py 或 recall_topics.py
- 修改 LLM 的输出格式
- A/B 测试不同 prompt 版本
- 召回历史数据（只召回当天的）
