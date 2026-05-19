"""检查微信读书书架上公众号的更新状态。

调用微信读书 API 获取书架，过滤公众号条目，
对比本地 last_check.json 判断是否有新文章更新。
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── 路径常量 ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
STATE_FILE = DATA_DIR / "last_check.json"
ENV_FILE = SCRIPT_DIR / ".env"

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


def get_api_key():
    """获取微信读书 API Key。"""
    api_key = os.environ.get("WEREAD_API_KEY", "")
    if not api_key:
        print(json.dumps({"error": "WEREAD_API_KEY 未设置，请在 .env 中配置"}))
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


def compare_status(accounts: list[dict], prev_state: dict) -> list[dict]:
    """对比当前公众号列表与上次状态，确定更新状态。"""
    prev_accounts = prev_state.get("accounts", {})
    results = []

    for acc in accounts:
        name = acc["title"]
        update_time = acc.get("updateTime", 0)
        prev = prev_accounts.get(name)

        if prev is None:
            status = "new"
        elif update_time > prev.get("updateTime", 0):
            status = "updated"
        else:
            status = "no_change"

        results.append({
            "name": name,
            "bookId": acc["bookId"],
            "status": status,
            "last_update": format_timestamp(update_time),
            "cover": acc.get("cover", ""),
        })

    # 检测已取关的公众号
    current_names = {acc["title"] for acc in accounts}
    for name, info in prev_accounts.items():
        if name not in current_names:
            results.append({
                "name": name,
                "bookId": info.get("bookId", ""),
                "status": "removed",
                "last_update": format_timestamp(info.get("updateTime", 0)),
                "cover": info.get("cover", ""),
            })

    return results


def format_timestamp(ts: int) -> str:
    """Unix 时间戳转 YYYY-MM-DD 格式。"""
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def build_new_state(accounts: list[dict]) -> dict:
    """构建新的状态快照。"""
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "accounts": {
            acc["title"]: {
                "bookId": acc["bookId"],
                "updateTime": acc.get("updateTime", 0),
                "cover": acc.get("cover", ""),
            }
            for acc in accounts
        },
    }


def main():
    load_env()
    api_key = get_api_key()

    # 1. 获取书架
    shelf = call_weread_api("/shelf/sync", api_key)
    books = shelf.get("books", [])

    # 2. 过滤公众号
    mp_accounts = filter_mp_accounts(books)

    # 3. 读取上次状态
    prev_state = load_state()

    # 4. 对比状态
    results = compare_status(mp_accounts, prev_state)

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


if __name__ == "__main__":
    main()
