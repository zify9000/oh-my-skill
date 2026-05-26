"""反馈记录脚本：将用户反馈写入 tasted_topics.jsonl"""
import json
import fcntl
import argparse
from datetime import datetime

from common import DATA_DIR, setup_logging

TASTED_TOPICS_PATH = DATA_DIR / "tasted_topics.jsonl"

logger = setup_logging("feedback")


def main():
    parser = argparse.ArgumentParser(description="记录用户对话题的反馈")
    parser.add_argument("--word", required=True, help="话题名称")
    parser.add_argument("--liked", required=True, choices=["true", "false"], help="是否感兴趣")
    parser.add_argument("--category", default="", help="话题分类")
    parser.add_argument("--ts", default="", help="推送时间戳")
    parser.add_argument("--comment", default="", help="用户原话")
    args = parser.parse_args()

    record = {
        "ts": args.ts or datetime.now().isoformat(),
        "word": args.word,
        "liked": args.liked == "true",
        "category": args.category,
        "comment": args.comment,
        "recorded_at": datetime.now().isoformat(),
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd = open(TASTED_TOPICS_PATH, "a", encoding="utf-8")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        fd.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.info(f"反馈已写入: {args.word} → {'👍' if args.liked == 'true' else '👎'}")
    finally:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        fd.close()

    print(json.dumps({"status": "ok", "word": args.word, "liked": args.liked == "true"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
