# 触发式脚本重构 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除 server.py 常驻服务，所有交互改为 agent 对话触发，Python 脚本输出结构化 JSON。

**Architecture:** Python 脚本退为纯数据处理层（抓取、LLM 调用、文件读写），agent 做交互中枢（识别触发词、展示结果、解析自然语言、调用写入脚本）。tasted_topics.jsonl 写入入口统一为 feedback.py。

**Tech Stack:** Python 3, Flask (移除), curl_cffi, openai, PyYAML, fcntl

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `scripts/feedback.py` | **新建** | CLI：接收 --word/--liked/--category/--ts，追加写入 tasted_topics.jsonl |
| `scripts/run.py` | 修改 | 推送卡片去掉按钮、添加反馈提示 |
| `scripts/survey.py` | 修改 | 合并 recall_topics 逻辑，输出 JSON 到 stdout |
| `scripts/self-optimization/optimize_prompt.py` | 修改 | 去掉飞书卡片+session_state，输出 JSON 到 stdout |
| `scripts/self-optimization/optimize_rules.py` | 修改 | 同上 |
| `SKILL.md` | 重写 | 四个触发流的工作流指令 |
| `scripts/server.py` | **删除** | 常驻飞书回调服务 |
| `scripts/recall_topics.py` | **删除** | 合并到 survey.py |
| `scripts/session_state.json` | **删除** | 运行时文件，不再生成 |

---

### Task 1: 新建 feedback.py

**Files:**
- Create: `scripts/feedback.py`

- [ ] **Step 1: 创建 feedback.py**

```python
#!/opt/hermes/.venv/bin/python3
"""反馈记录脚本：将用户反馈写入 tasted_topics.jsonl"""
import sys
import json
import os
import fcntl
import argparse
from datetime import datetime
from pathlib import Path

os.environ["TZ"] = "Asia/Shanghai"

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
TASTED_TOPICS_PATH = DATA_DIR / "tasted_topics.jsonl"


def main():
    parser = argparse.ArgumentParser(description="记录用户对话题的反馈")
    parser.add_argument("--word", required=True, help="话题名称")
    parser.add_argument("--liked", required=True, choices=["true", "false"], help="是否感兴趣")
    parser.add_argument("--category", default="", help="话题分类")
    parser.add_argument("--ts", default="", help="推送时间戳")
    args = parser.parse_args()

    record = {
        "ts": args.ts or datetime.now().isoformat(),
        "word": args.word,
        "liked": args.liked == "true",
        "recorded_at": datetime.now().isoformat(),
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(TASTED_TOPICS_PATH, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    print(json.dumps({"status": "ok", "word": args.word, "liked": args.liked == "true"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 验证 feedback.py 可执行**

```bash
python3 scripts/feedback.py --word "#测试话题#" --liked true --category "科技" --ts "2026-05-19T12:00:00"
```

Expected: 输出 `{"status": "ok", "word": "#测试话题#", "liked": true}`，且 `scripts/data/tasted_topics.jsonl` 追加一行。

- [ ] **Step 3: 清理测试数据**

```bash
# 删除测试写入的行（最后一行）
sed -i '$ d' scripts/data/tasted_topics.jsonl
```

- [ ] **Step 4: 提交**

```bash
git add scripts/feedback.py
git commit -m "feat: add feedback.py CLI for recording user feedback to tasted_topics.jsonl"
```

---

### Task 2: 修改 run.py — 简化推送卡片

**Files:**
- Modify: `scripts/run.py:310-370` (send_push_card 函数)

- [ ] **Step 1: 替换 send_push_card 中的卡片构建逻辑**

定位到 `scripts/run.py` 的 `send_push_card` 函数，将 `elements` 构建代码（约 line 320-367）替换为：

```python
    elements = []

    for i, n in enumerate(topic_items):
        cat = n.get("category", "")
        hot = n.get("hot_str", "")
        word = n["word"]

        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{i+1}.** {word}  `{cat}`  {hot}"}
        })
        elements.append({"tag": "hr"})

    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "💬 回复序号评价本次推送，如\"1,3感兴趣\""}
    })
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"🕐 {date_str}"}
    })
```

删除原来的 `column_set` / `column` / `button` 代码块。

- [ ] **Step 2: 修改 send_push_card 返回值**

将函数末尾的 `return True`（约 line 391）改为：

```python
        return json.dumps({"success": True, "pushed_count": len(topic_items)}, ensure_ascii=False)
```

将 `return False`（约 line 394）改为：

```python
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
```

同时修改 main() 函数中对 `feishu_ok` 的使用（约 line 509-523），`send_push_card` 现在返回 JSON 字符串：

```python
    result = None
    if pushed:
        result = send_push_card(time_str, pushed, now.isoformat())
        try:
            result_obj = json.loads(result)
            feishu_ok = result_obj.get("success", False)
        except (json.JSONDecodeError, TypeError):
            feishu_ok = False
    else:
        logger.info("无重要话题，跳过推送")
```

- [ ] **Step 3: 验证语法正确**

```bash
python3 -c "import py_compile; py_compile.compile('scripts/run.py', doraise=True)"
```

Expected: 无错误输出。

- [ ] **Step 4: 提交**

```bash
git add scripts/run.py
git commit -m "refactor: simplify push card — remove like/dislike buttons, add feedback hint"
```

---

### Task 3: 合并 recall_topics.py 到 survey.py

**Files:**
- Modify: `scripts/survey.py`
- Delete: `scripts/recall_topics.py`

- [ ] **Step 1: 重写 survey.py**

完整替换 `scripts/survey.py`：

```python
#!/opt/hermes/.venv/bin/python3
"""
调研脚本：从未推送新闻中筛选候选，输出 JSON 供 agent 展示
"""
import sys
import json
import os
import re
import logging
from datetime import datetime, date
from pathlib import Path

import yaml
import openai

os.environ.setdefault("TZ", "Asia/Shanghai")

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
CONFIG_DIR = SCRIPT_DIR / "config"

ALL_TOPICS_PATH = DATA_DIR / "all_topics.jsonl"
PUSHED_TOPICS_PATH = DATA_DIR / "pushed_topics.jsonl"
PROMPT_PATH = CONFIG_DIR / "prompt.yaml"
BASE_CONFIG_PATH = CONFIG_DIR / "base.yaml"

MIN_UNPUSHED_COUNT = 5


def setup_logging():
    log_level = os.environ.get("WEIBO_HOT_NEWS_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    return logging.getLogger("survey")


logger = setup_logging()


def load_config():
    sys.path.insert(0, str(SCRIPT_DIR))
    from run import load_env

    cfg = {}
    if BASE_CONFIG_PATH.exists():
        with open(BASE_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}

    load_env()
    return cfg


def collect_unpushed_topics() -> tuple:
    """从 all_topics.jsonl 和 pushed_topics.jsonl 计算今天未推送的话题"""
    if not ALL_TOPICS_PATH.exists():
        return [], 0

    today = date.today().isoformat()

    pushed_words = set()
    if PUSHED_TOPICS_PATH.exists():
        with open(PUSHED_TOPICS_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not record.get("ts", "").startswith(today):
                    continue
                for n in record.get("topics", []):
                    pushed_words.add(n.get("word", ""))

    unpushed = {}
    with open(ALL_TOPICS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not record.get("ts", "").startswith(today):
                continue
            for n in record.get("topics", []):
                word = n.get("word", "")
                if word and word not in pushed_words and word not in unpushed:
                    unpushed[word] = {"word": word, "category": n.get("category", "")}

    result = list(unpushed.values())
    return result, len(pushed_words)


def call_llm_survey(unpushed: list, pushed_count: int, config: dict) -> list:
    """LLM 从未推送话题中判断用户可能感兴趣的，返回全部候选（带 llm_recommended 标记）"""
    api_key = config.get("llm", {}).get("api_key", "")
    if not api_key:
        logger.error("未找到 API_KEY")
        sys.exit(1)

    llm_model = config["llm"]["model"]
    base_url = config["llm"]["base_url"]

    with open(PROMPT_PATH, encoding="utf-8") as f:
        prompt_data = yaml.safe_load(f)
    current_prompt = prompt_data["judge_prompt"]

    criteria_section = ""
    important_match = re.search(r"【yes】范围：(.*?)(?=\n\n【no】)", current_prompt, re.DOTALL)
    if important_match:
        criteria_section = important_match.group(1).strip()

    target_count = max(1, pushed_count)

    topics_text_rows = "\n".join(
        f"{i+1}. {n['word']} | 分类:{n.get('category', '')}"
        for i, n in enumerate(unpushed)
    )

    prompt = f"""你是一个新闻重要性评估专家。以下微博热搜之前未被推送，请从中选出用户可能感兴趣的内容，数量约 {target_count} 条。

当前判断标准：
{criteria_section}

=== 未推送的新闻列表 ===
{topics_text_rows}

=== 输出格式 ===
每行格式："序号:选/不选"，严格按序号输出：
1:选
2:不选
3:选
...

必须包含全部 {len(unpushed)} 条新闻的判断。"""

    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    try:
        resp = client.chat.completions.create(
            model=llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4096,
            timeout=60,
        )
        content = resp.choices[0].message.content
        if not content:
            logger.warning("LLM 返回为空")
            return [{"word": n["word"], "category": n.get("category", ""), "llm_recommended": False} for n in unpushed]

        selections = {}
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d+):(选|不选)", line)
            if m:
                idx = int(m.group(1))
                selected = m.group(2) == "选"
                if 1 <= idx <= len(unpushed):
                    selections[unpushed[idx - 1]["word"]] = selected

        result = []
        for n in unpushed:
            result.append({
                "word": n["word"],
                "category": n.get("category", ""),
                "llm_recommended": selections.get(n["word"], False),
            })
        return result

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        return [{"word": n["word"], "category": n.get("category", ""), "llm_recommended": False} for n in unpushed]


def main():
    CONFIG = load_config()

    unpushed, pushed_count = collect_unpushed_topics()

    if len(unpushed) < MIN_UNPUSHED_COUNT:
        result = {
            "ready": False,
            "message": f"未推送新闻不足：{len(unpushed)} 条，需至少 {MIN_UNPUSHED_COUNT} 条",
            "total_unpushed": len(unpushed),
            "pushed_count": pushed_count,
        }
        print(json.dumps(result, ensure_ascii=False))
        return

    logger.info(f"当天未推送 {len(unpushed)} 条，已推送 {pushed_count} 条")

    candidates = call_llm_survey(unpushed, pushed_count, CONFIG)

    selected_count = sum(1 for c in candidates if c["llm_recommended"])
    logger.info(f"LLM 推荐调研 {selected_count} 条")

    result = {
        "ready": True,
        "candidates": candidates,
        "total_unpushed": len(unpushed),
        "pushed_count": pushed_count,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 删除 recall_topics.py**

```bash
rm scripts/recall_topics.py
```

- [ ] **Step 3: 清理 recall_topics 的 __pycache__**

```bash
rm -f scripts/self-optimization/__pycache__/recall_topics.*.pyc scripts/self-optimization/__pycache__/recall_news.*.pyc
```

- [ ] **Step 4: 验证语法正确**

```bash
python3 -c "import py_compile; py_compile.compile('scripts/survey.py', doraise=True)"
```

- [ ] **Step 5: 提交**

```bash
git add scripts/survey.py scripts/recall_topics.py
git rm scripts/recall_topics.py
git commit -m "refactor: merge recall_topics into survey.py, output JSON to stdout"
```

---

### Task 4: 修改 optimize_prompt.py — 去掉飞书交互

**Files:**
- Modify: `scripts/self-optimization/optimize_prompt.py`

- [ ] **Step 1: 删除飞书卡片构建和发送逻辑**

删除以下函数（约 line 229-264）：
- `build_card_json()` 函数

修改 `main()` 函数（约 line 267-348），替换飞书发送 + session_state 部分为 JSON 输出。

定位到 `main()` 中 line 299 之后的代码块（`logger.info(...)` 和飞书发送部分），替换为：

```python
    logger.info("=== 变更摘要 ===")
    for line in change_summary:
        logger.info(f"  {line}")

    logger.info(f"旧 prompt ({len(current_prompt)} 字), 新 prompt ({len(new_prompt)} 字)")

    result = {
        "ready": True,
        "current_prompt_preview": current_prompt[:150] + ("..." if len(current_prompt) > 150 else ""),
        "new_prompt": new_prompt,
        "change_summary": change_summary,
        "diff": _generate_diff(current_prompt, new_prompt),
        "total_feedback": total_feedback,
    }
    print(json.dumps(result, ensure_ascii=False))


def _generate_diff(old: str, new: str) -> str:
    """生成简单的行级 diff"""
    import difflib
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile="prompt.yaml (old)", tofile="prompt.yaml (new)"
    ))
    return "".join(diff_lines)
```

同时删除 main() 中从 `session_id = datetime.now().isoformat()` 到 `logger.info(f"会话状态已保存...")` 的所有飞书发送和 session_state 写入代码。

对于 `total_feedback` 不足的情况（约 line 287-291），替换 `return` 为：

```python
        result = {
            "ready": False,
            "total_feedback": total_feedback,
            "min_required": MIN_FEEDBACK_COUNT,
            "false_positive": len(feedback_data["false_positive"]),
            "true_positive": len(feedback_data["true_positive"]),
            "false_negative": len(feedback_data["false_negative"]),
        }
        print(json.dumps(result, ensure_ascii=False))
        return
```

删除不再需要的 `import shutil`（如果没有其他地方使用）。

- [ ] **Step 2: 验证语法正确**

```bash
python3 -c "import py_compile; py_compile.compile('scripts/self-optimization/optimize_prompt.py', doraise=True)"
```

- [ ] **Step 3: 提交**

```bash
git add scripts/self-optimization/optimize_prompt.py
git commit -m "refactor: remove feishu card from optimize_prompt, output JSON to stdout"
```

---

### Task 5: 修改 optimize_rules.py — 去掉飞书交互

**Files:**
- Modify: `scripts/self-optimization/optimize_rules.py`

- [ ] **Step 1: 删除飞书卡片相关函数**

删除以下函数：
- `build_card_json()`（约 line 173-229）
- `send_feishu_card()`（约 line 232-250）

- [ ] **Step 2: 简化 main() 为 JSON 输出**

替换 `main()` 中从 `choices = dict(recommendations)` 到文件末尾的所有代码（约 line 277-303）：

```python
    for cat, choice in recommendations.items():
        label = LABEL_MAP.get(choice, choice)
        logger.info(f"  {cat} → {label}")

    result = {
        "ready": True,
        "unclassified": unclassified,
        "recommendations": recommendations,
    }
    print(json.dumps(result, ensure_ascii=False))
```

同时修改 `find_unclassified_categories` 返回 `[]` 时的处理（约 line 266-268），将 `return` 改为：

```python
        result = {"ready": False, "message": "所有分类已归类"}
        print(json.dumps(result, ensure_ascii=False))
        return
```

删除不再需要的 `import shutil`。

- [ ] **Step 3: 验证语法正确**

```bash
python3 -c "import py_compile; py_compile.compile('scripts/self-optimization/optimize_rules.py', doraise=True)"
```

- [ ] **Step 4: 提交**

```bash
git add scripts/self-optimization/optimize_rules.py
git commit -m "refactor: remove feishu card from optimize_rules, output JSON to stdout"
```

---

### Task 6: 删除 server.py

**Files:**
- Delete: `scripts/server.py`

- [ ] **Step 1: 删除 server.py**

```bash
rm scripts/server.py
```

- [ ] **Step 2: 清理 server.py 的 __pycache__**

```bash
rm -f scripts/__pycache__/server.*.pyc
```

- [ ] **Step 3: 提交**

```bash
git rm scripts/server.py
git commit -m "refactor: remove server.py — replaced by trigger-based conversation flow"
```

---

### Task 7: 重写 SKILL.md

**Files:**
- Modify: `SKILL.md`

- [ ] **Step 1: 重写 SKILL.md**

完整替换 `SKILL.md`：

```markdown
---
name: weibo-hot-with-your-taste
description: 抓取微博热榜，根据用户偏好定制化筛选政治/经济/科技类新闻，通过飞书向用户推送
category: rss
---

# 微博热榜追踪

## 目录结构

weibo-hot-with-your-taste/
├── SKILL.md                  # Skill 说明文档
├── scripts/
│   ├── run.py                # 主脚本：抓取 → 规则过滤 → 规则反写 → LLM核校 → 推送
│   ├── feedback.py           # 反馈记录：将用户反馈写入 tasted_topics.jsonl
│   ├── survey.py             # 调研脚本：LLM 从未推送话题中召回候选
│   ├── config/
│   │   ├── base.yaml         # 基础配置（LLM参数、飞书webhook、性能开关）
│   │   ├── rule.yaml         # 规则配置（category_exclude分类排除、keyword_recall关键词反写）
│   │   └── prompt.yaml       # LLM 判断 prompt 模板
│   ├── data/
│   │   ├── category.json     # 分类词库（自动维护，记录微博API返回的category）
│   │   ├── all_topics.jsonl  # 原始全量抓取数据（每次run追加）
│   │   ├── pushed_topics.jsonl # 已推送新闻记录
│   │   ├── tasted_topics.jsonl # 用户品味档案（反馈+调研结果合并）
│   │   └── reports/          # 本地运行报告
│   └── self-optimization/
│       ├── optimize_prompt.py  # Prompt优化：根据品味数据优化判断标准
│       └── optimize_rules.py   # 规则优化：发现未归类分类，LLM预判归属
└── references/               # 参考文档
    └── weibo-api-header.md   # 微博 API Header 要求

## 核心脚本

| 脚本 | 触发方式 | 职责 |
|------|---------|------|
| `run.py` | agent 调用 | 抓取 → 规则过滤 → 反写 → LLM核校 → 推送飞书 |
| `feedback.py` | agent 调用 | 接收 --word/--liked 参数，写入 tasted_topics.jsonl |
| `survey.py` | agent 调用 | 计算差集，LLM 召回候选，输出 JSON 到 stdout |
| `optimize_prompt.py` | agent 调用 | 分析 tasted_topics.jsonl → LLM 优化 prompt，输出 diff |
| `optimize_rules.py` | agent 调用 | 发现 category.json 未归类分类 → LLM 预判归属，输出建议 |

## 业务流

### 1. 推送流（run.py）

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
2. 如果 `ready=false` → 告知"所有分类已归类，无需更新"
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
{"ts": "2026-05-18T10:00:00", "word": "#小米YU7#", "liked": true, "recorded_at": "2026-05-18T10:05:00"}
```

### pushed_topics.jsonl

每次推送的记录。

```json
{"ts": "2026-05-18T10:00:00", "total_fetched": 50, "topics": [{"word": "...", "category": "..."}]}
```

## 本地报告

`scripts/data/reports/YYYYMMDD_HHMM.json`

```json
{
  "ts": "2026-05-18T10:00:00",
  "total_fetched": 50,
  "pushed_count": 8,
  "pushed": ["#话题1#", "#话题2#"],
  "feishu_sent": true
}
```
```

- [ ] **Step 2: 提交**

```bash
git add SKILL.md
git commit -m "docs: rewrite SKILL.md for trigger-based conversation flows"
```
