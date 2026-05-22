"""查询公众号详情（按名称或 bookId）"""
import argparse
import json
import sys

from common import (
    CREDENTIALS_PATH, setup_logging, load_env, format_timestamp,
    call_weread_api, get_api_key, MP_BOOK_ID_PREFIX,
)

logger = setup_logging("detail")


def get_shelf_books(api_key: str) -> list[dict]:
    shelf = call_weread_api("/shelf/sync", api_key)
    return shelf.get("books", [])


def filter_mp(books: list[dict]) -> list[dict]:
    return [b for b in books if b.get("bookId", "").startswith(MP_BOOK_ID_PREFIX)]


def find_by_name(books: list[dict], name: str) -> dict | None:
    for b in books:
        if b.get("title") == name:
            return b
    return None


def find_by_bookid(books: list[dict], book_id: str) -> dict | None:
    for b in books:
        if b.get("bookId") == book_id:
            return b
    return None


def get_book_detail(api_key: str, book_id: str) -> dict:
    return call_weread_api("/book/info", api_key, bookId=book_id)


def main():
    parser = argparse.ArgumentParser(description="公众号详情查询")
    parser.add_argument("--name", default="", help="公众号名称")
    parser.add_argument("--book-id", default="", help="bookId")
    args = parser.parse_args()

    load_env(CREDENTIALS_PATH)
    api_key = get_api_key()

    books = get_shelf_books(api_key)
    mp_books = filter_mp(books)

    target = None
    if args.book_id:
        target = find_by_bookid(mp_books, args.book_id)
    elif args.name:
        target = find_by_name(mp_books, args.name)
    else:
        print(json.dumps({"error": "请提供 --name 或 --book-id"}))
        sys.exit(1)

    if not target:
        print(json.dumps({"error": "未找到匹配的公众号"}))
        sys.exit(1)

    detail = get_book_detail(api_key, target["bookId"])
    update_time = target.get("updateTime", 0)
    read_time = target.get("readUpdateTime", 0)

    result = {
        "name": target["title"],
        "bookId": target["bookId"],
        "last_update": format_timestamp(update_time),
        "last_read": format_timestamp(read_time),
        "has_unread": update_time > read_time if update_time and read_time else False,
        "cover": target.get("cover", ""),
        "intro": detail.get("intro", ""),
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
