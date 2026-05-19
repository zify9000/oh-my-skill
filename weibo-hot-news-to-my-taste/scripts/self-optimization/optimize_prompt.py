#!/opt/hermes/.venv/bin/python3
"""
Prompt 优化脚本：根据 tasted_topics.jsonl 中的用户品味数据优化 prompt.yaml 判断标准
"""
import sys
import json
import os
import re

import logging

from pathlib import Path

import yaml
import openai

os.environ.setdefault("TZ", "Asia/Shanghai")

SCRIPT_DIR = Path(__file__).parent.parent  # scripts/
DATA_DIR = SCRIPT_DIR / "data"
CONFIG_DIR = SCRIPT_DIR / "config"

TASTED_TOPICS_PATH = DATA_DIR / "tasted_topics.jsonl"
PUSHED_TOPICS_PATH = DATA_DIR / "pushed_topics.jsonl"
PROMPT_PATH = CONFIG_DIR / "prompt.yaml"
BASE_CONFIG_PATH = CONFIG_DIR / "base.yaml"


MIN_FEEDBACK_COUNT = 5


def setup_logging():
    log_level = os.environ.get("WEIBO_HOT_NEWS_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    return logging.getLogger("prompt-optimizer")


logger = setup_logging()


def load_config():
    sys.path.insert(0, str(SCRIPT_DIR))
    from run import load_env

    cfg = {}
    if BASE_CONFIG_PATH.exists():
        with open(BASE_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}

    load_env()

    feishu_chat_id = os.environ.get("FEISHU_HOME_CHANNEL")
    if feishu_chat_id:
        cfg.setdefault("feishu", {})["chat_id"] = feishu_chat_id

    return cfg


def collect_feedback_data() -> dict:
    """
    从 tasted_topics.jsonl 和 pushed_topics.jsonl 收集反馈数据

    分类逻辑：
    - 已推送 + 用户喜欢 → true_positive
    - 已推送 + 用户不喜欢 → false_positive
    - 未推送 + 用户喜欢 → false_negative（从调研/召回中发现的遗漏）

    Returns:
        {"false_positive": [...], "true_positive": [...], "false_negative": [...]}
    """
    if not TASTED_TOPICS_PATH.exists():
        return {"false_positive": [], "true_positive": [], "false_negative": []}

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
                for n in record.get("topics", []):
                    pushed_words.add(n.get("word", ""))

    false_positive = []
    true_positive = []
    false_negative = []

    seen = set()

    with open(TASTED_TOPICS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            word = record.get("word", "")
            if not word or word in seen:
                continue
            seen.add(word)

            liked = record.get("liked", False)
            category = record.get("category", "")
            entry = {"word": word, "category": category}

            if word in pushed_words:
                if liked:
                    true_positive.append(entry)
                else:
                    false_positive.append(entry)
            else:
                if liked:
                    false_negative.append(entry)

    return {
        "false_positive": false_positive,
        "true_positive": true_positive,
        "false_negative": false_negative,
    }


def format_feedback_for_llm(feedback_data: dict) -> str:
    sections = []

    fp = feedback_data["false_positive"]
    if fp:
        lines = [f'- "{t["word"]}" ({t["category"]}) → 👎' for t in fp]
        sections.append(f"假阳性（被推送但用户不感兴趣）：\n" + "\n".join(lines))

    tp = feedback_data["true_positive"]
    if tp:
        lines = [f'- "{t["word"]}" ({t["category"]}) → 👍' for t in tp]
        sections.append(f"真阳性（被推送且用户感兴趣）：\n" + "\n".join(lines))

    fn = feedback_data["false_negative"]
    if fn:
        lines = [f'- "{t["word"]}" ({t["category"]}) → 👍' for t in fn]
        sections.append(f"假阴性（被排除但用户感兴趣）：\n" + "\n".join(lines))

    return "\n\n".join(sections)


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


def call_llm_optimize(current_prompt: str, feedback_text: str, config: dict) -> tuple:
    api_key = config.get("llm", {}).get("api_key", "")
    if not api_key:
        logger.error("未找到 API_KEY")
        sys.exit(1)

    llm_model = config["llm"]["model"]
    base_url = config["llm"]["base_url"]

    optimize_prompt = f"""你是一个 prompt 优化专家。请根据用户反馈优化以下新闻判断 prompt。

当前 prompt:
{current_prompt}

用户反馈数据:
{feedback_text}

请根据用户反馈优化判断标准，使 prompt 更准确地匹配用户偏好。

要求：
1. 只修改判断标准部分（【重要】和【不重要】的范围），不改变输出格式
2. 输出格式必须保持：序号:【重要】或 序号:【不重要】
3. 保持 prompt 的整体结构不变

请按以下格式输出：

===优化后的prompt===
（完整的优化后 prompt，包含所有部分）

===变更摘要===
1. 【操作类型】变更描述
2. 【操作类型】变更描述
..."""

    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    try:
        resp = client.chat.completions.create(
            model=llm_model,
            messages=[{"role": "user", "content": optimize_prompt}],
            temperature=0.3,
            max_tokens=8192,
            timeout=120,
        )
        content = resp.choices[0].message.content
        if not content:
            logger.error("LLM 返回为空")
            sys.exit(1)

        new_prompt = ""
        change_summary = []

        prompt_match = re.search(r"===优化后的prompt===\s*\n(.*?)(?=\n===变更摘要===)", content, re.DOTALL)
        if prompt_match:
            new_prompt = prompt_match.group(1).strip()

        summary_match = re.search(r"===变更摘要===\s*\n(.*)", content, re.DOTALL)
        if summary_match:
            for line in summary_match.group(1).strip().split("\n"):
                line = line.strip()
                if line:
                    change_summary.append(line)

        if not new_prompt:
            logger.error("无法解析 LLM 输出中的优化后 prompt")
            sys.exit(1)

        return new_prompt, change_summary

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        sys.exit(1)


def main():
    CONFIG = load_config()

    if not PROMPT_PATH.exists():
        logger.error("prompt.yaml 不存在")
        sys.exit(1)

    with open(PROMPT_PATH, encoding="utf-8") as f:
        prompt_data = yaml.safe_load(f)
    current_prompt = prompt_data["judge_prompt"]

    feedback_data = collect_feedback_data()

    total_feedback = (
        len(feedback_data["false_positive"])
        + len(feedback_data["true_positive"])
        + len(feedback_data["false_negative"])
    )

    if total_feedback < MIN_FEEDBACK_COUNT:
        logger.info(f"反馈数据不足（{total_feedback} 条，需 {MIN_FEEDBACK_COUNT} 条），请先积累数据")
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

    logger.info(f"收集到 {total_feedback} 条反馈（假阳性 {len(feedback_data['false_positive'])}, 真阳性 {len(feedback_data['true_positive'])}, 假阴性 {len(feedback_data['false_negative'])}）")

    feedback_text = format_feedback_for_llm(feedback_data)
    new_prompt, change_summary = call_llm_optimize(current_prompt, feedback_text, CONFIG)

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


if __name__ == "__main__":
    main()
