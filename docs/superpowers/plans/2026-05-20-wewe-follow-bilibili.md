# wewe-follow B站UP主追踪 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 wewe-follow 扩展 B站 UP 主动态追踪（新视频 + 图文动态），与公众号追踪平级。

**Architecture:** 分平台独立脚本（`check_wechat.py` + `check_bili.py`），共享 config.yaml 和飞书推送基础设施，各自维护状态文件。公众号侧做纯重命名，B站侧新建完整脚本。

**Tech Stack:** Python 3, requests, PyYAML, B站公开 API

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `wewe-follow/SKILL.md` | 修改 | 更新脚本引用 + 新增B站触发流 |
| `wewe-follow/scripts/check.py` | **重命名** → `check_wechat.py` | 公众号检查，更新 STATE_FILE 路径 |
| `wewe-follow/scripts/check_bili.py` | **新建** | B站UP主检查 |
| `wewe-follow/scripts/config.yaml` | 修改 | `mps`→`wechat`，新增 `bili` 段 |
| `wewe-follow/scripts/data/last_check.json` | **重命名** → `wechat_last_check.json` | 公众号状态 |
| `wewe-follow/scripts/data/bili_last_check.json` | **新建** | B站状态（运行时自动创建） |

---

### Task 1: 重命名公众号文件和配置

**Files:**
- Modify: `wewe-follow/scripts/check.py` → `wewe-follow/scripts/check_wechat.py`
- Modify: `wewe-follow/scripts/config.yaml`
- Rename: `wewe-follow/scripts/data/last_check.json` → `wewe-follow/scripts/data/wechat_last_check.json`

- [ ] **Step 1: 重命名 check.py 为 check_wechat.py 并更新内部 STATE_FILE 路径**

```bash
cd /home/zify/myProject/oh-my-skill
git mv wewe-follow/scripts/check.py wewe-follow/scripts/check_wechat.py
```

然后修改 `check_wechat.py` 中的 STATE_FILE 路径定义：

```python
STATE_FILE = DATA_DIR / "wechat_last_check.json"
```

（原为 `last_check.json`）

- [ ] **Step 2: 重命名状态文件**

```bash
cd /home/zify/myProject/oh-my-skill
git mv wewe-follow/scripts/data/last_check.json wewe-follow/scripts/data/wechat_last_check.json
```

- [ ] **Step 3: 更新 config.yaml — mps → wechat，新增 bili 段**

替换 `wewe-follow/scripts/config.yaml` 完整内容：

```yaml
# 公众号（按名称匹配，留空则不限制）
wechat:
  - "猫笔刀"
  - "重燃阅读"

# B站UP主（按名称匹配，留空则不限制）
bili:
  - "稚晖君"
```

- [ ] **Step 4: 语法验证**

```bash
cd /home/zify/myProject/oh-my-skill && python3 -c "import py_compile; py_compile.compile('wewe-follow/scripts/check_wechat.py', doraise=True)"
```

Expected: 无错误输出。

- [ ] **Step 5: 提交**

```bash
cd /home/zify/myProject/oh-my-skill
git add wewe-follow/scripts/check_wechat.py wewe-follow/scripts/config.yaml wewe-follow/scripts/data/wechat_last_check.json
git rm wewe-follow/scripts/check.py wewe-follow/scripts/data/last_check.json
git commit -m "refactor: rename check.py→check_wechat.py, mps→wechat, prepare bili config"
```

---

### Task 2: 创建 check_bili.py — 导入、路径、工具函数

**Files:**
- Create: `wewe-follow/scripts/check_bili.py`

- [ ] **Step 1: 创建文件骨架**

```python
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
```

- [ ] **Step 2: 验证语法**

```bash
cd /home/zify/myProject/oh-my-skill && python3 -c "import py_compile; py_compile.compile('wewe-follow/scripts/check_bili.py', doraise=True)"
```

Expected: 无错误输出。

- [ ] **Step 3: 提交**

```bash
cd /home/zify/myProject/oh-my-skill
git add wewe-follow/scripts/check_bili.py
git commit -m "feat: add check_bili.py skeleton with utils and API constants"
```

---

### Task 3: 实现 B站 API 调用函数

**Files:**
- Modify: `wewe-follow/scripts/check_bili.py`

- [ ] **Step 1: 追加 search_up主 函数**

在 `_api_call` 函数之后追加：

```python

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
```

- [ ] **Step 2: 验证语法**

```bash
cd /home/zify/myProject/oh-my-skill && python3 -c "import py_compile; py_compile.compile('wewe-follow/scripts/check_bili.py', doraise=True)"
```

Expected: 无错误输出。

- [ ] **Step 3: 提交**

```bash
cd /home/zify/myProject/oh-my-skill
git add wewe-follow/scripts/check_bili.py
git commit -m "feat: add B站 API call functions — search, info, video, dynamic"
```

---

### Task 4: 实现状态对比和主流程

**Files:**
- Modify: `wewe-follow/scripts/check_bili.py`

- [ ] **Step 1: 追加状态对比和结果构建函数**

在 `fetch_latest_dynamic` 函数之后追加：

```python

# ═══════════════════════════════════════════════════════════
# 状态对比
# ═══════════════════════════════════════════════════════════

def compare_status(fresh_data: list[dict], state: dict) -> list[dict]:
    """将最新数据与本地状态对比，判定每个UP主的状态。"""
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
            "name": name,
            "uid": up["uid"],
            "sign": up.get("sign", ""),
            "face": up.get("face", ""),
            "status": status,
            "has_new_video": has_new_video,
            "has_new_dynamic": has_new_dynamic,
            "last_video": up.get("last_video"),
            "last_dynamic": up.get("last_dynamic"),
            "deep_link": f"https://space.bilibili.com/{up['uid']}",
        })

    # 检测已取关
    current_names = {up["name"] for up in fresh_data}
    for name, info in prev_accounts.items():
        if name not in current_names:
            results.append({
                "name": name,
                "uid": info.get("uid", ""),
                "sign": info.get("sign", ""),
                "face": info.get("face", ""),
                "status": "removed",
                "has_new_video": False,
                "has_new_dynamic": False,
                "last_video": info.get("last_video"),
                "last_dynamic": info.get("last_dynamic"),
                "deep_link": f"https://space.bilibili.com/{info.get('uid', '')}",
            })

    return results


def _video_changed(new_v: dict | None, old_v: dict | None) -> bool:
    """视频是否有更新。"""
    if not new_v:
        return False
    if not old_v:
        return True
    return new_v.get("pubdate", 0) > old_v.get("pubdate", 0)


def _dynamic_changed(new_d: dict | None, old_d: dict | None) -> bool:
    """动态是否有更新（id_str 不同即为新）。"""
    if not new_d:
        return False
    if not old_d:
        return True
    return new_d.get("id_str", "") != old_d.get("id_str", "")


def build_new_state(results: list[dict], state: dict, uid_map: dict[str, str]) -> dict:
    """从结果构建新的状态快照。"""
    accounts = {}
    for r in results:
        if r["status"] == "removed":
            continue
        name = r["name"]
        accounts[name] = {
            "uid": r["uid"],
            "name": name,
            "sign": r.get("sign", ""),
            "face": r.get("face", ""),
            "last_video": r.get("last_video"),
            "last_dynamic": r.get("last_dynamic"),
        }

    # 为 uid_map 中所有名称补齐缓存
    uid_cache = state.get("uid_cache", {})
    for name, uid in uid_map.items():
        uid_cache[name] = uid

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "uid_cache": uid_cache,
        "accounts": accounts,
    }
```

- [ ] **Step 2: 追加 main 函数**

在文件末尾追加：

```python

# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════

def main():
    load_env()

    # 1. 加载配置
    target_names = load_config()
    if not target_names:
        print(json.dumps({"error": "config.yaml 中 bili 段为空，请在配置中指定要追踪的UP主名称"}))
        sys.exit(1)

    # 2. 加载状态
    state = load_state()

    # 3. 名称 → UID
    uid_map, failures, state = resolve_uids(target_names, state)
    if failures:
        print(json.dumps({"error": f"未找到以下UP主: {', '.join(failures)}，请检查 config.yaml 中的名称"}))
        sys.exit(1)

    # 4. 遍历 UP 主获取数据
    fresh_data = []
    errors = []
    for name in target_names:
        uid = uid_map[name]
        try:
            info = fetch_up主_info(uid)
            video = fetch_latest_video(uid)
            time.sleep(0.5)
            dynamic = fetch_latest_dynamic(uid)
            time.sleep(0.5)
        except Exception as e:
            errors.append({"name": name, "uid": uid, "error": str(e)})
            continue

        fresh_data.append({
            "uid": uid,
            "name": name,
            "sign": info["sign"],
            "face": info["face"],
            "last_video": video,
            "last_dynamic": dynamic,
        })

    # 5. 状态对比
    results = compare_status(fresh_data, state)

    # 6. 更新状态文件
    new_state = build_new_state(results, state, uid_map)
    save_state(new_state)

    # 7. 输出
    summary = {
        "total": len(fresh_data),
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
    if errors:
        output["errors"] = errors

    print(json.dumps(output, ensure_ascii=False))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="B站UP主更新检查")
    parser.add_argument("--push", action="store_true", help="同时推送到飞书")
    args = parser.parse_args()

    output = main()
    if args.push:
        push_to_feishu(output["accounts"], output["summary"])
```

- [ ] **Step 3: 验证语法**

```bash
cd /home/zify/myProject/oh-my-skill && python3 -c "import py_compile; py_compile.compile('wewe-follow/scripts/check_bili.py', doraise=True)"
```

Expected: 无错误输出。

- [ ] **Step 4: 提交**

```bash
cd /home/zify/myProject/oh-my-skill
git add wewe-follow/scripts/check_bili.py
git commit -m "feat: add status comparison and main flow for check_bili"
```

---

### Task 5: 实现飞书推送

**Files:**
- Modify: `wewe-follow/scripts/check_bili.py`

- [ ] **Step 1: 在文件末尾（`if __name__ == "__main__":` 之前）追加飞书推送函数**

```python

# ═══════════════════════════════════════════════════════════
# 飞书推送
# ═══════════════════════════════════════════════════════════

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
    """组装飞书卡片并发送。"""
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

    total = summary["total"]
    updated = summary["updated"]
    new = summary["new"]
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"共 {total} 个 | 🆕更新 {updated} | 📌新增 {new}"}
    })
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"🕐 {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')}"
        }
    })

    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps({
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "📺 B站UP主更新"},
                "template": "blue"
            },
            "elements": elements
        }, ensure_ascii=False)
    }

    _send_feishu_card(token, chat_id, payload)
    print(json.dumps({"push_success": True, "pushed_count": len(results)}))
```

- [ ] **Step 2: 验证语法**

```bash
cd /home/zify/myProject/oh-my-skill && python3 -c "import py_compile; py_compile.compile('wewe-follow/scripts/check_bili.py', doraise=True)"
```

Expected: 无错误输出。

- [ ] **Step 3: 提交**

```bash
cd /home/zify/myProject/oh-my-skill
git add wewe-follow/scripts/check_bili.py
git commit -m "feat: add feishu push to check_bili"
```

---

### Task 6: 更新 SKILL.md

**Files:**
- Modify: `wewe-follow/SKILL.md`

- [ ] **Step 1: 更新 frontmatter 和标题，以及目录结构中脚本引用**

将 frontmatter description 和标题扩展，更新目录结构中对脚本的引用：

```markdown
---
name: wewe-follow
description: 追踪微信公众号和B站UP主的更新，检测新内容发布。关键词：公众号/微信读书/B站/bilibili/UP主/更新追踪
---

# wewe-follow

追踪微信公众号（微信读书书架）和 B站 UP 主的更新，通过对比本地状态检测新内容发布。
```

目录结构中：
- `check.py` → `check_wechat.py`
- `last_check.json` → `wechat_last_check.json`
- 新增 `check_bili.py` 和 `bili_last_check.json`

核心脚本文档更新为：

```markdown
| 脚本 | 触发方式 | 职责 |
|------|---------|------|
| `check_wechat.py` | agent 调用 | 查书架 → 过滤公众号 → 对比本地状态 → 输出更新摘要（`--push` 推送飞书） |
| `check_bili.py` | agent 调用 | 查B站UP主 → 获取最新视频/动态 → 对比本地状态 → 输出更新摘要（`--push` 推送飞书） |
```

- [ ] **Step 2: 将原有公众号流的教程名改为"检查公众号更新"**

原有的 "1. 检查更新" 改为 "1. 检查公众号更新"，触发词和命令引用更新为 `check_wechat.py`：

```
### 1. 检查公众号更新

**触发词**：`检查公众号` / `公众号更新` / `有没有新文章` / `公众号`

**工作流**：

1. 执行 `python3 scripts/check_wechat.py`，获取 JSON 输出
```

- [ ] **Step 3: 在展示规范之后、配置文件之前，新增 B站 业务流**

```markdown

### 3. 检查B站更新

**触发词**：`检查B站` / `B站更新` / `UP主更新` / `有没有新视频` / `B站`

**工作流**：

1. 执行 `python3 scripts/check_bili.py`，获取 JSON 输出
2. 按以下格式展示结果：

```
📺 B站UP主更新检查（共 2 个）

🆕 有更新（视频）：
1. 稚晖君 — 🎬新视频: 我做了个机械臂 / 2025-05-20 10:30  [打开](https://space.bilibili.com/20259914)

🆕 有更新（动态）：
2. LinusTechTips — 📝新动态: 本周硬件速递... / 2025-05-20 09:00  [打开](https://space.bilibili.com/123456)

✅ 无新内容：
3. 某UP主 — 最后视频: 2025-05-15 / 最后动态: 2025-05-18
```

3. 展示规则：
   - 先展示 🆕 有更新的（视频优先于动态），再展示 ✅ 无新内容的
   - 同一 UP 主同时有视频和动态更新时，合并在一个 UP 主项中，视频信息在前
   - 如果有 `new` 状态的 UP 主，用 📌 标记并提示"新关注的UP主"
   - 如果有 `removed` 状态的 UP 主，提示"以下UP主已不在追踪列表中：xxx"
   - 如果输出含 `errors` 字段，在末尾展示"⚠️ 以下UP主获取失败：xxx"

### 4. 飞书推送（B站）

**触发词**：`推送B站更新` / `B站推送`

**工作流**：

1. 执行 `python3 scripts/check_bili.py --push`
2. 发送飞书卡片，包含所有 UP 主的更新状态、视频标题、动态摘要和空间链接
3. 不需要额外回复，用户会在飞书收到卡片
```

- [ ] **Step 4: 在展示规范部分补充 B站 标记**

```markdown
- **B站标记**：🎬新视频 / 📝新动态
- **深度链接**：B站为 UP 主空间首页 `https://space.bilibili.com/{uid}`
```

- [ ] **Step 5: 更新配置文件部分**

```markdown
## 配置文件

`config.yaml` 格式：

```yaml
# 公众号（按名称匹配，留空则不限制）
wechat:
  - "猫笔刀"
  - "重燃阅读"

# B站UP主（按名称匹配，留空则不限制）
bili:
  - "稚晖君"
```
```

- [ ] **Step 6: 提交**

```bash
cd /home/zify/myProject/oh-my-skill
git add wewe-follow/SKILL.md
git commit -m "docs: update SKILL.md — rename wechat refs, add bilibili flows"
```

---

### Task 7: 端到端验证

**Files:**
- Verify: `wewe-follow/scripts/check_wechat.py`
- Verify: `wewe-follow/scripts/check_bili.py`
- Verify: `wewe-follow/scripts/config.yaml`

- [ ] **Step 1: 验证两个脚本语法正确且可 import**

```bash
cd /home/zify/myProject/oh-my-skill
python3 -c "
import py_compile
py_compile.compile('wewe-follow/scripts/check_wechat.py', doraise=True)
py_compile.compile('wewe-follow/scripts/check_bili.py', doraise=True)
print('Both OK')
"
```

Expected: `Both OK`

- [ ] **Step 2: 验证 check_wechat.py --help 正常**

```bash
cd /home/zify/myProject/oh-my-skill
cd wewe-follow/scripts && python3 check_wechat.py --help
```

Expected: 显示 usage 和 `--push` 参数。

- [ ] **Step 3: 验证 check_bili.py --help 正常**

```bash
cd /home/zify/myProject/oh-my-skill
cd wewe-follow/scripts && python3 check_bili.py --help
```

Expected: 显示 usage 和 `--push` 参数。

- [ ] **Step 4: 验证 check_bili.py 无 API Key 时的错误处理**

```bash
cd /home/zify/myProject/oh-my-skill/wewe-follow/scripts
# 临时屏蔽 .env 测试配置错误
WEREAD_API_KEY="" python3 -c "
import os, sys
os.environ.pop('WEREAD_API_KEY', None)
# 仅测试 bili 脚本的本地逻辑（不需要 API key）
exec(open('check_bili.py').read().split(\"if __name__ == '__main__':\")[0])
print('Module loaded OK')
"
```

Expected: `Module loaded OK`

- [ ] **Step 5: 确认文件结构正确**

```bash
cd /home/zify/myProject/oh-my-skill
ls -la wewe-follow/scripts/
ls -la wewe-follow/scripts/data/
```

Expected 存在：
- `check_wechat.py`
- `check_bili.py`
- `config.yaml`
- `data/wechat_last_check.json`
- 不存在 `check.py` 和 `data/last_check.json`

- [ ] **Step 6: 提交（如有残留变更）**

```bash
cd /home/zify/myProject/oh-my-skill
git status
```
