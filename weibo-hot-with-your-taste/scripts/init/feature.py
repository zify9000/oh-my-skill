"""偏好初始化脚本：Step 1 关键词→语义匹配分类，Step 2 用户选择→生成配置"""
import sys
import json
import re
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
import openai

from common import (
    SCRIPT_DIR, DATA_DIR, CONFIG_DIR,
    CATEGORY_STORE_PATH, RULE_CONFIG_PATH, PROMPT_PATH, INITIALIZED_PATH,
    setup_logging, load_base_config, load_llm_env, load_prompt, resolve_llm_creds,
)

logger = setup_logging("init")
CONFIG = load_base_config()


# ── Step 1: 关键词 → 语义匹配分类 ──

def load_categories() -> list:
    """从 topic_category.json 加载全部分类"""
    if not CATEGORY_STORE_PATH.exists():
        logger.error("topic_category.json 不存在，请先运行 fetch.py 抓取数据")
        sys.exit(1)
    with open(CATEGORY_STORE_PATH, encoding="utf-8") as f:
        store = json.load(f)
    return store.get("categories", [])


def call_llm_match_keywords(keywords: list, categories: list, llm_model="", base_url="", api_key="") -> dict:
    """LLM 根据关键词从分类列表中选出 liked/disliked 各10个"""
    if not api_key or not llm_model or not base_url:
        logger.error("LLM 配置不完整")
        sys.exit(1)

    kw_str = "、".join(keywords)
    cat_list = "\n".join(f"{i+1}. {cat}" for i, cat in enumerate(categories))

    prompt_template = load_prompt("init_keywords_prompt")
    prompt = prompt_template.format(keywords=kw_str, category_list=cat_list)

    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    try:
        resp = client.chat.completions.create(
            model=llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4096,
            timeout=60,
        )
        content = resp.choices[0].message.content
        if not content:
            logger.error("LLM 返回为空")
            sys.exit(1)

        liked = []
        disliked = []
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d+):(liked|disliked):(.+)$", line)
            if m:
                cat_name = m.group(3).strip()
                if cat_name in categories:
                    if m.group(2) == "liked":
                        liked.append(cat_name)
                    else:
                        disliked.append(cat_name)

        if not liked and not disliked:
            logger.error("LLM 输出未匹配到任何分类")
            sys.exit(1)

        logger.info(f"语义匹配结果: liked={len(liked)}, disliked={len(disliked)}")
        return {"liked": liked, "disliked": disliked}

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        sys.exit(1)


def step_keywords(args):
    """Step 1: 关键词 → 语义匹配"""
    keywords = args.kw
    if len(keywords) != 3:
        logger.error("请提供恰好3个关键词")
        sys.exit(1)

    categories = load_categories()
    logger.info(f"加载 {len(categories)} 个分类，关键词: {keywords}")

    llm_model, llm_base_url, llm_api_key = resolve_llm_creds(
        CONFIG, args.llm_model, args.llm_base_url, args.llm_api_key
    )
    result = call_llm_match_keywords(keywords, categories, llm_model, llm_base_url, llm_api_key)

    output = {
        "keywords": keywords,
        "liked": result["liked"],
        "disliked": result["disliked"],
    }
    print(json.dumps(output, ensure_ascii=False))


# ── Step 2: 用户选择 → 生成配置 ──

def call_llm_generate_criteria(keywords, liked, disliked, recall, llm_model="", base_url="", api_key="") -> tuple:
    """LLM 根据用户偏好生成 yes/no 判断标准，返回 (yes_criteria, no_criteria)"""
    if not api_key or not llm_model or not base_url:
        logger.error("LLM 配置不完整")
        sys.exit(1)

    kw_str = "、".join(keywords)
    liked_str = "、".join(liked)
    disliked_str = "、".join(disliked)
    recall_str = "、".join(recall) if recall else "无"

    prompt_template = load_prompt("init_criteria_prompt")
    prompt = prompt_template.format(
        keywords=kw_str,
        liked_categories=liked_str,
        disliked_categories=disliked_str,
        recall_keywords=recall_str,
    )

    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    try:
        resp = client.chat.completions.create(
            model=llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=4096,
            timeout=120,
        )
        content = resp.choices[0].message.content
        if not content:
            logger.error("LLM 返回为空")
            sys.exit(1)

        yes_criteria = ""
        no_criteria = ""

        yes_match = re.search(r"===yes===\s*\n(.*?)(?=\n===no===)", content, re.DOTALL)
        if yes_match:
            yes_criteria = yes_match.group(1).strip()

        no_match = re.search(r"===no===\s*\n(.*)", content, re.DOTALL)
        if no_match:
            no_criteria = no_match.group(1).strip()

        if not yes_criteria or not no_criteria:
            logger.error("无法解析 LLM 输出中的判断标准")
            sys.exit(1)

        logger.info(f"判断标准生成完成: yes={len(yes_criteria)}字, no={len(no_criteria)}字")
        return yes_criteria, no_criteria

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        sys.exit(1)


def generate_rule_yaml(liked, disliked, keywords, recall):
    """生成 rule.yaml 内容"""
    # category_exclude: 用户不喜欢的分类
    category_exclude = list(disliked)

    # keyword_recall: 用户喜欢的分类 + 关键词 + 召回关键词（去重保序）
    seen = set()
    keyword_recall = []
    for item in liked + keywords + recall:
        if item not in seen:
            seen.add(item)
            keyword_recall.append(item)

    rule_config = {
        "category_exclude": category_exclude,
        "keyword_recall": keyword_recall,
    }

    # 备份旧文件
    if RULE_CONFIG_PATH.exists():
        backup_path = RULE_CONFIG_PATH.with_suffix(".yaml.bak")
        RULE_CONFIG_PATH.rename(backup_path)
        logger.info(f"旧 rule.yaml 已备份为 {backup_path.name}")

    with open(RULE_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(rule_config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    logger.info(f"rule.yaml 已生成: exclude={len(category_exclude)}, recall={len(keyword_recall)}")
    return rule_config


def write_initialized(keywords, liked, disliked, recall, yes_criteria, no_criteria):
    """写入 .initialized 标记文件"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "initialized_at": datetime.now().isoformat(),
        "keywords": keywords,
        "liked_categories": liked,
        "disliked_categories": disliked,
        "recall_keywords": recall,
        "yes_criteria": yes_criteria,
        "no_criteria": no_criteria,
    }
    with open(INITIALIZED_PATH, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    logger.info(".initialized 标记文件已写入")


def step_choices(args):
    """Step 2: 用户选择 → 生成配置"""
    keywords = args.kw
    liked = [x.strip() for x in args.liked.split(",") if x.strip()]
    disliked = [x.strip() for x in args.disliked.split(",") if x.strip()]
    recall = [x.strip() for x in args.recall.split(",") if x.strip()] if args.recall else []

    if len(keywords) != 3:
        logger.error("请提供恰好3个关键词")
        sys.exit(1)
    if len(liked) < 5:
        logger.error(f"感兴趣的分类至少选5个，当前{len(liked)}个")
        sys.exit(1)
    if len(disliked) < 5:
        logger.error(f"不感兴趣的分类至少选5个，当前{len(disliked)}个")
        sys.exit(1)

    llm_model, llm_base_url, llm_api_key = resolve_llm_creds(
        CONFIG, args.llm_model, args.llm_base_url, args.llm_api_key
    )
    yes_criteria, no_criteria = call_llm_generate_criteria(
        keywords, liked, disliked, recall, llm_model, llm_base_url, llm_api_key
    )

    rule_config = generate_rule_yaml(liked, disliked, keywords, recall)
    write_initialized(keywords, liked, disliked, recall, yes_criteria, no_criteria)

    output = {
        "status": "ok",
        "rule": rule_config,
        "prompt_updated": True,
    }
    print(json.dumps(output, ensure_ascii=False))


def main():
    load_llm_env()
    parser = argparse.ArgumentParser(description="偏好初始化")
    subparsers = parser.add_subparsers(dest="step", required=True)

    # Step 1: keywords
    p1 = subparsers.add_parser("keywords", help="关键词→语义匹配分类")
    p1.add_argument("--kw", nargs=3, required=True, help="3个关注关键词")
    p1.add_argument("--llm-model", default="", help="LLM 模型名")
    p1.add_argument("--llm-base-url", default="", help="LLM API 地址")
    p1.add_argument("--llm-api-key", default="", help="LLM API 密钥")

    # Step 2: choices
    p2 = subparsers.add_parser("choices", help="用户选择→生成配置")
    p2.add_argument("--kw", nargs=3, required=True, help="3个关注关键词")
    p2.add_argument("--liked", required=True, help="逗号分隔的感兴趣分类")
    p2.add_argument("--disliked", required=True, help="逗号分隔的不感兴趣分类")
    p2.add_argument("--recall", default="", help="逗号分隔的召回关键词")
    p2.add_argument("--llm-model", default="", help="LLM 模型名")
    p2.add_argument("--llm-base-url", default="", help="LLM API 地址")
    p2.add_argument("--llm-api-key", default="", help="LLM API 密钥")

    args = parser.parse_args()

    if args.step == "keywords":
        step_keywords(args)
    elif args.step == "choices":
        step_choices(args)


if __name__ == "__main__":
    main()
