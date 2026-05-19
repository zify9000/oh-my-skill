"""查看指定公众号的详细信息。

通过公众号名称或 bookId 查看详情，包括简介、更新时间、阅读进度。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── 路径常量 ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"

# ── API 常量 ──────────────────────────────────────────────
GATEWAY_URL = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.3"
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


def find_mp_in_shelf(name: str, api_key: str) -> dict | None:
    """通过公众号名称在书架中查找，返回完整的书架条目（含 updateTime）。"""
    shelf = call_weread_api("/shelf/sync", api_key)
    for book in shelf.get("books", []):
        if book.get("title") == name and book.get("bookId", "").startswith(MP_BOOK_ID_PREFIX):
            return book
    return None


def format_timestamp(ts: int) -> str:
    """Unix 时间戳转 YYYY-MM-DD 格式。"""
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def main():
    parser = argparse.ArgumentParser(description="查看公众号详情")
    parser.add_argument("--name", help="公众号名称")
    parser.add_argument("--id", dest="book_id", help="公众号 bookId（MP_WXS_xxxx）")
    args = parser.parse_args()

    if not args.name and not args.book_id:
        print(json.dumps({"error": "请指定 --name 或 --id 参数"}))
        sys.exit(1)

    load_env()
    api_key = get_api_key()

    # 获取书架数据（updateTime 在书架条目中，不在 /book/info 中）
    shelf_item = None

    if args.name:
        shelf_item = find_mp_in_shelf(args.name, api_key)
        if not shelf_item:
            print(json.dumps({"error": f"书架上未找到公众号: {args.name}"}))
            sys.exit(1)
        book_id = shelf_item["bookId"]
    else:
        book_id = args.book_id
        # 用 --id 时也需从书架获取 updateTime，遍历一次
        shelf = call_weread_api("/shelf/sync", api_key)
        for book in shelf.get("books", []):
            if book.get("bookId") == book_id:
                shelf_item = book
                break

    # 获取书籍详情（简介等）
    info = call_weread_api("/book/info", api_key, bookId=book_id)

    # 获取阅读进度（最后阅读时间在 progress.book.updateTime 中）
    progress = call_weread_api("/book/getprogress", api_key, bookId=book_id)

    # 公众号最后更新时间来自书架条目
    mp_update_time = shelf_item.get("updateTime", 0) if shelf_item else 0
    # 最后阅读时间来自进度接口的 book.updateTime
    read_time = progress.get("book", {}).get("updateTime", 0)

    last_update = format_timestamp(mp_update_time)
    last_read = format_timestamp(read_time) if read_time else ""

    # 判断是否有未读内容：公众号更新时间 > 最后阅读时间
    has_unread = mp_update_time > read_time if mp_update_time and read_time else False

    output = {
        "name": info.get("title", ""),
        "bookId": book_id,
        "intro": info.get("intro", ""),
        "cover": info.get("cover", ""),
        "last_update": last_update,
        "last_read": last_read,
        "has_unread": has_unread,
        "deep_link": f"weread://reading?bId={book_id}",
    }

    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
