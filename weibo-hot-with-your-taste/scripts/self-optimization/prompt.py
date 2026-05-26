"""
Prompt 优化脚本：根据 tasted_topics.jsonl 中的用户品味数据优化 .initialized 中的 yes/no 判断标准
"""
import sys
import json
import re
import os
from pathlib import Path

import openai

sys.path.insert(0, str(Path(__file__).parent.parent))
from common import (
    DATA_DIR, PUSHED_TOPICS_PATH,
    setup_logging, load_base_config,
    load_user_prefs, INITIALIZED_PATH,
)

TASTED_TOPICS_PATH = DATA_DIR / "tasted_topics.jsonl"

logger = setup_logging("prompt-optimizer")
CONFIG = load_base_config()
MIN_FEEDBACK_COUNT = 5


def collect_feedback_data() -> dict:
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
    import difflib

    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(old_lines, new_lines, fromfile="old", tofile="new"))
    return "".join(diff_lines)


def call_llm_optimize(current_yes: str, current_no: str, feedback_text: str, llm_model="", base_url="", api_key="") -> tuple:
    """根据用户反馈优化 yes/no 判断标准，返回 (new_yes, new_no, change_summary)"""
    if not api_key:
        logger.error("未找到 API_KEY")
        sys.exit(1)
    if not llm_model or not base_url:
        logger.error("未配置 llm_model 或 llm_base_url")
        sys.exit(1)

    optimize_prompt = f"""你是一个 prompt 优化专家。请根据用户反馈优化以下新闻判断标准。

当前【yes】范围：
{current_yes}

当前【no】范围：
{current_no}

用户反馈数据:
{feedback_text}

请根据用户反馈优化判断标准，使其更准确地匹配用户偏好。

要求：
1. 只修改【yes】和【no】范围的判断标准
2. 保持每条一行，格式为"领域：具体描述"
3. 可以新增、删除、调整条目

请按以下格式输出：

===yes===
（优化后的 yes 范围，每条一行）

===no===
（优化后的 no 范围，每条一行）

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

        new_yes = ""
        new_no = ""
        change_summary = []

        yes_match = re.search(r"===yes===\s*\n(.*?)(?=\n===no===)", content, re.DOTALL)
        if yes_match:
            new_yes = yes_match.group(1).strip()

        no_match = re.search(r"===no===\s*\n(.*?)(?=\n===变更摘要===)", content, re.DOTALL)
        if no_match:
            new_no = no_match.group(1).strip()

        summary_match = re.search(r"===变更摘要===\s*\n(.*)", content, re.DOTALL)
        if summary_match:
            for line in summary_match.group(1).strip().split("\n"):
                line = line.strip()
                if line:
                    change_summary.append(line)

        if not new_yes or not new_no:
            logger.error("无法解析 LLM 输出中的判断标准")
            sys.exit(1)

        return new_yes, new_no, change_summary

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        sys.exit(1)


def main():
    prefs = load_user_prefs()
    if not prefs:
        result = {"ready": False, "message": "尚未初始化偏好，请先运行 init.py"}
        print(json.dumps(result, ensure_ascii=False))
        return

    current_yes = prefs.get("yes_criteria", "")
    current_no = prefs.get("no_criteria", "")

    feedback_data = collect_feedback_data()

    total_feedback = (
        len(feedback_data["false_positive"])
        + len(feedback_data["true_positive"])
        + len(feedback_data["false_negative"])
    )

    if total_feedback < MIN_FEEDBACK_COUNT:
        logger.info(f"反馈数据不足（{total_feedback} 条，需 {MIN_FEEDBACK_COUNT} 条）")
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

    logger.info(f"收集到 {total_feedback} 条反馈")

    feedback_text = format_feedback_for_llm(feedback_data)
    llm_model = os.environ.get("llm_model", "")
    llm_base_url = os.environ.get("llm_base_url", "")
    llm_api_key = os.environ.get("llm_api_key", "")
    new_yes, new_no, change_summary = call_llm_optimize(
        current_yes, current_no, feedback_text, llm_model, llm_base_url, llm_api_key
    )

    logger.info("=== 变更摘要 ===")
    for line in change_summary:
        logger.info(f"  {line}")

    # 更新 .initialized 中的 yes/no criteria
    prefs["yes_criteria"] = new_yes
    prefs["no_criteria"] = new_no
    with open(INITIALIZED_PATH, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False, indent=2)
    logger.info(".initialized 中的判断标准已更新")

    result = {
        "ready": True,
        "current_yes_preview": current_yes[:150] + ("..." if len(current_yes) > 150 else ""),
        "new_yes": new_yes,
        "new_no": new_no,
        "change_summary": change_summary,
        "diff_yes": _generate_diff(current_yes, new_yes),
        "diff_no": _generate_diff(current_no, new_no),
        "total_feedback": total_feedback,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
