#!/usr/bin/env python3
"""
云端每日发布脚本 — GitHub Actions 运行，不依赖本地机器

触发：GitHub Actions cron 工作日 UTC 1:00 (北京时间 9:00)

环境变量（GitHub Secrets）：
  SILICONFLOW_API_KEY — 硅基流动 API 密钥（国内永久免费）
  WEIXIN_APPID         — 广大大公众号 AppID
  WEIXIN_SECRET        — 广大大公众号 AppSecret

封面图：PIL 本地生成（零外部依赖）

用法：
  python3 scripts/cloud_daily_publish.py [--date 2026-07-03]
"""

import os
import sys
import json
import logging
import argparse
import subprocess
import tempfile
import re
import time
import struct
import zlib
from datetime import datetime
from pathlib import Path
from io import BytesIO

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============================================================
# 配置
# ============================================================
WEIXIN_APPID = os.environ.get("WEIXIN_APPID", "wx94eb6ba27c82a203")
WEIXIN_SECRET = os.environ.get("WEIXIN_SECRET", "")
AUTHOR = "zylon"

# 硅基流动（国内，永久免费，兼容 OpenAI SDK）
SILICONFLOW_BASE = "https://api.siliconflow.cn/v1"
SILICONFLOW_MODEL = "Qwen/Qwen2.5-7B-Instruct"  # 永久免费，中文能力强

# 赛道
TRACKS = {
    "game": {
        "name": "游戏",
        "search_hint": "2026出海手游 SLG 休闲 买量 下载量 收入 最新",
        "data_sources": "广大大、gamelook、白鲸出海、10100.com",
    },
    "tool": {
        "name": "工具",
        "search_hint": "2026出海工具App 清理 壁纸 AI助手 买量 下载量 最新",
        "data_sources": "广大大、白鲸出海、扬帆出海、10100.com",
    },
    "drama": {
        "name": "短剧",
        "search_hint": "2026出海短剧 AI短剧 ReelShort 买量 排行 最新",
        "data_sources": "广大大、扬帆出海、10100.com、App Store",
    },
}

# 竞品黑名单（出现=推送事故）
BANNED_COMPETITORS = [
    "热云", "XMP", "Insightrackr", "insightrackr", "Mintegral", "mintegral",
    "有米云", "AppGrowing", "Appark",
    "DataEye", "dataeye", "ADX",
    "SensorTower", "sensortower",
    "橙果数杭", "独立出海联合体", "游戏茶馆", "AntGlobal", "游戏陀螺",
]

SAFE_SOURCES = [
    "广大大", "SocialPeta", "10100.com", "大数跨境",
    "虎嗅", "36氪", "扬帆出海", "白鲸出海", "gamelook",
    "App Store", "Google Play", "上市公司财报",
]


def check_no_competitor(text):
    for banned in BANNED_COMPETITORS:
        if banned.lower() in text.lower():
            return False, banned
    return True, None


# ============================================================
# 微信 API
# ============================================================
def wechat_get_token():
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={WEIXIN_APPID}&secret={WEIXIN_SECRET}"
    r = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=30)
    data = json.loads(r.stdout)
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"Token 失败: {data.get('errmsg')}")
    log.info(f"✅ Token OK")
    return data["access_token"]


def wechat_upload_cover(token, image_path):
    if not image_path or not os.path.exists(image_path):
        return None
    url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={token}&type=image"
    r = subprocess.run(["curl", "-s", "-X", "POST", url, "-F", f"media=@{image_path}"],
                       capture_output=True, text=True, timeout=60)
    data = json.loads(r.stdout)
    mid = data.get("media_id", "")
    if not mid:
        log.warning(f"封面上传失败: {data}")
        return None
    log.info(f"✅ 封面: {mid}")
    return mid


def wechat_create_draft(token, title, digest, html, thumb_id=None):
    article = {
        "title": title, "author": AUTHOR, "digest": digest,
        "content": html, "need_open_comment": 1, "only_fans_can_comment": 0,
    }
    if thumb_id:
        article["thumb_media_id"] = thumb_id

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"articles": [article]}, f, ensure_ascii=False)
        jp = f.name

    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
    r = subprocess.run(["curl", "-s", "-X", "POST", url, "-H", "Content-Type: application/json", "-d", f"@{jp}"],
                       capture_output=True, text=True, timeout=30)
    os.unlink(jp)
    data = json.loads(r.stdout)
    mid = data.get("media_id", "")
    if not mid:
        raise RuntimeError(f"草稿失败 [{data.get('errcode')}]: {data.get('errmsg')}")
    return mid


# ============================================================
# 硅基流动 文本生成（国内，永久免费）
# ============================================================
def get_siliconflow_client():
    from openai import OpenAI
    key = os.environ.get("SILICONFLOW_API_KEY")
    if not key:
        raise RuntimeError("SILICONFLOW_API_KEY 未设置")
    return OpenAI(api_key=key, base_url=SILICONFLOW_BASE)


SYSTEM_PROMPT = f"""你是广大大(SocialPeta)公众号写手~广大大是出海广告买量素材分析工具~

写作铁律：
1. 句号用~代替（不用。！）
2. 禁止写作框架（不要开头场景代入/结尾总结/首先其次最后）
3. 禁止AI词汇：此外/值得注意的是/毋庸置疑/由此可见/总的来说/综上所述
4. 禁止虚假强调：破折号——/不仅而且/硬凑三点
5. 禁止套话开头："在当今""随着的发展""近年来"
6. 说话像微信聊天：可以废话/跑题/吐槽/口语/不确定
7. 正文禁止emoji
8. 不低于800字

数据规则：
- 数据真实可查，不确定就写范围或趋势
- 数据来源只能用：{', '.join(SAFE_SOURCES)}

竞品黑名单（绝对禁止）：
{', '.join(BANNED_COMPETITORS)}

每篇结尾引导回复关键词获取资料~"""


def generate_article(track_key):
    track = TRACKS[track_key]

    track_styles = {
        "game": "暗色数据仪表盘风格：大数字卡片、表格对比、排名列表、像分析师解读报表",
        "tool": "效率清单风格：分类卡片、简洁列表、对比变现模式、像懂行的朋友分享观察",
        "drama": "杂志叙事风格：时间线、产品对比、引用块、像资深编辑讲行业故事",
    }

    prompt = f"""写一篇出海{track['name']}买量的公众号文章~

风格：{track_styles.get(track_key, '')}
搜索方向：{track['search_hint']}
数据来源：{track['data_sources']}

要求：
1. 重点拆解1-2个具体产品的买量策略
2. 用广大大视角量化分析
3. 结尾引导回复关键词
4. 句号用~，禁止emoji，禁止竞品名

直接输出文章纯文本~"""

    log.info(f"🤖 生成 {track['name']}...")

    client = get_siliconflow_client()
    resp = client.chat.completions.create(
        model=SILICONFLOW_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.85,
        max_tokens=4000,
    )

    text = resp.choices[0].message.content

    # 检查竞品
    clean, banned = check_no_competitor(text)
    if not clean:
        log.error(f"❌ 发现竞品: {banned}，重新生成...")
        resp2 = client.chat.completions.create(
            model=SILICONFLOW_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"上面出现了竞品名「{banned}」，禁止！请重新生成整篇，移除所有竞品引用~"},
            ],
            temperature=0.85,
            max_tokens=4000,
        )
        text = resp2.choices[0].message.content

    log.info(f"✅ {track['name']} {len(text)}字")
    return text


# ============================================================
# 封面图生成（PIL 本地 — 零外部依赖）
# ============================================================
def generate_covers():
    """一次性生成三张封面图，返回 {track_key: BytesIO}"""
    from PIL import Image, ImageDraw

    configs = {
        "game": {
            "colors": [(26, 26, 46), (22, 33, 62)],
            "accent": (220, 38, 38),
            "accent2": (251, 191, 36),
            "shapes": "grid",
        },
        "tool": {
            "colors": [(240, 253, 244), (255, 255, 255)],
            "accent": (13, 148, 136),
            "accent2": (20, 184, 166),
            "shapes": "circles",
        },
        "drama": {
            "colors": [(250, 249, 247), (254, 243, 199)],
            "accent": (124, 58, 237),
            "accent2": (245, 158, 11),
            "shapes": "waves",
        },
    }

    covers = {}

    for track_key, cfg in configs.items():
        w, h = 900, 383
        img = Image.new("RGB", (w, h), cfg["colors"][0])

        # 渐变背景（c1=顶部颜色, c2=底部颜色）
        c1 = cfg["colors"][0]
        c2 = cfg["colors"][1]
        for y in range(h):
            ratio = y / h
            r = int(c1[0] + (c2[0] - c1[0]) * ratio)
            g = int(c1[1] + (c2[1] - c1[1]) * ratio)
            b = int(c1[2] + (c2[2] - c1[2]) * ratio)
            for x in range(w):
                img.putpixel((x, y), (r, g, b))

        draw = ImageDraw.Draw(img)

        # 装饰图形
        import random
        random.seed(hash(track_key) % 10000)

        if cfg["shapes"] == "grid":
            # 几何线条
            for i in range(3):
                x1 = random.randint(50, 250)
                x2 = random.randint(650, 850)
                y = random.randint(80, 300)
                draw.line([(x1, y), (x2, y)], fill=cfg["accent2"], width=1)
            for i in range(4):
                rx = random.randint(100, 800)
                ry = random.randint(60, 320)
                draw.rectangle([rx, ry, rx + random.randint(30, 80), ry + random.randint(10, 20)],
                               outline=cfg["accent"], width=1)
        elif cfg["shapes"] == "circles":
            for i in range(6):
                cx = random.randint(80, 820)
                cy = random.randint(60, 320)
                r = random.randint(15, 45)
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=cfg["accent"], width=1)
        else:  # waves
            for i in range(5):
                y_base = 100 + i * 55
                points = []
                for x in range(0, w, 10):
                    y_off = int(18 * (1 if i % 2 == 0 else -1) * (1 if x % 40 < 20 else -1) * 0.6)
                    points.append((x, y_base + y_off))
                for j in range(len(points) - 1):
                    draw.line([points[j], points[j + 1]], fill=cfg["accent2"], width=1)

        # 另存
        buf = BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        covers[track_key] = buf.getvalue()

    log.info(f"🎨 三张封面图生成完成（PIL本地）")
    return covers


# ============================================================
# HTML 排版
# ============================================================
def markdown_to_html(md_text, track_key):
    styles = {
        "game": {"bg": "#1a1a2e", "text": "#e0e0e0", "accent": "#dc2626", "accent2": "#fbbf24",
                 "card_bg": "#16213e", "border": "#333"},
        "tool": {"bg": "#ffffff", "text": "#333", "accent": "#0d9488", "accent2": "#14b8a6",
                 "card_bg": "#f0fdf4", "border": "#e0e0e0"},
        "drama": {"bg": "#faf9f7", "text": "#333", "accent": "#7c3aed", "accent2": "#f59e0b",
                  "card_bg": "#fef3c7", "border": "#e0d5c8"},
    }
    s = styles.get(track_key, styles["tool"])

    parts = [f"""<section style="padding:12px 10px;font-size:15px;line-height:1.88;color:{s['text']};word-break:break-all;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;background:{s['bg']};">"""]
    parts.append(f"""<section style="text-align:center;margin-bottom:24px;padding:14px 0;border-bottom:1px solid {s['border']};"><p style="color:#999;font-size:12px;letter-spacing:1px;margin:0;">点击上方蓝字关注，每周解锁出海买量新玩法</p></section>""")

    for line in md_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("### "):
            txt = line[4:]
            parts.append(f"""<section style="margin:22px 0 12px 0;padding:10px 0;border-left:4px solid {s['accent']};padding-left:14px;"><p style="margin:0;font-size:17px;font-weight:bold;color:{s['accent']};">{txt}</p></section>""")
        elif line.startswith("## "):
            txt = line[3:]
            parts.append(f"""<section style="margin:28px 0 14px 0;text-align:center;"><p style="margin:0;font-size:20px;font-weight:bold;color:{s['accent']};border-bottom:2px solid {s['accent']};display:inline-block;padding-bottom:6px;">{txt}</p></section>""")
        elif any(k in line for k in ["万", "亿", "$", "%"]):
            parts.append(f"""<p style="margin:0 0 18px 0;font-size:15px;color:{s['text']};line-height:1.9;"><strong>{line}</strong></p>""")
        elif line.startswith("- "):
            txt = line[2:]
            parts.append(f"""<p style="margin:0 0 12px 0;padding-left:14px;font-size:14px;color:{s['text']};line-height:1.8;border-left:3px solid {s['accent']};">{txt}</p>""")
        else:
            parts.append(f"""<p style="margin:0 0 18px 0;font-size:15px;color:{s['text']};line-height:1.9;">{line}</p>""")

    parts.append(f"""<section style="margin:30px 0;text-align:center;background:{s['card_bg']};border-radius:12px;padding:20px 18px;border:1px dashed {s['accent']};"><p style="margin:0 0 8px 0;font-size:14px;font-weight:bold;color:{s['accent']};">想看广大大完整买量数据？</p><p style="margin:0;font-size:14px;color:{s['text']};">回复关键词获取更多数据~</p></section>""")
    parts.append(f"""<section style="margin:20px 0 10px 0;text-align:center;padding-top:14px;border-top:1px solid {s['border']};"><p style="margin:0;font-size:11px;color:#999;">广大大 | 出海买量数据专家</p></section>""")
    parts.append("</section>")

    return "\n".join(parts)


def extract_title_digest(text, track_name, date_str):
    lines = text.strip().split("\n")
    first = next((l.strip() for l in lines if l.strip() and not l.startswith("#")), "")
    title = (first[:40].rstrip("~") + "...") if len(first) > 40 else first[:40].rstrip("~") or f"{track_name}出海买量 | {date_str}"
    preview = text[:120].replace("\n", " ").replace("~", "").strip()
    digest = preview[:90].rstrip() + "~"
    return title, digest


# ============================================================
# 主流程
# ============================================================
def publish_track(token, track_key, date_str, cover_data, output_dir):
    track_name = TRACKS[track_key]["name"]
    log.info(f"\n{'='*40}\n📝 {track_name}\n{'='*40}")

    # 生成文章
    article = generate_article(track_key)
    title, digest = extract_title_digest(article, track_name, date_str)
    log.info(f"📌 {title}")

    # 保存封面
    cover_path = os.path.join(output_dir, f"cover_{track_key}.png")
    with open(cover_path, "wb") as f:
        f.write(cover_data)

    # HTML
    html = markdown_to_html(article, track_key)

    clean, banned = check_no_competitor(html)
    if not clean:
        log.error(f"❌ HTML中还有竞品: {banned}，跳过")
        return None, None

    # 保存文件
    html_path = os.path.join(output_dir, f"draft_{track_key}.html")
    md_path = os.path.join(output_dir, f"draft_{track_key}.md")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(article)

    # 推送
    thumb_id = wechat_upload_cover(token, cover_path)
    html_clean = re.sub(r">\s+<", "><", html.replace("\n", " ").strip())
    draft_id = wechat_create_draft(token, title, digest, html_clean, thumb_id)
    log.info(f"✅ 草稿ID: {draft_id}")

    return draft_id, {"title": title, "digest": digest}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--tracks", nargs="+", choices=["game", "tool", "drama"], default=["game", "tool", "drama"])
    parser.add_argument("--output", default="/tmp/gzh_output")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    date_str = args.date
    output_dir = args.output
    os.makedirs(output_dir, exist_ok=True)

    log.info(f"🚀 广大大云端发布 | {date_str}")
    log.info(f"🤖 硅基流动(国内永久免费) | 🎨 PIL本地封面")

    cover_datas = generate_covers()

    token = None if args.dry_run else wechat_get_token()

    results = {}
    for tk in args.tracks:
        try:
            if tk in cover_datas:
                did, info = publish_track(token, tk, date_str, cover_datas[tk], output_dir)
                if did:
                    results[tk] = {"draft_id": did, **info}
            time.sleep(2)
        except Exception as e:
            log.error(f"❌ {TRACKS[tk]['name']} 失败: {e}")
            import traceback
            traceback.print_exc()

    log.info(f"\n{'='*50}")
    for tk, info in results.items():
        log.info(f"  {TRACKS[tk]['name']}: {info['title']}")
        log.info(f"    草稿ID: {info['draft_id']}")
    log.info(f"📌 审核发布: https://mp.weixin.qq.com → 草稿箱")

    print(json.dumps({k: {"draft_id": v["draft_id"], "title": v["title"]} for k, v in results.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
