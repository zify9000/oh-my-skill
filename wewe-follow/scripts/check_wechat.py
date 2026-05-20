"""检查微信读书书架上公众号的更新状态，可选推送飞书卡片。

调用微信读书 API 获取书架，过滤公众号条目，
对比本地 wechat_last_check.json 判断是否有新文章更新。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ── 路径常量 ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
STATE_FILE = DATA_DIR / "wechat_last_check.json"
ENV_FILE = SCRIPT_DIR / ".env"
CONFIG_FILE = SCRIPT_DIR / "config.yaml"

# ── API 常量 ──────────────────────────────────────────────
GATEWAY_URL = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.3"

# 公众号 bookId 前缀
MP_BOOK_ID_PREFIX = "MP_WXS_"


def load_env():
    """从 .env 文件加载环境变量。"""
    if not ENV_FILE.exists():
        print(json.dumps({"error": f".env 文件不存在: {ENV_FILE}，请参考 .env.example 创建"}))
        sys.exit(1)

    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def load_config() -> set:
    """读取 config.yaml，返回公众号名称集合。空则返回 None 表示全部追踪。"""
    if not CONFIG_FILE.exists():
        return None
    import yaml
    with open(CONFIG_FILE) as f:
        cfg = yaml.safe_load(f) or {}
    mps = cfg.get("wechat", [])
    return set(mps) if mps else None


def get_api_key():
    """获取微信读书 API Key。"""
    api_key = os.environ.get("weread_api_key", "")
    if not api_key:
        print(json.dumps({"error": "weread_api_key 未设置，请在 .env 中配置"}))
        sys.exit(1)
    return api_key


def call_weread_api(api_name: str, api_key: str, **params) -> dict:
    """调用微信读书 Agent Gateway。"""
    body = {"api_name": api_name, "skill_version": SKILL_VERSION, **params}
    resp = requests.post(
        GATEWAY_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode", 0) != 0:
        print(json.dumps({"error": f"API 错误: {data.get('errmsg', '未知错误')}", "errcode": data.get("errcode")}))
        sys.exit(1)
    return data


def filter_mp_accounts(books: list[dict]) -> list[dict]:
    """从书架书籍列表中过滤出公众号条目。"""
    return [b for b in books if b.get("bookId", "").startswith(MP_BOOK_ID_PREFIX)]


def load_state() -> dict:
    """读取上次检查的状态文件。"""
    if not STATE_FILE.exists():
        return {"checked_at": None, "accounts": {}}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state: dict):
    """保存状态到文件。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def compare_status(accounts: list[dict], prev_state: dict, tracked_names: set | None = None) -> list[dict]:
    """对比当前公众号列表与上次状态，确定更新状态。"""
    prev_accounts = prev_state.get("accounts", {})
    results = []

    for acc in accounts:
        name = acc["title"]
        update_time = acc.get("updateTime", 0)
        prev = prev_accounts.get(name)

        if prev is None:
            status = "new"
        elif format_timestamp(update_time) > prev.get("updateTime", ""):
            status = "updated"
        else:
            status = "no_change"

        read_time = acc.get("readUpdateTime", 0)
        results.append({
            "name": name,
            "bookId": acc["bookId"],
            "status": status,
            "last_update": format_timestamp(update_time),
            "last_read": format_timestamp(read_time),
            "has_unread": update_time > read_time if update_time and read_time else False,
            "cover": acc.get("cover", ""),
        })

    # 检测已取关的公众号（仅限配置范围内的）
    current_names = {acc["title"] for acc in accounts}
    for name, info in prev_accounts.items():
        if name not in current_names and (tracked_names is None or name in tracked_names):
            results.append({
                "name": name,
                "bookId": info.get("bookId", ""),
                "status": "removed",
                "last_update": info.get("updateTime", ""),
                "cover": info.get("cover", ""),
            })

    return results


def format_timestamp(ts: int) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def build_new_state(accounts: list[dict]) -> dict:
    """构建新的状态快照。"""
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "accounts": {
            acc["title"]: {
                "bookId": acc["bookId"],
                "updateTime": format_timestamp(acc.get("updateTime", 0)),
            }
            for acc in accounts
        },
    }


def _get_feishu_token() -> str | None:
    app_id = os.environ.get("feishu_app_id", "")
    app_secret = os.environ.get("feishu_app_secret", "")
    if not app_id or not app_secret:
        return None
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    return resp.json()["tenant_access_token"]


def _send_feishu_card(token: str, chat_id: str, payload: dict):
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"飞书发送失败: code={result.get('code')} msg={result.get('msg')}")


def push_to_feishu(results: list[dict], summary: dict):
    chat_id = os.environ.get("feishu_chat_id", "")
    if not chat_id:
        print(json.dumps({"push_error": "feishu_chat_id 未配置"}))
        return

    token = _get_feishu_token()
    if not token:
        print(json.dumps({"push_error": "飞书 token 获取失败，检查 feishu_app_id / feishu_app_secret"}))
        return

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

    total = summary["total"]
    updated = summary["updated"]
    new = summary["new"]
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"共 {total} 个 | 🆕更新 {updated} | 📌新增 {new}"}
    })
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"🕐 {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')}"}
    })

    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps({
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "📚 公众号更新"},
                "template": "blue"
            },
            "elements": elements
        }, ensure_ascii=False)
    }

    _send_feishu_card(token, chat_id, payload)
    print(json.dumps({"push_success": True, "pushed_count": len(results)}))


def main():
    load_env()
    api_key = get_api_key()

    # 1. 获取书架
    shelf = call_weread_api("/shelf/sync", api_key)
    books = shelf.get("books", [])

    # 2. 过滤公众号
    mp_accounts = filter_mp_accounts(books)

    # 2.5 按配置文件进一步过滤
    target_mps = load_config()
    if target_mps is not None:
        mp_accounts = [acc for acc in mp_accounts if acc["title"] in target_mps]

    # 3. 读取上次状态
    prev_state = load_state()

    # 4. 对比状态
    results = compare_status(mp_accounts, prev_state, target_mps)

    # 5. 更新状态文件
    new_state = build_new_state(mp_accounts)
    save_state(new_state)

    # 6. 输出结果
    summary = {
        "total": len(mp_accounts),
        "updated": sum(1 for r in results if r["status"] == "updated"),
        "no_change": sum(1 for r in results if r["status"] == "no_change"),
        "new": sum(1 for r in results if r["status"] == "new"),
        "removed": sum(1 for r in results if r["status"] == "removed"),
    }

    output = {
        "checked_at": new_state["checked_at"],
        "summary": summary,
        "accounts": results,
    }

    print(json.dumps(output, ensure_ascii=False))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="公众号更新检查")
    parser.add_argument("--push", action="store_true", help="同时推送到飞书")
    args = parser.parse_args()

    output = main()
    if args.push:
        push_to_feishu(output["accounts"], output["summary"])
