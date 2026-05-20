"""检查B站UP主的更新状态（新视频、图文动态），可选推送飞书卡片。

通过 B站公开 API 获取 UP 主最新视频和图文动态，
对比本地 bili_last_check.json 判断是否有新发布。
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ── 路径常量 ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
STATE_FILE = DATA_DIR / "bili_last_check.json"
ENV_FILE = SCRIPT_DIR / ".env"
CONFIG_FILE = SCRIPT_DIR / "config.yaml"

# ── API 常量 ──────────────────────────────────────────────
BILI_API = "https://api.bilibili.com"
SEARCH_URL = f"{BILI_API}/x/web-interface/search/type"
SPACE_INFO_URL = f"{BILI_API}/x/space/acc/info"
ARC_SEARCH_URL = f"{BILI_API}/x/space/arc/search"
DYNAMIC_FEED_URL = f"{BILI_API}/x/polymer/web-dynamic/v1/feed/space"

# ── 动态类型过滤 ──────────────────────────────────────────
# DRAW=图文, WORD=纯文字。排除 REPOST=转发, AV=视频发布(已由arc/search覆盖)
TRACKED_DYNAMIC_TYPES = {"DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_WORD"}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def load_env():
    """从 .env 文件加载环境变量到 os.environ。"""
    if not ENV_FILE.exists():
        return
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def load_config() -> list | None:
    """读取 config.yaml 中 bili 段。返回名称列表；空则返回 None。"""
    if not CONFIG_FILE.exists():
        return None
    import yaml
    with open(CONFIG_FILE) as f:
        cfg = yaml.safe_load(f) or {}
    bili_list = cfg.get("bili", [])
    return bili_list if bili_list else None


def load_state() -> dict:
    """读取 bili_last_check.json。"""
    if not STATE_FILE.exists():
        return {"checked_at": None, "uid_cache": {}, "accounts": {}}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state: dict):
    """保存状态到 bili_last_check.json。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def bili_headers() -> dict:
    """B站 API 基础请求头。"""
    return {
        "User-Agent": USER_AGENT,
        "Referer": "https://www.bilibili.com/",
    }


def format_timestamp(ts: int) -> str:
    """Unix 时间戳 → Asia/Shanghai 时间字符串。"""
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _api_call(url: str, params: dict, timeout: int = 15) -> dict:
    """封装 GET 请求 + 错误检测。"""
    resp = requests.get(url, params=params, headers=bili_headers(), timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    code = data.get("code", -1)
    if code != 0:
        raise RuntimeError(f"B站API错误: code={code} message={data.get('message', 'unknown')} url={url}")
    return data


# ═══════════════════════════════════════════════════════════
# B站 API 调用
# ═══════════════════════════════════════════════════════════

def search_up主(name: str) -> dict | None:
    """按名称搜索UP主，返回 {'uid': str, 'name': str} 或 None。"""
    data = _api_call(SEARCH_URL, {"search_type": "bili_user", "keyword": name})
    results = data.get("data", {}).get("result", [])
    if not results:
        return None
    # 精确匹配优先
    for r in results:
        if r.get("uname", "").strip() == name.strip():
            return {"uid": str(r["mid"]), "name": r["uname"]}
    # 无精确匹配，取第一个
    first = results[0]
    return {"uid": str(first["mid"]), "name": first["uname"]}


def resolve_uids(names: list[str], state: dict) -> tuple[dict[str, str], list, dict]:
    """解析名称→UID，优先从缓存读取，未命中则搜索。

    返回 (uid_map: {name: uid}, failures: [name], updated_state)
    """
    uid_cache = state.get("uid_cache", {})
    uid_map = {}
    failures = []
    dirty = False

    for name in names:
        if name in uid_cache:
            uid_map[name] = uid_cache[name]
        else:
            result = search_up主(name)
            if result is None:
                failures.append(name)
            else:
                uid_map[name] = result["uid"]
                uid_cache[name] = result["uid"]
                dirty = True
            time.sleep(0.5)  # 搜索接口限频

    state["uid_cache"] = uid_cache
    return uid_map, failures, state


def fetch_up主_info(uid: str) -> dict:
    """获取 UP 主基本信息 name/sign/face。"""
    data = _api_call(SPACE_INFO_URL, {"mid": uid})
    d = data["data"]
    return {
        "name": d.get("name", ""),
        "sign": d.get("sign", ""),
        "face": d.get("face", ""),
    }


def fetch_latest_video(uid: str) -> dict | None:
    """获取 UP 主最新视频。返回 {bvid, title, desc, cover, pubdate} 或 None。"""
    data = _api_call(ARC_SEARCH_URL, {"mid": uid, "ps": 1, "order": "pubdate"})
    vlist = data.get("data", {}).get("list", {}).get("vlist", [])
    if not vlist:
        return None
    v = vlist[0]
    return {
        "bvid": v.get("bvid", ""),
        "title": v.get("title", ""),
        "desc": v.get("description", ""),
        "cover": v.get("pic", ""),
        "pubdate": v.get("created", 0),
    }


def fetch_latest_dynamic(uid: str) -> dict | None:
    """获取 UP 主最新图文动态（排除转发和视频发布）。"""
    data = _api_call(DYNAMIC_FEED_URL, {"host_mid": uid, "offset": ""})
    items = data.get("data", {}).get("items", [])
    for item in items:
        dtype = item.get("type", "")
        if dtype not in TRACKED_DYNAMIC_TYPES:
            continue
        modules = item.get("modules", {})
        desc_text = ""
        desc = modules.get("module_dynamic", {}).get("desc", {})
        if desc:
            desc_text = desc.get("text", "")

        images = []
        major = modules.get("module_dynamic", {}).get("major", {})
        if major.get("type") == "MAJOR_TYPE_DRAW":
            for img in major.get("draw", {}).get("items", []):
                images.append(img.get("src", ""))

        author = modules.get("module_author", {})
        return {
            "id_str": item.get("id_str", ""),
            "content": desc_text,
            "images": images,
            "timestamp": author.get("pub_ts", 0),
        }
    return None
