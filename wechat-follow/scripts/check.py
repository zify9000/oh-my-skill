"""检查公众号更新，输出 JSON 到 stdout + 写入 last_result.json"""
import json
import sys
from datetime import datetime, timezone

from common import (
    DATA_DIR, STATE_FILE, CREDENTIALS_PATH,
    setup_logging, load_env, load_target_config,
    format_timestamp, call_weread_api, get_api_key,
    MP_BOOK_ID_PREFIX,
)

logger = setup_logging("check")
LAST_RESULT_PATH = DATA_DIR / "last_result.json"


def filter_mp_accounts(books: list[dict]) -> list[dict]:
    return [b for b in books if b.get("bookId", "").startswith(MP_BOOK_ID_PREFIX)]


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"checked_at": None, "accounts": {}}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def compare_status(accounts: list[dict], prev_state: dict, tracked_names: set | None = None) -> list[dict]:
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


def build_new_state(accounts: list[dict]) -> dict:
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


def main():
    load_env(CREDENTIALS_PATH)
    api_key = get_api_key()

    shelf = call_weread_api("/shelf/sync", api_key)
    books = shelf.get("books", [])
    mp_accounts = filter_mp_accounts(books)

    target_mps = load_target_config()
    if target_mps is not None:
        mp_accounts = [acc for acc in mp_accounts if acc["title"] in target_mps]

    prev_state = load_state()
    results = compare_status(mp_accounts, prev_state, target_mps)
    new_state = build_new_state(mp_accounts)
    save_state(new_state)

    summary = {
        "total": len(mp_accounts),
        "updated": sum(1 for r in results if r["status"] == "updated"),
        "no_change": sum(1 for r in results if r["status"] == "no_change"),
        "new": sum(1 for r in results if r["status"] == "new"),
        "removed": sum(1 for r in results if r["status"] == "removed"),
    }

    output = {"checked_at": new_state["checked_at"], "summary": summary, "accounts": results}
    print(json.dumps(output, ensure_ascii=False))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LAST_RESULT_PATH, "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已写入 {LAST_RESULT_PATH}")


if __name__ == "__main__":
    main()
