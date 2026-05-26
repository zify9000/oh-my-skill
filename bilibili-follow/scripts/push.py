"""读取 last_result.json，发送飞书卡片"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

from common import (
    DATA_DIR, FEISHU_ENV_PATH,
    setup_logging, load_env, format_timestamp,
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
        line = f"{emoji} **{name}**"

        if r.get("has_new_video") and r.get("last_video"):
            v = r["last_video"]
            line += f" 🎬{v['title']} | {format_timestamp(v['pubdate'])}"
        if r.get("has_new_dynamic") and r.get("last_dynamic"):
            d = r["last_dynamic"]
            text = d['content'][:60].replace('\n', ' ') if d['content'] else "[图片]"
            line += f" 📝{text} | {format_timestamp(d['timestamp'])}"

        if r["status"] == "no_change":
            if r.get("last_video"):
                line += f" 最后视频: {format_timestamp(r['last_video']['pubdate'])}"
            if r.get("last_dynamic"):
                line += f" 最后动态: {format_timestamp(r['last_dynamic']['timestamp'])}"

        line += f"  [打开]({r['deep_link']})"
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": line}})
        elements.append({"tag": "hr"})

    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"共 {summary['total']} 个 | 🆕更新 {summary['updated']} | 📌新增 {summary['new']}"}})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"🕐 {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')}"}})

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "📺 B站UP主更新"}, "template": "blue"},
        "elements": elements,
    }


def main():
    if not LAST_RESULT_PATH.exists():
        logger.error("last_result.json 不存在，请先运行 check.py")
        sys.exit(1)

    with open(LAST_RESULT_PATH) as f:
        data = json.load(f)

    load_env(FEISHU_ENV_PATH)
    app_id = os.environ.get("feishu_app_id", "")
    app_secret = os.environ.get("feishu_app_secret", "")
    chat_id = os.environ.get("feishu_chat_id", "")

    if not app_id or not app_secret or not chat_id:
        logger.error("飞书凭据不完整，请检查 env/.feishu.env")
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
