"""检查B站UP主更新，输出 JSON 到 stdout + 写入 last_result.json"""
import json
import sys
import time
from datetime import datetime, timezone

from common import (
    DATA_DIR, STATE_FILE, CREDENTIALS_PATH,
    setup_logging, load_env, load_target_config,
    api_call, sign_wbi,
    SEARCH_URL, SPACE_INFO_URL, ARC_SEARCH_URL, DYNAMIC_FEED_URL,
    TRACKED_DYNAMIC_TYPES,
)

logger = setup_logging("check")
LAST_RESULT_PATH = DATA_DIR / "last_result.json"


# ── B站 API 操作 ──

def search_up(name: str) -> dict | None:
    data = api_call(SEARCH_URL, {"search_type": "bili_user", "keyword": name})
    results = data.get("data", {}).get("result", [])
    if not results:
        return None
    for r in results:
        if r.get("uname", "").strip() == name.strip():
            return {"uid": str(r["mid"]), "name": r["uname"]}
    first = results[0]
    return {"uid": str(first.get("mid", "")), "name": first.get("uname", "")}


def resolve_uids(names: list[str], state: dict) -> tuple:
    uid_cache = state.get("uid_cache", {})
    uid_map = {}
    failures = []
    for name in names:
        if name in uid_cache:
            uid_map[name] = uid_cache[name]
        else:
            try:
                result = search_up(name)
            except Exception:
                failures.append(name)
                continue
            if result is None:
                failures.append(name)
            else:
                uid_map[name] = result["uid"]
                uid_cache[name] = result["uid"]
            time.sleep(2)
    state["uid_cache"] = uid_cache
    return uid_map, failures, state


def fetch_up_info(uid: str) -> dict:
    data = api_call(SPACE_INFO_URL, {"mid": uid})
    d = data.get("data", {})
    return {"name": d.get("name", ""), "sign": d.get("sign", ""), "face": d.get("face", "")}


def fetch_latest_video(uid: str) -> dict | None:
    params = sign_wbi({"mid": uid, "ps": 1, "order": "pubdate"})
    data = api_call(ARC_SEARCH_URL, params, referer=f"https://space.bilibili.com/{uid}/video")
    vlist = data.get("data", {}).get("list", {}).get("vlist", [])
    if not vlist:
        return None
    v = vlist[0]
    return {"bvid": v.get("bvid", ""), "title": v.get("title", ""), "desc": v.get("description", ""), "cover": v.get("pic", ""), "pubdate": v.get("created", 0)}


def fetch_latest_dynamic(uid: str) -> dict | None:
    data = api_call(DYNAMIC_FEED_URL, {"host_mid": uid}, referer=f"https://space.bilibili.com/{uid}/dynamic")
    items = data.get("data", {}).get("items", [])
    for item in items:
        dtype = item.get("type", "")
        if dtype not in TRACKED_DYNAMIC_TYPES:
            continue
        modules = item.get("modules", {})
        desc = modules.get("module_dynamic", {}).get("desc", {})
        desc_text = desc.get("text", "") if isinstance(desc, dict) and desc else ""
        images = []
        major = modules.get("module_dynamic", {}).get("major", {})
        if major.get("type") == "MAJOR_TYPE_DRAW":
            for img in major.get("draw", {}).get("items", []):
                images.append(img.get("src", ""))
        author = modules.get("module_author", {})
        return {"id_str": item.get("id_str", ""), "content": desc_text, "images": images, "timestamp": author.get("pub_ts", 0)}
    return None


# ── 状态对比 ──

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"checked_at": None, "uid_cache": {}, "accounts": {}}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def compare_status(fresh_data: list[dict], state: dict) -> list[dict]:
    prev_accounts = state.get("accounts", {})
    results = []
    for up in fresh_data:
        name = up["name"]
        prev = prev_accounts.get(name)
        if prev is None:
            status = "new"
            has_new_video = up["last_video"] is not None
            has_new_dynamic = up["last_dynamic"] is not None
        else:
            has_new_video = _video_changed(up.get("last_video"), prev.get("last_video"))
            has_new_dynamic = _dynamic_changed(up.get("last_dynamic"), prev.get("last_dynamic"))
            status = "updated" if (has_new_video or has_new_dynamic) else "no_change"
        results.append({
            "name": name, "uid": up["uid"], "sign": up.get("sign", ""), "face": up.get("face", ""),
            "status": status, "has_new_video": has_new_video, "has_new_dynamic": has_new_dynamic,
            "last_video": up.get("last_video"), "last_dynamic": up.get("last_dynamic"),
            "deep_link": f"https://space.bilibili.com/{up['uid']}",
        })
    current_names = {up["name"] for up in fresh_data}
    for name, info in prev_accounts.items():
        if name not in current_names:
            results.append({
                "name": name, "uid": info.get("uid", ""), "sign": info.get("sign", ""), "face": info.get("face", ""),
                "status": "removed", "has_new_video": False, "has_new_dynamic": False,
                "last_video": info.get("last_video"), "last_dynamic": info.get("last_dynamic"),
                "deep_link": f"https://space.bilibili.com/{info.get('uid', '')}",
            })
    return results


def _video_changed(new_v, old_v) -> bool:
    if not new_v:
        return False
    if not old_v:
        return True
    return new_v.get("pubdate", 0) > old_v.get("pubdate", 0)


def _dynamic_changed(new_d, old_d) -> bool:
    if not new_d:
        return False
    if not old_d:
        return True
    return new_d.get("id_str", "") != old_d.get("id_str", "")


def build_new_state(results: list[dict], state: dict, uid_map: dict) -> dict:
    accounts = {}
    for r in results:
        if r["status"] == "removed":
            continue
        accounts[r["name"]] = {
            "uid": r["uid"], "name": r["name"], "sign": r.get("sign", ""), "face": r.get("face", ""),
            "last_video": r.get("last_video"), "last_dynamic": r.get("last_dynamic"),
        }
    uid_cache = state.get("uid_cache", {})
    for name, uid in uid_map.items():
        uid_cache[name] = uid
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "uid_cache": uid_cache, "accounts": accounts}


# ── 主流程 ──

def main():
    load_env(CREDENTIALS_PATH)

    target_names = load_target_config()
    if not target_names:
        print(json.dumps({"error": "base.yaml 中 follow 段为空"}))
        sys.exit(1)

    state = load_state()
    uid_map, failures, state = resolve_uids(target_names, state)
    if failures:
        print(json.dumps({"error": f"未找到: {', '.join(failures)}"}))
        sys.exit(1)

    fresh_data = []
    errors = []
    for name in target_names:
        uid = uid_map[name]
        try:
            info = fetch_up_info(uid)
            time.sleep(5)
            video = fetch_latest_video(uid)
            time.sleep(5)
            dynamic = fetch_latest_dynamic(uid)
            time.sleep(5)
        except Exception as e:
            # API 部分失败时用旧状态回退，避免误标为 removed
            errors.append({"name": name, "uid": uid, "error": str(e)})
            prev = state.get("accounts", {}).get(name, {})
            fresh_data.append({
                "uid": uid, "name": name,
                "sign": prev.get("sign", ""), "face": prev.get("face", ""),
                "last_video": prev.get("last_video"), "last_dynamic": prev.get("last_dynamic"),
            })
            continue
        fresh_data.append({"uid": uid, "name": name, "sign": info["sign"], "face": info["face"], "last_video": video, "last_dynamic": dynamic})

    results = compare_status(fresh_data, state)
    new_state = build_new_state(results, state, uid_map)
    save_state(new_state)

    summary = {
        "total": len(fresh_data),
        "updated": sum(1 for r in results if r["status"] == "updated"),
        "no_change": sum(1 for r in results if r["status"] == "no_change"),
        "new": sum(1 for r in results if r["status"] == "new"),
        "removed": sum(1 for r in results if r["status"] == "removed"),
    }

    output = {"checked_at": new_state["checked_at"], "summary": summary, "accounts": results}
    if errors:
        output["errors"] = errors

    print(json.dumps(output, ensure_ascii=False))

    # 写入 last_result.json 供 push.py 使用
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LAST_RESULT_PATH, "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已写入 {LAST_RESULT_PATH}")


if __name__ == "__main__":
    main()
