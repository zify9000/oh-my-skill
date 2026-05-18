#!/opt/hermes/.venv/bin/python3
"""
Prompt 优化脚本：根据用户反馈优化 prompt.yaml 中的判断标准
"""

import sys
import json
import os
import re
import shutil
import logging
from datetime import datetime
from pathlib import Path

import yaml
import openai

os.environ.setdefault("TZ", "Asia/Shanghai")

SCRIPT_DIR = Path(__file__).parent
PUSH_HISTORY_PATH = SCRIPT_DIR / "push_history.jsonl"
PROMPT_PATH = SCRIPT_DIR / "prompt.yaml"
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
DAEMON_STATE_PATH = SCRIPT_DIR / "daemon_state.json"

MIN_FEEDBACK_COUNT = 5


def setup_logging():
    """配置日志系统"""
    log_level = os.environ.get("WEIBO_TRACKER_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    return logging.getLogger("prompt-optimizer")


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


def collect_feedback_data() -> dict:
    """
    从 push_history.jsonl 收集有反馈的数据

    Returns:
        {"false_positive": [...], "true_positive": [...], "false_negative": [...]}
    """
    if not PUSH_HISTORY_PATH.exists():
        return {"false_positive": [], "true_positive": [], "false_negative": []}

    false_positive = []
    true_positive = []
    false_negative = []

    with open(PUSH_HISTORY_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            for topic in record.get("topics", []):
                feedback = topic.get("feedback")
                if feedback is None:
                    continue

                entry = {"word": topic["word"], "category": topic.get("category", "")}

                if topic.get("pushed") and feedback == 0:
                    false_positive.append(entry)
                elif topic.get("pushed") and feedback == 1:
                    true_positive.append(entry)
                elif not topic.get("pushed") and feedback == 1:
                    false_negative.append(entry)

    return {
        "false_positive": false_positive,
        "true_positive": true_positive,
        "false_negative": false_negative,
    }


def format_feedback_for_llm(feedback_data: dict) -> str:
    """格式化反馈数据供 LLM 分析"""
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


def call_llm_optimize(current_prompt: str, feedback_text: str, config: dict) -> tuple:
    """
    调用 LLM 生成优化后的 prompt 和变更摘要

    Returns:
        (new_prompt, change_summary_list)
    """
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


def build_card_json(change_summary: list, session_id: str) -> dict:
    """构建飞书交互式卡片 JSON"""
    summary_lines = "\n".join(change_summary)

    elements = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**变更摘要：**\n{summary_lines}"}
        },
        {"tag": "hr"},
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "✅ 确认更新"},
                    "type": "primary",
                    "value": {"source": "optimize_prompt", "session_id": session_id, "action": "confirm"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "❌ 放弃"},
                    "type": "default",
                    "value": {"source": "optimize_prompt", "session_id": session_id, "action": "reject"}
                }
            ]
        }
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📋 Prompt 优化建议"},
            "template": "blue"
        },
        "elements": elements
    }


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
        print(f"反馈数据不足：{total_feedback} 条，需至少 {MIN_FEEDBACK_COUNT} 条有反馈的记录")
        print(f"  假阳性: {len(feedback_data['false_positive'])} 条")
        print(f"  真阳性: {len(feedback_data['true_positive'])} 条")
        print(f"  假阴性: {len(feedback_data['false_negative'])} 条")
        return

    logger.info(f"收集到 {total_feedback} 条反馈（假阳性 {len(feedback_data['false_positive'])}, 真阳性 {len(feedback_data['true_positive'])}, 假阴性 {len(feedback_data['false_negative'])}）")

    feedback_text = format_feedback_for_llm(feedback_data)
    new_prompt, change_summary = call_llm_optimize(current_prompt, feedback_text, CONFIG)

    logger.info("=== 变更摘要 ===")
    for line in change_summary:
        logger.info(f"  {line}")

    logger.info("=== 新旧 prompt 对比 ===")
    logger.info(f"旧 prompt ({len(current_prompt)} 字):")
    logger.info(current_prompt[:200] + "..." if len(current_prompt) > 200 else current_prompt)
    logger.info(f"新 prompt ({len(new_prompt)} 字):")
    logger.info(new_prompt[:200] + "..." if len(new_prompt) > 200 else new_prompt)

    session_id = datetime.now().isoformat()

    card_json = build_card_json(change_summary, session_id)

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

    message_id = _send_feishu_message(token, chat_id, payload)
    logger.info(f"优化建议卡片已发送, message_id={message_id}")

    state = {"sessions": {}}
    if DAEMON_STATE_PATH.exists():
        try:
            with open(DAEMON_STATE_PATH, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            pass

    state["sessions"][session_id] = {
        "type": "optimize_prompt",
        "new_prompt": new_prompt,
        "message_id": message_id,
    }

    with open(DAEMON_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    logger.info(f"会话状态已保存 (session_id={session_id})，等待 feedback_daemon 处理回调")


if __name__ == "__main__":
    main()
