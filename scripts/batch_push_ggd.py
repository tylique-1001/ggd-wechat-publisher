#!/usr/bin/env python3
"""
广大大批量推送脚本
一次性推送多篇文章到草稿箱，全部完成后发一条飞书汇总通知

用法:
  python3 scripts/batch_push_ggd.py <json文件>

JSON文件格式:
  [
    {
      "title": "标题",
      "digest": "摘要",
      "html_file": "outputs/game.html",
      "cover_image": "outputs/cover_game.png"
    },
    ...
  ]

示例:
  python3 scripts/batch_push_ggd.py batch_config.json
"""

import json
import sys
import os
import subprocess

# 加入上级目录以便 import push_ggd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from push_ggd import get_verified_token, read_and_clean_html, upload_cover, create_draft, GGD_NAME

FEISHU_TARGET_OPENID = "ou_e61d62d0f233b8c91fc56ea461f88f0c"


def send_feishu_summary(success_list, fail_list):
    """发送飞书汇总通知"""
    if success_list and not fail_list:
        titles = "\n".join([f"{i}. {a['title']}" for i, a in enumerate(success_list, 1)])
        text = f"✅ 广大大推送完成({len(success_list)}篇):\n{titles}\n👉 前往草稿箱审核: https://mp.weixin.qq.com"
    elif fail_list and not success_list:
        items = "\n".join([f"{i}. ❌ {a['title']}: {a['error']}" for i, a in enumerate(fail_list, 1)])
        text = f"⚠️ 全部失败({len(fail_list)}篇):\n{items}"
    else:
        ok_titles = "\n".join([f"{i}. {a['title']}" for i, a in enumerate(success_list, 1)])
        fail_items = "\n".join([f"{i}. ❌ {a['title']}: {a['error']}" for i, a in enumerate(fail_list, 1)])
        text = f"✅ 成功({len(success_list)}篇):\n{ok_titles}\n\n❌ 失败({len(fail_list)}篇):\n{fail_items}"

    try:
        result = subprocess.run(
            ["lark-cli", "im", "+messages-send", "--as", "bot",
             "--user-id", FEISHU_TARGET_OPENID, "--text", text],
            timeout=15, capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"[ERROR] 飞书通知失败 (exit {result.returncode}): {result.stderr.strip()}", file=sys.stderr)
        else:
            print("[OK] 飞书通知已发送", file=sys.stderr)
    except Exception as e:
        print(f"[ERROR] 飞书通知异常: {e}", file=sys.stderr)


def push_one(token, article):
    """推送单篇文章，返回 (success, title, error)"""
    title = article["title"]
    digest = article["digest"]
    html_path = article.get("html_file", "")
    cover_path = article.get("cover_image")

    try:
        html = read_and_clean_html(html_path)
        thumb_media_id = None
        if cover_path:
            thumb_media_id = upload_cover(token, cover_path)
        draft_media_id = create_draft(token, title, digest, html, thumb_media_id)
        print(f"[OK] ✅ {title}")
        return True, title, None
    except SystemExit as e:
        if e.code != 0:
            print(f"[FAIL] ❌ {title} (exit {e.code})")
            return False, title, f"exit code {e.code}"
        return True, title, None
    except Exception as e:
        print(f"[FAIL] ❌ {title}: {e}")
        return False, title, str(e)


def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/batch_push_ggd.py <config.json>", file=sys.stderr)
        sys.exit(1)

    config_path = sys.argv[1]
    with open(config_path, "r", encoding="utf-8") as f:
        articles = json.load(f)

    if not articles:
        print("[WARN] 配置文件为空，无文章可推送", file=sys.stderr)
        return

    print(f"[INFO] 目标账号: {GGD_NAME}")
    print(f"[INFO] 共 {len(articles)} 篇文章待推送\n")

    token = get_verified_token()
    success_list = []
    fail_list = []

    for i, article in enumerate(articles, 1):
        print(f"[{i}/{len(articles)}] 推送: {article['title']} ...")
        ok, title, error = push_one(token, article)
        if ok:
            success_list.append(article)
        else:
            fail_list.append({"title": title, "error": error})
        print()

    # 汇总
    print("=" * 50)
    print(f"推送完成: ✅ {len(success_list)} 成功 / ❌ {len(fail_list)} 失败")
    print("=" * 50)

    # 飞书汇总通知
    send_feishu_summary(success_list, fail_list)
    print("\n[INFO] 飞书汇总通知已发送")


if __name__ == "__main__":
    main()
