#!/opt/hermes/.venv/bin/python3
"""
飞书回调守护进程：监听所有卡片交互回调，根据 source 字段分发处理

支持回调类型：
1. source=feedback → 更新 push_history.jsonl 的 feedback 字段
2. source=optimize_rules → 更新 config.yaml 的分类归属
3. source=optimize_prompt → 更新 prompt.yaml
4. source=recall → 更新 push_history.jsonl 中被排除话题的 feedback 字段
"""

import sys
import json
import os
import fcntl
import shutil
import logging
from pathlib import Path

import yaml
import lark_oapi as lark

os.environ["TZ"] = "Asia/Shanghai"

SCRIPT_DIR = Path(__file__).parent
PUSH_HISTORY_PATH = SCRIPT_DIR / "push_history.jsonl"
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
PROMPT_PATH = SCRIPT_DIR / "prompt.yaml"
DAEMON_STATE_PATH = SCRIPT_DIR / "daemon_state.json"


def setup_logging():
    """配置日志系统"""
    log_level = os.environ.get("WEIBO_TRACKER_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    return logging.getLogger("feedback-daemon")


logger = setup_logging()


def load_daemon_state() -> dict:
    """加载 daemon 状态"""
    if DAEMON_STATE_PATH.exists():
        try:
            with open(DAEMON_STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载 daemon_state.json 失败: {e}")
    return {"sessions": {}}


def save_daemon_state(state: dict):
    """保存 daemon 状态"""
    with open(DAEMON_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _update_feishu_card(message_id: str, card_json: dict):
    """更新已发送的飞书卡片"""
    try:
        from run import _get_feishu_token
        import curl_cffi

        token, _ = _get_feishu_token()
        if not token:
            return

        sess = curl_cffi.Session(impersonate="chrome131")
        card_str = json.dumps(card_json, ensure_ascii=False)

        sess.patch(
            f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"content": card_str},
            timeout=15,
        )
    except Exception as e:
        logger.warning(f"更新卡片失败: {e}")


def update_feedback_in_history(ts: str, word: str, feedback: int):
    """
    更新 push_history.jsonl 中指定记录的 feedback 字段

    使用文件锁防止与 run.py 的并发写入冲突
    """
    if not PUSH_HISTORY_PATH.exists():
        logger.warning("push_history.jsonl 不存在")
        return

    with open(PUSH_HISTORY_PATH, "r+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

        lines = f.readlines()
        updated = False

        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            if record.get("ts") != ts:
                continue

            for topic in record.get("topics", []):
                if topic.get("word") == word and topic.get("feedback") is None:
                    topic["feedback"] = feedback
                    updated = True

            if updated:
                lines[i] = json.dumps(record, ensure_ascii=False) + "\n"
                break

        if updated:
            f.seek(0)
            f.writelines(lines)
            f.truncate()
            logger.info(f"反馈已更新: {word} → {feedback}")
        else:
            logger.warning(f"未找到匹配记录: ts={ts}, word={word}")

        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def handle_feedback(action_value: dict):
    """处理 source=feedback 的回调（推送反馈卡片）"""
    ts = action_value.get("ts", "")
    action = action_value.get("action", "")

    if action == "skip_all":
        logger.info(f"跳过全部反馈: ts={ts}")
        return

    word = action_value.get("word", "")
    feedback = action_value.get("feedback")

    if word and feedback is not None:
        update_feedback_in_history(ts, word, int(feedback))


def handle_recall(action_value: dict):
    """处理 source=recall 的回调（召回反馈卡片）"""
    ts = action_value.get("ts", "")
    action = action_value.get("action", "")

    if action == "skip_all":
        logger.info(f"跳过全部召回反馈: ts={ts}")
        return

    word = action_value.get("word", "")
    feedback = action_value.get("feedback")

    if word and feedback is not None:
        update_feedback_in_history(ts, word, int(feedback))


def handle_optimize_rules(action_value: dict, state: dict):
    """处理 source=optimize_rules 的回调"""
    session_id = action_value.get("session_id", "")
    action = action_value.get("action", "")

    session = state.get("sessions", {}).get(session_id)
    if not session or session.get("type") != "optimize_rules":
        logger.warning(f"未找到 optimize_rules 会话: {session_id}")
        return

    if action == "confirm":
        choices = session.get("choices", {})
        config = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH) as f:
                config = yaml.safe_load(f) or {}

        backup_path = CONFIG_PATH.with_suffix(".yaml.bak")
        shutil.copy2(CONFIG_PATH, backup_path)
        logger.info(f"已备份配置到 {backup_path}")

        filter_cfg = config.setdefault("filter", {})
        exclude = filter_cfg.setdefault("exclude_categories", [])
        star_kw = filter_cfg.setdefault("star_keywords", {})
        important = star_kw.setdefault("important", [])

        for cat, choice in choices.items():
            if choice == "skip":
                continue
            elif choice == "exclude":
                if cat not in exclude:
                    exclude.append(cat)
                    logger.info(f"  + 排除: {cat}")
            elif choice == "important":
                if cat not in important:
                    important.append(cat)
                    logger.info(f"  + 重要: {cat}")

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        logger.info(f"配置已写入 {CONFIG_PATH}")

        message_id = session.get("message_id", "")
        if message_id:
            _update_feishu_card(message_id, {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "✅ 规则优化已完成"},
                    "template": "green"
                },
                "elements": [{
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": "配置已更新，新规则将在下次运行时生效"}
                }]
            })

        state["sessions"].pop(session_id, None)
        save_daemon_state(state)
        logger.info("会话已完成并清理")

    else:
        cat = action_value.get("category", "")
        choice = action_value.get("choice", "")
        if cat and choice in {"exclude", "important", "skip"}:
            session["choices"][cat] = choice
            save_daemon_state(state)
            logger.info(f"用户选择: {cat} → {choice}")

            message_id = session.get("message_id", "")
            if message_id:
                from optimize_rules import build_card_json
                new_card = build_card_json(
                    session["unclassified"],
                    session["choices"],
                    session["recommendations"],
                    session_id,
                )
                _update_feishu_card(message_id, new_card)


def handle_optimize_prompt(action_value: dict, state: dict):
    """处理 source=optimize_prompt 的回调"""
    session_id = action_value.get("session_id", "")
    action = action_value.get("action", "")

    session = state.get("sessions", {}).get(session_id)
    if not session or session.get("type") != "optimize_prompt":
        logger.warning(f"未找到 optimize_prompt 会话: {session_id}")
        return

    new_prompt = session.get("new_prompt", "")
    message_id = session.get("message_id", "")

    if action == "confirm":
        if not PROMPT_PATH.exists():
            logger.error("prompt.yaml 不存在")
            return

        backup_path = PROMPT_PATH.with_suffix(".yaml.bak")
        shutil.copy2(PROMPT_PATH, backup_path)
        logger.info(f"已备份 prompt 到 {backup_path}")

        with open(PROMPT_PATH, encoding="utf-8") as f:
            prompt_data = yaml.safe_load(f)

        prompt_data["judge_prompt"] = new_prompt

        with open(PROMPT_PATH, "w", encoding="utf-8") as f:
            yaml.dump(prompt_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        logger.info(f"prompt.yaml 已更新")

        if message_id:
            _update_feishu_card(message_id, {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "✅ Prompt 已更新"},
                    "template": "green"
                },
                "elements": [{
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": "新 prompt 将在下次运行时生效"}
                }]
            })

        state["sessions"].pop(session_id, None)
        save_daemon_state(state)
        logger.info("optimize_prompt 会话已完成并清理")

    elif action == "reject":
        if message_id:
            _update_feishu_card(message_id, {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "❌ Prompt 优化已放弃"},
                    "template": "red"
                },
                "elements": [{
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": "prompt 未修改"}
                }]
            })

        state["sessions"].pop(session_id, None)
        save_daemon_state(state)
        logger.info("optimize_prompt 会话已放弃并清理")


def main():
    from run import _load_dotenv
    _load_dotenv()

    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        logger.error("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")
        sys.exit(1)

    state = load_daemon_state()
    logger.info(f"加载了 {len(state.get('sessions', {}))} 个活跃会话")

    def handle_card_action(data):
        from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

        action_value = data.event.action.value
        source = action_value.get("source", "")

        logger.info(f"收到回调: source={source}, value={action_value}")

        if source == "feedback":
            handle_feedback(action_value)
        elif source == "recall":
            handle_recall(action_value)
        elif source == "optimize_rules":
            handle_optimize_rules(action_value, state)
        elif source == "optimize_prompt":
            handle_optimize_prompt(action_value, state)
        else:
            logger.warning(f"未知 source: {source}")

        return P2CardActionTriggerResponse()

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_card_action_trigger(handle_card_action)
        .build()
    )

    cli = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.DEBUG,
    )

    logger.info("feedback_daemon 已启动，等待飞书回调...")
    cli.start()


if __name__ == "__main__":
    main()
