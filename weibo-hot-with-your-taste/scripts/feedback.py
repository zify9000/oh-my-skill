"""反馈记录脚本：将用户反馈写入 tasted_topics.jsonl"""
import sys
import json
import os
import time
import fcntl
import argparse
import logging
from datetime import datetime
from pathlib import Path

os.environ["TZ"] = "Asia/Shanghai"
time.tzset()

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
LOG_DIR = SCRIPT_DIR / "log"
TASTED_TOPICS_PATH = DATA_DIR / "tasted_topics.jsonl"


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_level = os.environ.get("WEIBO_HOT_NEWS_LOG_LEVEL", "INFO").upper()
    log_file = LOG_DIR / f"feedback_{datetime.now().strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    return logging.getLogger("feedback")


logger = setup_logging()


def main():
    parser = argparse.ArgumentParser(description="记录用户对话题的反馈")
    parser.add_argument("--word", required=True, help="话题名称")
    parser.add_argument("--liked", required=True, choices=["true", "false"], help="是否感兴趣")
    parser.add_argument("--category", default="", help="话题分类")
    parser.add_argument("--ts", default="", help="推送时间戳")
    args = parser.parse_args()

    record = {
        "ts": args.ts or datetime.now().isoformat(),
        "word": args.word,
        "liked": args.liked == "true",
        "category": args.category,
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
