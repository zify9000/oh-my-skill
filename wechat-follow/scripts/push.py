"""读取 last_result.json，发送飞书卡片"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

from common import (
    DATA_DIR, FEISHU_ENV_PATH,
    setup_logging, load_env, load_base_config,
    get_feishu_token, send_feishu_message,
)

logger = setup_logging("push")
LAST_RESULT_PATH = DATA_DIR / "last_result.json"


def build_card(results: list[dict], summary: dict) -> dict:
    status_emoji = {"updated": "🆕", "new": "📌", "no_change": "✅", "removed": "❌"}

    elements = []
    for r in results:
        name = r["name"]
        emoji = status_emoji.get(r["status"], "❓")
        update = r.get("last_update", "")
        read = r.get("last_read", "")
        unread = "🔴未读" if r.get("has_unread") else "✅已读"
        link = f"weread://reading?bId={r['bookId']}"

        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"{emoji} **{name}** 更新:{update} 阅读:{read} {unread}  [打开]({link})"}
        })
        elements.append({"tag": "hr"})

    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"共 {summary['total']} 个 | 🆕更新 {summary['updated']} | 📌新增 {summary['new']}"}})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"🕐 {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')}"}})

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "📚 公众号更新"}, "template": "blue"},
        "elements": elements,
    }


def main():
    parser = argparse.ArgumentParser(description="公众号更新推送")
    parser.add_argument("--feishu-app-id", default="", help="agent 模式：飞书应用 ID")
    parser.add_argument("--feishu-app-secret", default="", help="agent 模式：飞书应用密钥")
    parser.add_argument("--feishu-chat-id", default="", help="agent 模式：飞书群聊 ID")
    args = parser.parse_args()

    if not LAST_RESULT_PATH.exists():
        logger.error("last_result.json 不存在，请先运行 check.py")
        sys.exit(1)

    with open(LAST_RESULT_PATH) as f:
        data = json.load(f)

    config = load_base_config()
    cred_source = config.get("feishu_credential_source", "env")

    if cred_source == "agent":
        app_id, app_secret, chat_id = args.feishu_app_id, args.feishu_app_secret, args.feishu_chat_id
    else:
        load_env(FEISHU_ENV_PATH)
        app_id = os.environ.get("feishu_app_id", "")
        app_secret = os.environ.get("feishu_app_secret", "")
        chat_id = os.environ.get("feishu_chat_id", "")

    if not app_id or not app_secret or not chat_id:
        logger.error("飞书凭据不完整")
        sys.exit(1)

    token = get_feishu_token(app_id, app_secret)
    card = build_card(data["accounts"], data["summary"])
    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    send_feishu_message(token, chat_id, payload)
    logger.info("飞书卡片发送成功")


if __name__ == "__main__":
    main()
