#!/opt/hermes/.venv/bin/python3
"""
规则优化脚本：发现 keyword.json 中未归类的分类，
让 LLM 预判归属，通过飞书交互式卡片让用户确认后由 feedback_daemon 写入 config.yaml
"""

import sys
import json
import os
import re
import logging
from datetime import datetime
from pathlib import Path

import yaml
import openai

os.environ.setdefault("TZ", "Asia/Shanghai")

SCRIPT_DIR = Path(__file__).parent
KEYWORD_STORE_PATH = SCRIPT_DIR / "keyword.json"
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
DAEMON_STATE_PATH = SCRIPT_DIR / "daemon_state.json"

CHOICE_EXCLUDE = "exclude"
CHOICE_IMPORTANT = "important"
CHOICE_SKIP = "skip"

VALID_CHOICES = {CHOICE_EXCLUDE, CHOICE_IMPORTANT, CHOICE_SKIP}

LABEL_MAP = {
    CHOICE_EXCLUDE: "排除",
    CHOICE_IMPORTANT: "重要",
    CHOICE_SKIP: "跳过",
}


def setup_logging():
    """配置日志系统"""
    import time as time_module
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
    """加载配置文件"""
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

    from run import _load_dotenv, _resolve_api_credentials
    _load_dotenv()
    _resolve_api_credentials(cfg)

    feishu_chat_id = os.environ.get("FEISHU_HOME_CHANNEL")
    if feishu_chat_id:
        if "feishu" not in cfg:
            cfg["feishu"] = {}
        cfg["feishu"]["chat_id"] = feishu_chat_id

    return cfg


def find_unclassified_categories(keyword_store: dict, config: dict) -> list:
    """
    找出 keyword.json 中未在 config.yaml 中归类的分类

    已归类 = 出现在 exclude_categories 或 star_keywords.important 中
    """
    all_cats = set(keyword_store.get("categories", []))

    filter_cfg = config.get("filter", {})
    exclude = set(filter_cfg.get("exclude_categories", []))
    important = set(filter_cfg.get("star_keywords", {}).get("important", []))

    classified = exclude | important
    return sorted(all_cats - classified)


def llm_classify_categories(categories: list, config: dict) -> dict:
    """
    调用 LLM 预判每个未归类分类的推荐归属

    Returns:
        {分类名: 推荐归属} 映射，归属值为 exclude/important/skip
    """
    api_key = config.get("llm", {}).get("api_key", "")
    if not api_key:
        logger.warning("未找到 API_KEY，所有分类默认标记为 skip")
        return {cat: CHOICE_SKIP for cat in categories}

    llm_model = config["llm"]["model"]
    base_url = config["llm"]["base_url"]

    filter_cfg = config.get("filter", {})
    exclude = filter_cfg.get("exclude_categories", [])
    important = filter_cfg.get("star_keywords", {}).get("important", [])

    cat_list = "\n".join(f"{i+1}. {cat}" for i, cat in enumerate(categories))

    prompt = f"""你是一个微博热搜分类专家。请判断以下微博热搜分类应归属哪一类。

=== 已有规则参考 ===

排除分类（娱乐/生活类，不值得关注）：
{', '.join(exclude)}

重要分类关键词（必须推送的重要新闻）：
{', '.join(important)}

=== 待分类列表 ===
{cat_list}

=== 归类标准 ===

排除(exclude)：纯娱乐/生活类，如影视、综艺、体育、美食、旅游等
重要(important)：政治/军事/重大科技/宏观经济等核心关注领域
跳过(skip)：无法确定或需要人工判断

=== 输出格式 ===
每行格式："序号:归属"，严格按序号输出，不要输出分类名称：
1:exclude
2:important
3:skip

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
            m = re.match(r"^(\d+):(exclude|important|skip)", line)
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


def build_card_json(unclassified: list, choices: dict, recommendations: dict, session_id: str) -> dict:
    """
    构建飞书交互式卡片 JSON

    Args:
        unclassified: 未归类分类列表
        choices: 当前用户选择 {分类: 归属}
        recommendations: LLM 推荐归属 {分类: 归属}
        session_id: 会话 ID（用于 feedback_daemon 路由）
    """
    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**发现 {len(unclassified)} 个新分类待处理**，LLM 已给出推荐归属，可逐个调整后确认提交"
            }
        },
        {"tag": "hr"}
    ]

    for cat in unclassified:
        current = choices.get(cat, recommendations.get(cat, CHOICE_SKIP))
        rec = recommendations.get(cat, CHOICE_SKIP)
        rec_label = LABEL_MAP.get(rec, rec)

        rec_hint = f"（推荐: {rec_label}）" if current != rec else f"（推荐: {rec_label} ✓）"

        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{cat}** {rec_hint}"}
        })

        actions = []
        for c in [CHOICE_EXCLUDE, CHOICE_IMPORTANT, CHOICE_SKIP]:
            label = LABEL_MAP[c]
            if c == current:
                label = f"{label} ✓"
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": label},
                "type": "primary" if c == current else "default",
                "value": {"source": "optimize_rules", "session_id": session_id, "category": cat, "choice": c}
            })

        elements.append({"tag": "action", "actions": actions})
        elements.append({"tag": "hr"})

    elements.append({
        "tag": "action",
        "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "✅ 确认提交"},
            "type": "primary",
            "value": {"source": "optimize_rules", "session_id": session_id, "action": "confirm"}
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


def send_feishu_card(config: dict, card_json: dict) -> str:
    """发送飞书交互式卡片，返回 message_id"""
    from run import _get_feishu_token, _send_feishu_message

    token, _ = _get_feishu_token()
    if not token:
        logger.error("飞书认证失败")
        sys.exit(1)

    chat_id = config["feishu"]["chat_id"]
    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card_json, ensure_ascii=False),
    }

    message_id = _send_feishu_message(token, chat_id, payload)
    logger.info(f"卡片已发送, message_id={message_id}")
    return message_id


def apply_choices_to_config(choices: dict, config: dict):
    """
    将用户确认的分类归属写入 config.yaml

    Args:
        choices: {分类: 归属} 映射
        config: 当前配置字典
    """
    import shutil

    backup_path = CONFIG_PATH.with_suffix(".yaml.bak")
    shutil.copy2(CONFIG_PATH, backup_path)
    logger.info(f"已备份配置到 {backup_path}")

    filter_cfg = config.setdefault("filter", {})
    exclude = filter_cfg.setdefault("exclude_categories", [])
    star_kw = filter_cfg.setdefault("star_keywords", {})
    important = star_kw.setdefault("important", [])

    for cat, choice in choices.items():
        if choice == CHOICE_SKIP:
            continue
        elif choice == CHOICE_EXCLUDE:
            if cat not in exclude:
                exclude.append(cat)
                logger.info(f"  + 排除: {cat}")
        elif choice == CHOICE_IMPORTANT:
            if cat not in important:
                important.append(cat)
                logger.info(f"  + 重要: {cat}")

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    logger.info(f"配置已写入 {CONFIG_PATH}")


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
    session_id = datetime.now().isoformat()

    card_json = build_card_json(unclassified, choices, recommendations, session_id)
    message_id = send_feishu_card(CONFIG, card_json)

    state = {"sessions": {}}
    if DAEMON_STATE_PATH.exists():
        try:
            with open(DAEMON_STATE_PATH, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            pass

    state["sessions"][session_id] = {
        "type": "optimize_rules",
        "choices": choices,
        "recommendations": recommendations,
        "unclassified": unclassified,
        "message_id": message_id,
    }

    with open(DAEMON_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    logger.info(f"会话状态已保存到 daemon_state.json (session_id={session_id})")
    logger.info("卡片已发送，等待 feedback_daemon 处理回调")


if __name__ == "__main__":
    main()
