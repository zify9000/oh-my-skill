#!/opt/hermes/.venv/bin/python3
"""
召回脚本：从被排除的话题中召回用户可能感兴趣的内容，收集假阴性信号
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
PUSH_HISTORY_PATH = SCRIPT_DIR / "push_history.jsonl"
PROMPT_PATH = SCRIPT_DIR / "prompt.yaml"
CONFIG_PATH = SCRIPT_DIR / "config.yaml"

MIN_EXCLUDED_COUNT = 5


def setup_logging():
    """配置日志系统"""
    log_level = os.environ.get("WEIBO_TRACKER_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    return logging.getLogger("recall-topics")


logger = setup_logging()


def load_config():
    """加载配置文件"""
    from run import _load_dotenv, _resolve_api_credentials

    cfg = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}

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
        except Exception as e:
            logger.warning(f"加载全局配置文件失败: {e}")

    _load_dotenv()
    _resolve_api_credentials(cfg)

    feishu_chat_id = os.environ.get("FEISHU_HOME_CHANNEL")
    if feishu_chat_id:
        if "feishu" not in cfg:
            cfg["feishu"] = {}
        cfg["feishu"]["chat_id"] = feishu_chat_id

    return cfg


def collect_today_excluded() -> list:
    """
    从 push_history.jsonl 收集当天 pushed=false 的话题（去重）

    Returns:
        [{"word": ..., "category": ...}, ...]
    """
    if not PUSH_HISTORY_PATH.exists():
        return []

    today = date.today().isoformat()
    seen = {}
    pushed_count = 0

    with open(PUSH_HISTORY_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = record.get("ts", "")
            if not ts.startswith(today):
                continue

            for topic in record.get("topics", []):
                if topic.get("pushed"):
                    pushed_count += 1
                    continue
                word = topic.get("word", "")
                if word and word not in seen:
                    seen[word] = {"word": word, "category": topic.get("category", "")}

    excluded = list(seen.values())
    return excluded, pushed_count


def call_llm_recall(excluded_topics: list, pushed_count: int, config: dict) -> dict:
    """
    调用 LLM 从被排除的话题中选出用户可能感兴趣的

    Returns:
        {word: True/False} 映射，True=LLM 推荐召回
    """
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
    important_match = re.search(r"【重要】范围：(.*?)(?=\n\n【不重要】)", current_prompt, re.DOTALL)
    if important_match:
        criteria_section = important_match.group(1).strip()

    target_count = max(1, pushed_count)

    topics_text = "\n".join(
        f"{i+1}. {t['word']} | 分类:{t['category']}"
        for i, t in enumerate(excluded_topics)
    )

    prompt = f"""你是一个新闻重要性评估专家。以下微博热搜话题之前被判断为"不重要"而未推送。
请从中选出用户可能感兴趣的话题，数量约 {target_count} 条。

当前判断标准：
{criteria_section}

=== 被排除的话题列表 ===
{topics_text}

=== 输出格式 ===
每行格式："序号:选/不选"，严格按序号输出：
1:选
2:不选
3:选
...

必须包含全部 {len(excluded_topics)} 条话题的判断。"""

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
            logger.warning("LLM 返回为空，所有话题标记为不选")
            return {t["word"]: False for t in excluded_topics}

        result = {}
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d+):(选|不选)", line)
            if m:
                idx = int(m.group(1))
                selected = m.group(2) == "选"
                if 1 <= idx <= len(excluded_topics):
                    result[excluded_topics[idx - 1]["word"]] = selected

        for t in excluded_topics:
            if t["word"] not in result:
                result[t["word"]] = False

        return result

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        return {t["word"]: False for t in excluded_topics}


def build_recall_card(topics: list, llm_selections: dict, ts: str, date_str: str) -> dict:
    """
    构建召回反馈卡片

    LLM 选中的话题用 🔹 标记，编号列表 + 编号按钮
    """
    topic_lines = []
    for i, t in enumerate(topics):
        selected = llm_selections.get(t["word"], False)
        prefix = "🔹 " if selected else ""
        topic_lines.append(f"**{i+1}.** {prefix}{t['word']}  `{t.get('category', '')}`")

    elements = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": "\n".join(topic_lines)}
        },
        {"tag": "hr"},
    ]

    for i, t in enumerate(topics):
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": f"{i+1} 👍"},
                    "type": "primary",
                    "value": {"source": "recall", "ts": ts, "word": t["word"], "feedback": 1}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": f"{i+1} 👎"},
                    "type": "default",
                    "value": {"source": "recall", "ts": ts, "word": t["word"], "feedback": 0}
                }
            ]
        })

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "action",
        "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "⏭️ 跳过全部"},
            "type": "default",
            "value": {"source": "recall", "ts": ts, "action": "skip_all"}
        }]
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"🔍 召回反馈 · {date_str}"},
            "template": "orange"
        },
        "elements": elements
    }


def main():
    CONFIG = load_config()

    excluded_topics, pushed_count = collect_today_excluded()

    if len(excluded_topics) < MIN_EXCLUDED_COUNT:
        logger.info(f"当天被排除话题不足（{len(excluded_topics)} 条，需 {MIN_EXCLUDED_COUNT} 条），退出")
        print(f"当天被排除话题不足：{len(excluded_topics)} 条，需至少 {MIN_EXCLUDED_COUNT} 条")
        return

    logger.info(f"当天被排除 {len(excluded_topics)} 条话题，已推送 {pushed_count} 条")

    llm_selections = call_llm_recall(excluded_topics, pushed_count, CONFIG)

    selected_count = sum(1 for v in llm_selections.values() if v)
    logger.info(f"LLM 推荐召回 {selected_count} 条话题")

    now = datetime.now()
    ts = now.isoformat()
    date_str = now.strftime("%Y年%m月%d日 %H:%M")

    card_json = build_recall_card(excluded_topics, llm_selections, ts, date_str)

    from run import _get_feishu_token, _send_feishu_message

    token, _ = _get_feishu_token()
    if not token:
        logger.error("飞书认证失败")
        sys.exit(1)

    chat_id = CONFIG["feishu"]["chat_id"]
    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card_json, ensure_ascii=False),
    }

    _send_feishu_message(token, chat_id, payload)
    logger.info("召回卡片已发送，等待 feedback_daemon 处理回调")


if __name__ == "__main__":
    main()
