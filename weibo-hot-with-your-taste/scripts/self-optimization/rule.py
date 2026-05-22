"""
规则优化脚本：发现 topic_category.json 中未归类的分类，LLM 预判归属，用户确认后更新 rule.yaml
"""
import sys
import json
import re
import argparse
from pathlib import Path

import yaml
import openai

sys.path.insert(0, str(Path(__file__).parent.parent))
from common import (
    DATA_DIR, CONFIG_DIR, CATEGORY_STORE_PATH, RULE_CONFIG_PATH, BASE_CONFIG_PATH,
    setup_logging, load_base_config, load_rule_config, resolve_llm_creds,
)

logger = setup_logging("rule-optimizer")
CONFIG = load_base_config()

CHOICE_EXCLUDE = "exclude"
CHOICE_STAR = "star"
CHOICE_SKIP = "skip"

LABEL_MAP = {
    CHOICE_EXCLUDE: "排除",
    CHOICE_STAR: "重要",
    CHOICE_SKIP: "跳过",
}


def find_unclassified_categories(keyword_store: dict, rule_config: dict) -> list:
    all_cats = set(keyword_store.get("categories", []))
    exclude = set(rule_config.get("category_exclude", []))
    star = set(rule_config.get("keyword_recall", []))
    classified = exclude | star
    return sorted(all_cats - classified)


def llm_classify_categories(categories: list, rule_config: dict, llm_model="", base_url="", api_key="") -> dict:
    if not api_key or not llm_model or not base_url:
        logger.warning("LLM 配置不完整，所有分类默认标记为 skip")
        return {cat: CHOICE_SKIP for cat in categories}

    exclude = rule_config.get("category_exclude", [])
    star = rule_config.get("keyword_recall", [])

    cat_list = "\n".join(f"{i+1}. {cat}" for i, cat in enumerate(categories))

    prompt = f"""你是一个微博热搜分类专家。请判断以下微博热搜分类应归属哪一类。

=== 已有规则参考 ===

排除分类（娱乐/生活类，不值得关注）：
{', '.join(exclude)}

关键词反写（命中后救回的重要新闻）：
{', '.join(star)}

=== 待分类列表 ===
{cat_list}

=== 归类标准 ===

排除(exclude)：纯娱乐/生活类，如影视、综艺、体育、美食、旅游等
重要(star)：政治/军事/重大科技/宏观经济等核心关注领域
跳过(skip)：无法确定或需要人工判断

=== 输出格式 ===
每行格式："序号:归属"，严格按序号输出，不要输出分类名称：
1:exclude
2:star
3:skip

必须包含全部 {len(categories)} 条分类的判断。"""

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
            logger.warning("LLM 返回为空")
            return {cat: CHOICE_SKIP for cat in categories}

        result = {}
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d+):(exclude|star|skip)", line)
            if m:
                idx = int(m.group(1))
                choice = m.group(2)
                if 1 <= idx <= len(categories):
                    result[categories[idx - 1]] = choice

        for cat in categories:
            if cat not in result:
                result[cat] = CHOICE_SKIP
                logger.warning(f"LLM 未返回 {cat} 的判断，默认 skip")

        return result

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        return {cat: CHOICE_SKIP for cat in categories}


def main():
    parser = argparse.ArgumentParser(description="规则优化")
    parser.add_argument("--llm-model", default="", help="agent 模式：LLM 模型名")
    parser.add_argument("--llm-base-url", default="", help="agent 模式：LLM API 地址")
    parser.add_argument("--llm-api-key", default="", help="agent 模式：LLM API 密钥")
    args = parser.parse_args()

    rule_config = load_rule_config()

    if not CATEGORY_STORE_PATH.exists():
        result = {"ready": False, "message": "topic_category.json 不存在，请先运行 push.py"}
        print(json.dumps(result, ensure_ascii=False))
        return

    with open(CATEGORY_STORE_PATH, encoding="utf-8") as f:
        keyword_store = json.load(f)

    unclassified = find_unclassified_categories(keyword_store, rule_config)

    if not unclassified:
        result = {"ready": False, "message": "所有分类已归类"}
        print(json.dumps(result, ensure_ascii=False))
        return

    logger.info(f"发现 {len(unclassified)} 个未归类分类: {unclassified}")

    llm_model, llm_base_url, llm_api_key = resolve_llm_creds(
        CONFIG, args.llm_model, args.llm_base_url, args.llm_api_key
    )
    recommendations = llm_classify_categories(unclassified, rule_config, llm_model, llm_base_url, llm_api_key)
    for cat, choice in recommendations.items():
        label = LABEL_MAP.get(choice, choice)
        logger.info(f"  {cat} → {label}")

    result = {
        "ready": True,
        "unclassified": unclassified,
        "recommendations": recommendations,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
