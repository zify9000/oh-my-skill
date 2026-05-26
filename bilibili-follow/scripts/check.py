"""检查B站UP主更新，输出 JSON 到 stdout + 写入 last_result.json

支持 --dry-run 模式：不发起任何API请求，直接用上次状态重新对比输出。
"""
import argparse
import json
import random
import sys
from datetime import datetime, timezone

from bili_client import BiliClient
from common import (
    DATA_DIR, STATE_FILE,
    setup_logging, load_base_config, load_target_config,
)

logger = setup_logging("check")
LAST_RESULT_PATH = DATA_DIR / "last_result.json"


# ── UID 解析 ──

def resolve_uids(client: BiliClient, names: list[str], state: dict) -> tuple:
    """将UP主名称解析为UID，优先使用缓存"""
    uid_cache = state.get("uid_cache", {})
    uid_map = {}
    failures = []
    for name in names:
        if name in uid_cache:
            uid_map[name] = uid_cache[name]
        else:
            try:
                result = client.search_up(name)
            except Exception as e:
                logger.warning(f"搜索UP主失败: {name} - {e}")
                failures.append(name)
                continue
            if result is None:
                failures.append(name)
            else:
                uid_map[name] = result["uid"]
                uid_cache[name] = result["uid"]
    state["uid_cache"] = uid_cache
    return uid_map, failures, state


# ── 状态管理 ──

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"checked_at": None, "uid_cache": {}, "accounts": {}}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── 状态对比 ──

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
    # 已移除的UP主
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
    parser = argparse.ArgumentParser(description="检查B站UP主更新")
    parser.add_argument("--dry-run", action="store_true",
                        help="不发起API请求，用上次缓存的状态重新对比输出")
    args = parser.parse_args()

    target_names = load_target_config()
    if not target_names:
        print(json.dumps({"error": "base.yaml 中 follow 段为空"}))
        sys.exit(1)

    state = load_state()

    # ── dry-run 模式：直接用上次结果重新输出 ──
    if args.dry_run:
        logger.info("dry-run 模式：跳过API请求，使用上次缓存数据")
        prev_accounts = state.get("accounts", {})
        if not prev_accounts:
            print(json.dumps({"error": "无缓存数据，请先正常运行一次"}))
            sys.exit(1)
        fresh_data = []
        for name, info in prev_accounts.items():
            fresh_data.append({
                "uid": info["uid"], "name": name,
                "sign": info.get("sign", ""), "face": info.get("face", ""),
                "last_video": info.get("last_video"), "last_dynamic": info.get("last_dynamic"),
            })
        uid_map = {name: info["uid"] for name, info in prev_accounts.items()}
        _output_results(fresh_data, state, uid_map, [])
        return

    # ── 正常模式 ──
    config = load_base_config()
    bili_config = config.get("bili", {})
    client = BiliClient(config=bili_config)

    uid_map, failures, state = resolve_uids(client, target_names, state)
    if failures:
        print(json.dumps({"error": f"未找到: {', '.join(failures)}"}))
        sys.exit(1)

    # 随机化检查顺序，避免固定请求模式
    shuffled_names = list(target_names)
    random.shuffle(shuffled_names)

    fresh_data = []
    errors = []
    for name in shuffled_names:
        uid = uid_map[name]
        try:
            info = client.fetch_up_info(uid)
            video = client.fetch_latest_video(uid)
            dynamic = client.fetch_latest_dynamic(uid)
        except Exception as e:
            # API 部分失败时用旧状态回退，避免误标为 removed
            logger.warning(f"获取 {name} 数据失败: {e}")
            errors.append({"name": name, "uid": uid, "error": str(e)})
            prev = state.get("accounts", {}).get(name, {})
            fresh_data.append({
                "uid": uid, "name": name,
                "sign": prev.get("sign", ""), "face": prev.get("face", ""),
                "last_video": prev.get("last_video"), "last_dynamic": prev.get("last_dynamic"),
            })
            continue
        fresh_data.append({
            "uid": uid, "name": name,
            "sign": info["sign"], "face": info["face"],
            "last_video": video, "last_dynamic": dynamic,
        })

    _output_results(fresh_data, state, uid_map, errors)


def _output_results(fresh_data: list[dict], state: dict, uid_map: dict, errors: list[dict]):
    """对比状态、保存、输出结果"""
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
