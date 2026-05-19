#!/opt/hermes/.venv/bin/python3
"""反馈记录脚本：将用户反馈写入 tasted_topics.jsonl"""
import sys
import json
import os
import time
import fcntl
import argparse
from datetime import datetime
from pathlib import Path

os.environ["TZ"] = "Asia/Shanghai"
time.tzset()

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
TASTED_TOPICS_PATH = DATA_DIR / "tasted_topics.jsonl"


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
        "recorded_at": datetime.now().isoformat(),
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(TASTED_TOPICS_PATH, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    print(json.dumps({"status": "ok", "word": args.word, "liked": args.liked == "true"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
