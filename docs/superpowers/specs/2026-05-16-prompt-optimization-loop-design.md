# Prompt 优化闭环设计

## 背景

run.py 每次运行调用 LLM 判断热点新闻重要性，但 prompt 是硬编码的，无法根据用户实际偏好迭代优化。需要建立"推送 → 反馈 → 优化"的闭环。

## 核心重构：删除 noteworthy，三级变二级

旧三级体系（critical/noteworthy/excluded）中，"重要"与"要闻"的边界模糊，重构为二元体系：

| 旧三级 | 新二级 | 行为 |
|--------|--------|------|
| critical（重点）| important（重要）| 推送飞书 + 收集反馈 |
| noteworthy（要闻）| ❌ 删除 | — |
| skip（排除）| excluded（排除）| 不推送 |

- config.yaml 中 `star_keywords.noteworthy` 的关键词迁移到 `important` 或 `exclude`，具体归属在实施时确定
- LLM prompt 改为二元判断（重要/不重要）
- `classify_priority` 返回布尔值
- 报告只分"重要"一档
- 飞书卡片只推 important 话题

## 子项目分解

### 子项目 A：二元重构 + 推送记录增强 + 反馈闭环

**改动范围：**

1. run.py 重构（删除 noteworthy + 增强记录 + 发送反馈卡片 + prompt 外置到 prompt.yaml）
2. 新增 prompt.yaml（从 run.py 硬编码提取）
3. 新增 push_history.jsonl（推送历史，含全部话题）
4. 新增 feedback_daemon.py（飞书回调守护进程）
5. optimize_rules.py 去长连接（改为 feedback_daemon 处理回调）
6. config.yaml 重构（删除 noteworthy，关键词迁移）

### 子项目 B：Prompt 优化 + 召回

**改动范围：**

1. 新增 optimize_prompt.py（根据反馈优化 prompt）
2. 新增 recall_topics.py（召回被排除的话题，收集假阴性信号）
3. feedback_daemon.py 新增这两种回调处理

子项目 A 完成后系统即可运行，子项目 B 在积累了足够反馈数据后再实施。

---

## 子项目 A 详细设计

### A1：推送记录增强

**改动：** 修改 `run.py` 的 `save_report` 和 `main`

新增 `push_history.jsonl`，每次运行追加一条记录：

```json
{
  "ts": "2026-05-16T10:30:00",
  "topics": [
    {"word": "芯片禁令升级", "category": "国内时政", "pushed": true, "feedback": null},
    {"word": "iPhone降价", "category": "数码", "pushed": false, "feedback": null},
    {"word": "某综艺热搜", "category": "综艺", "pushed": false, "feedback": null}
  ],
  "total_fetched": 52
}
```

- `pushed: true` → important 话题，推送时收集反馈
- `pushed: false` → 被排除的话题，召回时 LLM 从中筛选
- `feedback` 初始为 `null`，用户打分后更新为 `1`（感兴趣）或 `0`（不感兴趣）
- 保留原有 `skill_result/` 目录不变

**并发安全：** run.py 只追加（append-only），feedback_daemon.py 负责更新 feedback 字段（读全部行 → 修改 → 写回），使用 `fcntl.flock` 文件锁防止并发冲突。

### A2：即时反馈收集

**改动：** 修改 `run.py`（发送反馈卡片）+ 新增 `feedback_daemon.py`（长连接守护进程）

**run.py 改动：**

推送 important 卡片后，追加发送一张反馈卡片：

```
┌──────────────────────────────────────────┐
│ 📝 推送反馈 · 请评价以下话题是否值得关注    │
├──────────────────────────────────────────┤
│ 芯片禁令升级                              │
│   [👍 感兴趣] [👎 不感兴趣]               │
├──────────────────────────────────────────┤
│ 美联储利率决议                             │
│   [👍 感兴趣] [👎 不感兴趣]               │
├──────────────────────────────────────────┤
│            [⏭️ 跳过全部]                  │
└──────────────────────────────────────────┘
```

- 卡片按钮 value 包含 `source: "feedback"`、`ts`（记录时间戳）、`word`（话题名）
- run.py 发送后即退出，不等待回调

**feedback_daemon.py：**

常驻进程，通过飞书长连接监听所有卡片回调。根据 `source` 字段分发到对应处理函数（内联处理）：

1. `source: "feedback"` → 更新 push_history.jsonl 中对应记录的 feedback 字段
2. `source: "optimize_rules"` → 更新 config.yaml（内联 `apply_choices_to_config` 逻辑）
3. `source: "optimize_prompt"` → 更新 prompt.yaml（子项目 B 实现）

回调处理逻辑：
- 👍 感兴趣 → feedback = 1
- 👎 不感兴趣 → feedback = 0
- ⏭️ 跳过全部 → 该批次所有未评价的 topic 的 feedback 保持 null

**并发会话隔离：** 卡片 value 包含 `session_id`（时间戳），feedback_daemon.py 按 session_id 维护各自的 choices 状态。内存中用 `dict[session_id, choices]` 存储。

**状态持久化：** 活跃会话状态持久化到 `daemon_state.json`，每次状态变更时写入。daemon 启动时加载。结构：

```json
{
  "sessions": {
    "2026-05-16T10:30:00": {
      "type": "optimize_rules",
      "choices": {"辟谣/通报": "important", "电影": "excluded"},
      "message_id": "om_xxx"
    }
  }
}
```

确认提交后删除对应 session。

### A3：optimize_rules.py 改动

去掉长连接逻辑，只负责：
1. 发现未归类分类 + LLM 预判
2. 发送飞书交互式卡片（按钮从 4 个变 3 个：排除/重要/跳过）
3. 退出

卡片 value 包含 `source: "optimize_rules"` 和 `session_id`。

feedback_daemon.py 收到回调后，内联处理 config.yaml 写入。

### A4：prompt.yaml 外置

**新增 `prompt.yaml`：**

```yaml
judge_prompt: |
  你是一个新闻重要性评估专家。请判断以下微博热搜话题是否值得推送。

  === 判断标准（严格按此执行） ===

  【重要】范围：
  - AI大模型/大模型：GPT、ChatGPT、Claude、Gemini、DeepSeek、豆包、千问/Qwen、通义、文心、ERNIE、智谱ChatGLM 等模型发布/突破/争议
  - 芯片：芯片禁令、代工突破、封装创新、设备进展
  ...

  【不重要】范围：
  - 影视综艺：演唱会、电影播出、综艺热点
  ...

  === 输出格式 ===
  严格按顺序输出，每行格式："序号:【重要】"或"序号:【不重要】"
```

**run.py 改动：**

- `call_llm_judge()` 中的 prompt 从 `prompt.yaml` 读取，不再硬编码
- prompt.yaml 必须存在，否则报错退出

## 子项目 B 详细设计

### B1：Prompt 优化

**新增 `optimize_prompt.py`：**

手动触发的脚本，流程：

```
1. 读取 push_history.jsonl 中有 feedback 的记录
2. 分析模式：
   - feedback=0, pushed=true → 用户不感兴趣但被推送 → prompt 过度敏感（假阳性）
   - feedback=1, pushed=true → 用户感兴趣 → prompt 正确识别
   - feedback=1, pushed=false → 用户感兴趣但被排除 → prompt 遗漏（假阴性，来自召回）
3. 调用 LLM 生成优化后的 prompt
4. 发送飞书交互式卡片，展示变更摘要（3-5 条要点）
5. 用户确认 → 写入 prompt.yaml（带备份 prompt.yaml.bak）
```

**飞书卡片展示变更摘要（非完整 diff）：**

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

完整的新旧 prompt 对比在终端日志中打印。

**单向优化可接受：** 如果用户从未运行 recall_topics.py，optimize_prompt.py 只有假阳性信号，优化方向是单向的（只收紧不放宽）。这是可接受的——先用假阳性收紧，等召回后获得假阴性信号再放宽。

### B2：召回机制

**新增 `recall_topics.py`：**

手动触发的脚本，流程：

```
1. 读取 push_history.jsonl 中当天 pushed=false 的话题（去重）
2. 统计：全集 N 条，已推送 M 条，从 N-M 条中让 LLM 选出用户可能感兴趣的
3. 发送飞书交互式卡片，每条话题带 👍/👎 按钮
4. 用户反馈由 feedback_daemon.py 处理，更新 pushed=false 记录的 feedback 字段
```

## 文件结构

```
weibo-hot-tracker/
├── run.py                # 主脚本（修改：二元重构 + 增强记录 + 反馈卡片 + prompt 外置）
├── optimize_rules.py     # 规则优化脚本（修改：去长连接，按钮变 3 个）
├── optimize_prompt.py    # 新增：prompt 优化脚本（子项目 B）
├── recall_topics.py      # 新增：召回脚本（子项目 B）
├── feedback_daemon.py    # 新增：飞书回调守护进程
├── prompt.yaml           # 新增：LLM 判断 prompt
├── push_history.jsonl    # 新增：推送历史记录
├── daemon_state.json     # 新增：daemon 会话状态持久化
├── config.yaml           # 配置（重构：删除 noteworthy）
├── keyword.json          # 关键词库
└── skill_result/         # 原有报告目录
```

## 依赖

- `lark-oapi`：飞书长连接 SDK（feedback_daemon.py 使用）
- `openai`：LLM 调用
- `pyyaml`：读写 prompt.yaml 和 config.yaml

## 不在范围内

- 自动定期运行 optimize_prompt.py 或 recall_topics.py（手动触发即可）
- 修改 LLM 的输出格式
- A/B 测试不同 prompt 版本
