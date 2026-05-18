# optimize_rules.py 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建独立脚本 optimize_rules.py，自动发现 keyword.json 中未归类的分类，让 LLM 预判归属，通过飞书交互式卡片让用户确认后写入 config.yaml

**Architecture:** 单文件脚本 optimize_rules.py，读取 keyword.json + config.yaml 找出未归类分类，调用 LLM 预判，发送飞书交互式卡片，通过飞书长连接 SDK 接收用户按钮回调，确认后修改 config.yaml

**Tech Stack:** Python 3.13, lark-oapi (飞书长连接 SDK), openai (LLM), pyyaml (配置读写)

---

## 文件结构

| 文件 | 职责 | 修改类型 |
|------|------|----------|
| `weibo-hot-tracker/optimize_rules.py` | 规则优化脚本 | 新建 |
| `weibo-hot-tracker/config.yaml` | 本地配置 | 被脚本修改（追加分类） |
| `weibo-hot-tracker/keyword.json` | 关键词库 | 只读 |

---

### Task 1: 安装依赖 + 脚本骨架

**Files:**
- Create: `weibo-hot-tracker/optimize_rules.py`

- [ ] **Step 1: 安装 lark-oapi**

Run: `pip install lark-oapi -U && pip list | grep lark-oapi`

Expected: `lark-oapi x.x.x` 显示版本号

- [ ] **Step 2: 创建 optimize_rules.py 骨架**

```python
#!/opt/hermes/.venv/bin/python3
"""
规则优化脚本：发现 keyword.json 中未归类的分类，
让 LLM 预判归属，通过飞书交互式卡片让用户确认后写入 config.yaml
"""

import sys
import json
import os
import shutil
import time as time_module
import logging
from datetime import datetime
from pathlib import Path

import yaml
import openai

os.environ["TZ"] = "Asia/Shanghai"
time_module.tzset()

SCRIPT_DIR = Path(__file__).parent
KEYWORD_STORE_PATH = SCRIPT_DIR / "keyword.json"
CONFIG_PATH = SCRIPT_DIR / "config.yaml"

CHOICE_EXCLUDE = "exclude"
CHOICE_CRITICAL = "critical"
CHOICE_NOTEWORTHY = "noteworthy"
CHOICE_SKIP = "skip"

VALID_CHOICES = {CHOICE_EXCLUDE, CHOICE_CRITICAL, CHOICE_NOTEWORTHY, CHOICE_SKIP}

LABEL_MAP = {
    CHOICE_EXCLUDE: "排除",
    CHOICE_CRITICAL: "重点",
    CHOICE_NOTEWORTHY: "要闻",
    CHOICE_SKIP: "跳过",
}


def setup_logging():
    """配置日志系统"""
    log_level = os.environ.get("WEIBO_TRACKER_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    return logging.getLogger("rule-optimizer")


logger = setup_logging()


def load_config():
    """加载配置文件，本地 config.yaml 优先于全局 ~/.hermes/config.yaml"""
    cfg = {}

    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                cfg = yaml.safe_load(f) or {}
            logger.info(f"已加载配置文件: {CONFIG_PATH}")
        except Exception as e:
            logger.error(f"加载配置文件失败 {CONFIG_PATH}: {e}")
            raise

    global_cfg_path = Path.home() / ".hermes" / "config.yaml"
    if global_cfg_path.exists():
        try:
            with open(global_cfg_path) as f:
                global_cfg = yaml.safe_load(f) or {}
                if global_cfg:
                    for key, value in global_cfg.items():
                        if key not in cfg:
                            cfg[key] = value
                        elif isinstance(cfg[key], dict) and isinstance(value, dict):
                            merged = dict(value)
                            merged.update(cfg[key])
                            cfg[key] = merged
            logger.info(f"已加载全局配置文件: {global_cfg_path}")
        except Exception as e:
            logger.warning(f"加载全局配置文件失败 {global_cfg_path}: {e}")

    _load_dotenv()
    _resolve_api_credentials(cfg)

    feishu_chat_id = os.environ.get("FEISHU_HOME_CHANNEL")
    if feishu_chat_id:
        if "feishu" not in cfg:
            cfg["feishu"] = {}
        cfg["feishu"]["chat_id"] = feishu_chat_id

    return cfg


def _load_dotenv():
    """从 ~/.hermes/.env 加载环境变量"""
    env_path = Path.home() / ".hermes" / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")


def _resolve_api_credentials(cfg):
    """从全局配置解析 API key 和 LLM 参数"""
    cfg_path = Path.home() / ".hermes" / "config.yaml"
    if not cfg_path.exists():
        return

    with open(cfg_path) as f:
        global_model_cfg = yaml.safe_load(f).get("model", {})

    raw_key = global_model_cfg.get("api_key", "")
    if raw_key.startswith("${") and raw_key.endswith("}"):
        env_var = raw_key[2:-1]
        api_key = os.environ.get(env_var, "")
    else:
        api_key = raw_key

    if "llm" not in cfg:
        cfg["llm"] = {}

    if api_key:
        cfg["llm"]["api_key"] = api_key
    if "default" in global_model_cfg and "model" not in cfg["llm"]:
        cfg["llm"]["model"] = global_model_cfg["default"]
    if "base_url" in global_model_cfg and "base_url" not in cfg["llm"]:
        cfg["llm"]["base_url"] = global_model_cfg["base_url"]


def find_unclassified_categories(keyword_store: dict, config: dict) -> list[str]:
    """
    找出 keyword.json 中未在 config.yaml 中归类的分类

    一个分类如果出现在以下任一列表中，视为已归类：
    - filter.exclude_categories
    - filter.star_keywords.critical
    - filter.star_keywords.noteworthy
    """
    all_cats = set(keyword_store.get("categories", []))

    filter_cfg = config.get("filter", {})
    exclude = set(filter_cfg.get("exclude_categories", []))
    star_kw = filter_cfg.get("star_keywords", {})
    critical = set(star_kw.get("critical", []))
    noteworthy = set(star_kw.get("noteworthy", []))

    classified = exclude | critical | noteworthy
    unclassified = sorted(all_cats - classified)

    return unclassified


def main():
    CONFIG = load_config()

    if not KEYWORD_STORE_PATH.exists():
        logger.error("keyword.json 不存在，请先运行 run.py")
        sys.exit(1)

    with open(KEYWORD_STORE_PATH, encoding="utf-8") as f:
        keyword_store = json.load(f)

    unclassified = find_unclassified_categories(keyword_store, CONFIG)

    if not unclassified:
        logger.info("没有未归类的新分类，退出")
        return

    logger.info(f"发现 {len(unclassified)} 个未归类分类: {unclassified}")

    # 后续 Task 实现 LLM 预判 + 飞书交互


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 验证骨架可运行**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "import py_compile; py_compile.compile('optimize_rules.py', doraise=True)" && echo "SYNTAX OK"`

Expected: `SYNTAX OK`

- [ ] **Step 4: 验证未归类分类检测**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "
from optimize_rules import load_config, find_unclassified_categories, KEYWORD_STORE_PATH
import json
CONFIG = load_config()
with open(KEYWORD_STORE_PATH) as f:
    ks = json.load(f)
unclassified = find_unclassified_categories(ks, CONFIG)
print(f'未归类分类: {unclassified}')
"`

Expected: 输出当前未归类的分类列表（如 `['辟谣/通报', '电影', '演出', '教育']`）

---

### Task 2: LLM 预判分类归属

**Files:**
- Modify: `weibo-hot-tracker/optimize_rules.py`

- [ ] **Step 1: 在 main() 之前添加 llm_classify_categories 函数**

```python
def llm_classify_categories(categories: list[str], config: dict) -> dict[str, str]:
    """
    调用 LLM 预判每个未归类分类的推荐归属

    Args:
        categories: 未归类分类列表
        config: 配置字典

    Returns:
        {分类名: 推荐归属} 映射，归属值为 exclude/critical/noteworthy/skip
    """
    api_key = config.get("llm", {}).get("api_key", "")
    if not api_key:
        logger.warning("未找到 API_KEY，所有分类默认标记为 skip")
        return {cat: CHOICE_SKIP for cat in categories}

    llm_model = config["llm"]["model"]
    base_url = config["llm"]["base_url"]

    filter_cfg = config.get("filter", {})
    exclude = filter_cfg.get("exclude_categories", [])
    critical = filter_cfg.get("star_keywords", {}).get("critical", [])
    noteworthy = filter_cfg.get("star_keywords", {}).get("noteworthy", [])

    cat_list = "\n".join(f"{i+1}. {cat}" for i, cat in enumerate(categories))

    prompt = f"""你是一个微博热搜分类专家。请判断以下微博热搜分类应归属哪一类。

=== 已有规则参考 ===

排除分类（娱乐/生活类，不值得关注）：
{', '.join(exclude)}

重点分类关键词（必须推送的重要新闻）：
{', '.join(critical)}

要闻分类关键词（值得关注但非必须推送）：
{', '.join(noteworthy)}

=== 待分类列表 ===
{cat_list}

=== 归类标准 ===

排除(exclude)：纯娱乐/生活类，如影视、综艺、体育、美食、旅游等
重点(critical)：政治/军事/重大科技/宏观经济等核心关注领域
要闻(noteworthy)：一般性财经/科技/社会新闻
跳过(skip)：无法确定或需要人工判断

=== 输出格式 ===
每行格式："序号:归属"，严格按序号输出，不要输出分类名称：
1:exclude
2:critical
3:noteworthy
4:skip

必须包含全部 {len(categories)} 条分类的判断。"""

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
            logger.warning("LLM 返回为空，所有分类默认标记为 skip")
            return {cat: CHOICE_SKIP for cat in categories}

        result = {}
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            import re
            m = re.match(r"^(\d+):(exclude|critical|noteworthy|skip)", line)
            if m:
                idx = int(m.group(1))
                choice = m.group(2)
                if 1 <= idx <= len(categories):
                    result[categories[idx - 1]] = choice

        for cat in categories:
            if cat not in result:
                result[cat] = CHOICE_SKIP
                logger.warning(f"LLM 未返回 {cat} 的判断，默认 skip")

        return result

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}，所有分类默认标记为 skip")
        return {cat: CHOICE_SKIP for cat in categories}
```

- [ ] **Step 2: 更新 main() 调用 LLM 预判**

将 main() 中的 `# 后续 Task 实现 LLM 预判 + 飞书交互` 替换为：

```python
    recommendations = llm_classify_categories(unclassified, CONFIG)
    for cat, choice in recommendations.items():
        label = LABEL_MAP.get(choice, choice)
        logger.info(f"  {cat} → {label}")

    # 后续 Task 实现飞书交互
```

- [ ] **Step 3: 语法检查**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "import py_compile; py_compile.compile('optimize_rules.py', doraise=True)" && echo "SYNTAX OK"`

Expected: `SYNTAX OK`

---

### Task 3: 飞书交互式卡片 + 长连接回调

**Files:**
- Modify: `weibo-hot-tracker/optimize_rules.py`

- [ ] **Step 1: 添加飞书卡片构建函数**

在 `llm_classify_categories` 函数之后添加：

```python
def build_card_json(unclassified: list[str], choices: dict[str, str], recommendations: dict[str, str]) -> dict:
    """
    构建飞书交互式卡片 JSON

    Args:
        unclassified: 未归类分类列表
        choices: 当前用户选择 {分类: 归属}
        recommendations: LLM 推荐归属 {分类: 归属}
    """
    elements = []

    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"**发现 {len(unclassified)} 个新分类待处理**，LLM 已给出推荐归属，可逐个调整后确认提交"
        }
    })
    elements.append({"tag": "hr"})

    for cat in unclassified:
        current = choices.get(cat, recommendations.get(cat, CHOICE_SKIP))
        rec = recommendations.get(cat, CHOICE_SKIP)
        rec_label = LABEL_MAP.get(rec, rec)

        choice_labels = []
        for c in [CHOICE_EXCLUDE, CHOICE_CRITICAL, CHOICE_NOTEWORTHY, CHOICE_SKIP]:
            label = LABEL_MAP[c]
            if c == current:
                label = f"**{label} ✓**"
            choice_labels.append(label)

        rec_hint = f"（推荐: {rec_label}）" if current != rec else f"（推荐: {rec_label} ✓）"

        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**{cat}** {rec_hint}"
            }
        })

        actions = []
        for c in [CHOICE_EXCLUDE, CHOICE_CRITICAL, CHOICE_NOTEWORTHY, CHOICE_SKIP]:
            label = LABEL_MAP[c]
            if c == current:
                label = f"{label} ✓"
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": label},
                "type": "primary" if c == current else "default",
                "value": {"category": cat, "choice": c}
            })

        elements.append({"tag": "action", "actions": actions})
        elements.append({"tag": "hr"})

    elements.append({
        "tag": "action",
        "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "✅ 确认提交"},
            "type": "primary",
            "value": {"action": "confirm"}
        }]
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🔧 规则优化建议"},
            "template": "blue"
        },
        "elements": elements
    }
```

- [ ] **Step 2: 添加飞书消息发送函数**

```python
def send_feishu_card(config: dict, card_json: dict) -> str:
    """
    发送飞书交互式卡片，返回 message_id

    Args:
        config: 配置字典
        card_json: 卡片 JSON

    Returns:
        消息 ID
    """
    import curl_cffi

    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        logger.error("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")
        sys.exit(1)

    chat_id = config["feishu"]["chat_id"]
    sess = curl_cffi.Session(impersonate="chrome131")

    auth_resp = sess.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    token = auth_resp.json()["tenant_access_token"]

    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card_json, ensure_ascii=False),
    }

    resp = sess.post(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    result = resp.json()
    if result.get("code") != 0:
        logger.error(f"发送卡片失败: {result}")
        sys.exit(1)

    message_id = result["data"]["message_id"]
    logger.info(f"卡片已发送, message_id={message_id}")
    return message_id
```

- [ ] **Step 3: 添加飞书卡片更新函数**

```python
def update_feishu_card(config: dict, message_id: str, card_json: dict):
    """
    更新已发送的飞书卡片内容

    Args:
        config: 配置字典
        message_id: 消息 ID
        card_json: 新的卡片 JSON
    """
    import curl_cffi

    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    chat_id = config["feishu"]["chat_id"]
    sess = curl_cffi.Session(impersonate="chrome131")

    auth_resp = sess.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    token = auth_resp.json()["tenant_access_token"]

    resp = sess.patch(
        f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"content": json.dumps(card_json, ensure_ascii=False)},
        timeout=15,
    )
    result = resp.json()
    if result.get("code") != 0:
        logger.warning(f"更新卡片失败: {result}")
```

- [ ] **Step 4: 添加 config.yaml 写入函数**

```python
def apply_choices_to_config(choices: dict[str, str], config: dict):
    """
    将用户确认的分类归属写入 config.yaml

    Args:
        choices: {分类: 归属} 映射
        config: 当前配置字典
    """
    backup_path = CONFIG_PATH.with_suffix(".yaml.bak")
    shutil.copy2(CONFIG_PATH, backup_path)
    logger.info(f"已备份配置到 {backup_path}")

    filter_cfg = config.setdefault("filter", {})
    exclude = filter_cfg.setdefault("exclude_categories", [])
    star_kw = filter_cfg.setdefault("star_keywords", {})
    critical = star_kw.setdefault("critical", [])
    noteworthy = star_kw.setdefault("noteworthy", [])

    for cat, choice in choices.items():
        if choice == CHOICE_SKIP:
            continue
        elif choice == CHOICE_EXCLUDE:
            if cat not in exclude:
                exclude.append(cat)
                logger.info(f"  + 排除: {cat}")
        elif choice == CHOICE_CRITICAL:
            if cat not in critical:
                critical.append(cat)
                logger.info(f"  + 重点: {cat}")
        elif choice == CHOICE_NOTEWORTHY:
            if cat not in noteworthy:
                noteworthy.append(cat)
                logger.info(f"  + 要闻: {cat}")

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    logger.info(f"配置已写入 {CONFIG_PATH}")
```

- [ ] **Step 5: 添加飞书长连接回调处理 + 更新 main()**

将 main() 函数替换为：

```python
def main():
    CONFIG = load_config()

    if not KEYWORD_STORE_PATH.exists():
        logger.error("keyword.json 不存在，请先运行 run.py")
        sys.exit(1)

    with open(KEYWORD_STORE_PATH, encoding="utf-8") as f:
        keyword_store = json.load(f)

    unclassified = find_unclassified_categories(keyword_store, CONFIG)

    if not unclassified:
        logger.info("没有未归类的新分类，退出")
        return

    logger.info(f"发现 {len(unclassified)} 个未归类分类: {unclassified}")

    recommendations = llm_classify_categories(unclassified, CONFIG)
    for cat, choice in recommendations.items():
        label = LABEL_MAP.get(choice, choice)
        logger.info(f"  {cat} → {label}")

    choices = dict(recommendations)

    card_json = build_card_json(unclassified, choices, recommendations)
    message_id = send_feishu_card(CONFIG, card_json)

    import lark_oapi as lark
    from lark_oapi.api.im.v1 import *

    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")

    confirmed = {"value": False}

    def handle_card_action(ctx, conf, event):
        nonlocal choices, card_json

        action = event.event.action
        action_value = action.value

        if action_value.get("action") == "confirm":
            confirmed["value"] = True
            apply_choices_to_config(choices, CONFIG)
            logger.info("用户已确认，配置已更新")

            done_card = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "✅ 规则优化已完成"},
                    "template": "green"
                },
                "elements": [{
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "配置已更新，新规则将在下次运行时生效"
                    }
                }]
            }
            update_feishu_card(CONFIG, message_id, done_card)
            return lark.im.v1.P2CardActionTriggerResponse()

        cat = action_value.get("category", "")
        choice = action_value.get("choice", "")
        if cat and choice in VALID_CHOICES:
            choices[cat] = choice
            logger.info(f"用户选择: {cat} → {LABEL_MAP[choice]}")

        card_json = build_card_json(unclassified, choices, recommendations)
        update_feishu_card(CONFIG, message_id, card_json)

        return lark.im.v1.P2CardActionTriggerResponse()

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_card_action_trigger_v1(handle_card_action)
        .build()
    )

    cli = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.DEBUG,
    )

    logger.info("飞书长连接已启动，等待用户交互...（10 分钟超时）")

    import threading

    def timeout_exit():
        time_module.sleep(600)
        if not confirmed["value"]:
            logger.info("超时退出，未修改配置")
            os._exit(0)

    timeout_thread = threading.Thread(target=timeout_exit, daemon=True)
    timeout_thread.start()

    cli.start()
```

- [ ] **Step 6: 语法检查**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "import py_compile; py_compile.compile('optimize_rules.py', doraise=True)" && echo "SYNTAX OK"`

Expected: `SYNTAX OK`

---

### Task 4: 飞书应用配置 + 端到端验证

**Files:**
- Modify: `weibo-hot-tracker/SKILL.md` (新增 optimize_rules.py 说明)

- [ ] **Step 1: 确认飞书应用已开启卡片交互回调**

在飞书开发者后台检查：
1. 应用 → 事件与回调 → 事件订阅方式选择「使用长连接接收回调」
2. 添加事件订阅：`卡片回传交互 card.action.trigger`

如果未配置，需要在飞书开发者后台操作。

- [ ] **Step 2: 更新 SKILL.md**

在 SKILL.md 的目录结构中添加 optimize_rules.py，并在适当位置新增章节：

```markdown
## 规则优化

`optimize_rules.py` — 发现未归类分类，LLM 预判归属，飞书交互确认后写入 config.yaml

### 使用方式

```bash
python3 optimize_rules.py
```

### 前置条件

- 飞书应用已开启「长连接接收回调」
- 飞书应用已订阅 `card.action.trigger` 事件
- keyword.json 存在（先运行 run.py 至少一次）

### 流程

1. 读取 keyword.json 中未在 config.yaml 归类的分类
2. LLM 预判每个分类的归属（排除/重点/要闻/跳过）
3. 发送飞书交互式卡片，用户逐个调整
4. 用户点击「确认提交」→ 自动写入 config.yaml（带备份）
5. 10 分钟无操作自动退出
```

- [ ] **Step 3: 端到端测试**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 optimize_rules.py`

Expected: 脚本启动，发送飞书卡片，等待用户交互

- [ ] **Step 4: 提交**

```bash
cd /home/zify/myProject/oh-my-skill
git add weibo-hot-tracker/optimize_rules.py weibo-hot-tracker/SKILL.md
git commit -m "feat: add optimize_rules.py — LLM 预判分类归属 + 飞书交互确认"
```
