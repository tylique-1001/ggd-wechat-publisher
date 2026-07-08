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

写作铁律（违反任何一条=直接废稿）：
1. 句号用~代替（不用。！）
2. 禁止任何写作框架（不要开头场景代入/不要结尾总结/不要用"首先/其次/最后/接下来/随着/近年来/在当今"）
3. 禁止AI词汇：此外/值得注意的是/毋庸置疑/由此可见/总的来说/综上所述/不难发现/不难看出/可以说
4. 禁止虚假强调：破折号——/不仅而且/硬凑三点/层层递进/深刻洞察
5. 禁止套话开头：绝对禁止"随着短剧.../随着游戏.../随着出海.../近年来.../在当今..."等一切背景铺垫式开头
6. 说话像微信聊天：可以废话/跑题/吐槽/口语/不确定/没想好/断断续续的句子
7. 正文禁止emoji
8. 不低于800字
9. 禁止任何结构化格式：不要列表、不要编号、不要卡片、不要表格、不要时间线、不要引用块、不要分点论述
10. 只用自然段落写作，像平时微信聊天一样一段一段说
11. 数据穿插在段落里，不要单独列出来当标题或卡片
12. 开头直接说事，不要铺垫背景

数据规则：
- 数据真实可查，不确定就写范围或趋势
- 数据来源只能用：{', '.join(SAFE_SOURCES)}

竞品黑名单（绝对禁止）：
{', '.join(BANNED_COMPETITORS)}

每篇结尾引导回复关键词获取资料~

输出格式：只输出纯文本段落，不要加任何格式标记（如 **加粗**、#### 标题等）"""


def clean_ai_content(text):
    """清洗AI生成的内容，去除对话标记、AI套话、乱码行"""
    if not text:
        return ""
    
    # 1. 去除对话标记和角色标签行（user、assistant、system等）
    dialogue_markers = ["user", "assistant", "system", "human", "请拆解", "请重新生成", "请修改", "/Dk", "Dk", "**1", "—**", "A A", "A AA"]
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # 跳过纯对话标记行
        if stripped.lower() in ("user", "assistant", "system", "human"):
            continue
        if stripped == "1" or stripped == "2" or stripped == "3":
            continue
        # 跳过看起来是乱码的短行（只有A、*、数字等）
        if re.match(r'^[A-Za-z\*\s\-/]+$', stripped) and len(stripped) <= 10:
            continue
        # 跳过包含对话指令的行
        if any(marker in stripped for marker in dialogue_markers):
            # 但如果整行包含正常文字，只去除标记部分
            if len(stripped) > 30:
                for m in dialogue_markers:
                    stripped = stripped.replace(m, "").strip()
                if stripped:
                    cleaned_lines.append(stripped)
            continue
        cleaned_lines.append(line)
    
    text = "\n".join(cleaned_lines)
    
    # 2. 去除AI套话开头
    ai_openers = [
        r"随着.*?的.*?，",
        r"近年来.*?，",
        r"在当今.*?，",
        r"首先.*?，",
        r"接下来.*?，",
        r"首先[，,]?",
        r"其次[，,]?",
        r"最后[，,]?",
        r"综上所述[，,]?",
        r"总的来说[，,]?",
        r"值得注意的是[，,]?",
        r"不难发现[，,]?",
        r"可以看出[，,]?",
        r"众所周知[，,]?",
    ]
    for pattern in ai_openers:
        text = re.sub(pattern, "", text, count=1, flags=re.IGNORECASE)
    
    # 3. 去除多余空行和开头空格
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    
    # 4. 去除末尾不完整的句子（如"据数据显示~2"后面没有内容）
    text = re.sub(r'据数据显示~\d+$', '', text)
    text = re.sub(r'据数据显示~\d+\s*$', '', text)
    
    # 5. 去除孤立的编号（如 "1." 单独一行）
    text = re.sub(r'\n\d+[\.\、]\s*\n', '\n', text)
    
    # 6. 去除 "**" 加粗标记（要求AI纯文本输出，但万一有残留）
    text = text.replace("**", "")
    
    # 7. 去除多余的 "~" 符号（连续多个）
    text = re.sub(r'~{2,}', '~', text)
    
    # 8. 去除 "user" 和 "assistant" 标签
    text = re.sub(r'\buser\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bassistant\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bhuman\b', '', text, flags=re.IGNORECASE)
    
    # 9. 去除 "请拆解..." 等指令残留
    text = re.sub(r'请拆解.*?[。~]', '~', text)
    text = re.sub(r'请分析.*?[。~]', '~', text)
    text = re.sub(r'请重新生成.*?[。~]', '~', text)
    
    # 10. 最后清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    
    return text


def check_content_quality(text):
    """检查内容质量，返回 (is_ok, reason)"""
    if not text or len(text) < 300:
        return False, f"内容太短({len(text)}字)，需要重新生成"
    
    # 检查是否包含对话标记
    dialogue_words = ["user", "assistant", "system", "请拆解", "请重新生成", "请分析"]
    for word in dialogue_words:
        if word.lower() in text.lower():
            return False, f"包含对话标记'{word}'，需要重新生成"
    
    # 检查乱码率（非中文字符/数字/英文/标点占比过高）
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    total_chars = len(text)
    if total_chars > 0 and chinese_chars / total_chars < 0.3:
        return False, "中文内容比例过低，疑似乱码，需要重新生成"
    
    # 检查是否有大量重复短行（如 "A A"）
    weird_lines = [l for l in text.split("\n") if re.match(r'^[A-Z\*\s\-/]{1,10}$', l.strip())]
    if len(weird_lines) > 3:
        return False, "包含过多乱码行，需要重新生成"
    
    return True, ""


def generate_article(track_key, retry=0, max_retry=2):
    track = TRACKS[track_key]

    # 修正：去掉所有结构化/卡片化词汇，改为纯段落描述
    track_styles = {
        "game": "游戏买量分析：用数据聊产品，像和朋友讨论某款游戏最近怎么买量，语气随意，想到哪说到哪",
        "tool": "工具出海观察：像用过很多工具App的人在吐槽或推荐，想到什么说什么，不追求结构完整",
        "drama": "短剧行业闲聊：像追过不少短剧的人在八卦最近哪些剧在砸钱，哪些在偷懒，语气随意",
    }

    prompt = f"""写一篇出海{track['name']}买量的公众号文章~

语气：{track_styles.get(track_key, '')}
搜索方向：{track['search_hint']}
数据来源：{track['data_sources']}

要求：
1. 重点拆解1-2个具体产品的买量策略
2. 用广大大视角量化分析（数据穿插在段落里，不要单独列成表格或列表）
3. 结尾引导回复关键词
4. 句号用~，禁止emoji，禁止竞品名
5. 只输出纯文本段落，不要加粗、不要标题、不要列表、不要编号

直接输出文章正文~"""

    log.info(f"🤖 生成 {track['name']}...")

    client = get_siliconflow_client()
    
    # 增加 temperature 和 max_tokens，确保内容完整
    resp = client.chat.completions.create(
        model=SILICONFLOW_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        max_tokens=6000,
    )

    text = resp.choices[0].message.content
    
    # 清洗AI内容
    text = clean_ai_content(text)
    
    # 质量检查
    ok, reason = check_content_quality(text)
    if not ok:
        log.error(f"❌ 内容质量不通过: {reason}，重试({retry+1}/{max_retry})...")
        if retry < max_retry:
            return generate_article(track_key, retry=retry+1, max_retry=max_retry)
        else:
            log.error(f"❌ 重试耗尽，使用备用内容")
            return f"{track['name']}出海买量观察~今天先聊到这里，数据更新中~"

    # 检查竞品
    clean_check, banned = check_no_competitor(text)
    if not clean_check:
        log.error(f"❌ 发现竞品: {banned}，重新生成...")
        if retry < max_retry:
            return generate_article(track_key, retry=retry+1, max_retry=max_retry)
        else:
            # 尝试移除竞品名
            text = text.replace(banned, "某平台")
            log.warning(f"⚠️ 手动替换竞品名: {banned} -> 某平台")

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
    """将 AI 生成的 Markdown 文本转换为微信兼容的 HTML
    
    排版铁律：不能让人看出来是AI写的。
    用最简单的 HTML：纯段落 + 少量加粗，
    禁止 table / 进度条 / 卡片 / 渐变 / flexbox / grid / 列表 / 编号
    """
    styles = {
        "game": {"bg": "#ffffff", "text": "#2d2d2d", "accent": "#c0392b"},
        "tool": {"bg": "#ffffff", "text": "#2d2d2d", "accent": "#0d9488"},
        "drama": {"bg": "#ffffff", "text": "#2d2d2d", "accent": "#d97706"},
    }
    s = styles.get(track_key, styles["tool"])

    parts = [f"""<section style="padding:12px 10px;font-size:15px;line-height:1.88;color:{s['text']};word-break:break-all;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;background:{s['bg']};">"""]
    parts.append(f"""<p style="text-align:center;margin-bottom:20px;color:#999;font-size:12px;">点击上方蓝字关注，每周解锁出海买量新玩法~</p>""")

    # AI套话过滤列表（行级别）
    ai_filler_lines = [
        "首先", "其次", "最后", "综上所述", "总的来说", "值得注意的是",
        "不难发现", "可以看出", "众所周知", "随着", "近年来", "在当今",
        "接下来", "综上所述", "请拆解", "请分析", "请重新生成",
        "user", "assistant", "system", "human", "1", "2", "3",
    ]
    
    # 乱码行模式
    garbage_patterns = [
        r'^\s*A\s+A\s*$',
        r'^\s*A\s+AA\s*$',
        r'^\s*[\*\-/]+\s*\d+\s*$',
        r'^\s*[\*\-/]+\s*$',
        r'^\s*\d+\s*$',
        r'^\s*[/\D]k\w+\s*$',
    ]

    for line in md_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        # 跳过对话标记行和AI套话行
        line_lower = line.lower()
        if line_lower in ("user", "assistant", "system", "human"):
            continue
        if line in ("1", "2", "3"):
            continue
        
        # 跳过纯乱码行
        is_garbage = False
        for pat in garbage_patterns:
            if re.match(pat, line, re.IGNORECASE):
                is_garbage = True
                break
        if is_garbage:
            continue
        
        # 跳过AI套话开头行（如果整行就是套话）
        if line_lower in ai_filler_lines or len(line) <= 3:
            continue

        # 去除行内AI套话
        for filler in ["首先", "其次", "最后", "综上所述", "总的来说", "值得注意的是", "不难发现", "可以看出", "众所周知"]:
            line = line.replace(filler, "")
        line = line.strip()
        if not line:
            continue

        # 处理行内 **加粗**（如果AI还用了的话）
        line = re.sub(r'\*\*(.+?)\*\*', rf'<b style="color:{s["accent"]};">\1</b>', line)

        # 处理所有标题级别 → 统一为普通段落（标题本来就是AI痕迹）
        # 但如果行很短（<20字）且没有标点，可能是真正的标题，用加粗
        if line.startswith("#"):
            txt = line.lstrip("#").strip()
            if len(txt) < 20 and not any(c in txt for c in "~。，；"):
                parts.append(f"""<p style="margin:20px 0 10px 0;font-size:16px;font-weight:bold;color:#1a1a1a;">{txt}</p>""")
            else:
                parts.append(f"""<p style="margin:0 0 14px 0;font-size:15px;color:{s['text']};line-height:1.88;">{txt}</p>""")
        elif line.startswith("- ") or line.startswith("—"):
            # 列表 → 转为普通段落，去掉列表符号
            txt = line[2:] if line.startswith("- ") else line[1:].strip()
            # 去除列表内的加粗残留
            txt = re.sub(r'\*\*(.+?)\*\*', rf'<b style="color:{s["accent"]};">\1</b>', txt)
            parts.append(f"""<p style="margin:0 0 14px 0;font-size:15px;color:{s['text']};line-height:1.88;">{txt}</p>""")
        else:
            # 普通段落
            parts.append(f"""<p style="margin:0 0 14px 0;font-size:15px;color:{s['text']};line-height:1.88;">{line}</p>""")

    parts.append(f"""<p style="margin:30px 0 10px 0;text-align:center;color:{s['accent']};font-size:14px;">想看完整买量数据？回复关键词获取更多~</p>""")
    parts.append(f"""<p style="margin:20px 0 0 0;text-align:center;color:#bbb;font-size:11px;border-top:1px solid #eee;padding-top:14px;">广大大 | 出海买量数据专家</p>""")
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
# 飞书通知（Webhook）
# ============================================================
def send_feishu_webhook(results, date_str, errors=None):
    """通过飞书群机器人 webhook 发送推送通知（每篇一行带编号）"""
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if not webhook_url:
        log.info("[INFO] FEISHU_WEBHOOK_URL 未设置，跳过飞书通知")
        return

    import urllib.request

    success_list = [(k, v) for k, v in results.items()]
    fail_list = errors or []

    if success_list and not fail_list:
        titles = "\n".join([f"{i+1}. {v['title']}" for i, (k, v) in enumerate(success_list)])
        text = f"✅ 广大大推送完成({len(success_list)}篇):\n{titles}\n👉 前往草稿箱审核: https://mp.weixin.qq.com"
    elif not success_list and fail_list:
        text = f"⚠️ 全部失败({len(fail_list)}篇):\n" + "\n".join(fail_list)
    else:
        ok_titles = "\n".join([f"{i+1}. {v['title']}" for i, (k, v) in enumerate(success_list)])
        fail_text = "\n".join(fail_list)
        text = f"✅ 成功({len(success_list)}篇):\n{ok_titles}\n\n❌ 失败({len(fail_list)}篇):\n{fail_text}"

    payload = json.dumps({"msg_type": "text", "content": {"text": text}}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            log.info(f"✅ 飞书通知已发送: {body}")
    except Exception as e:
        log.error(f"[ERROR] 飞书通知发送失败: {e}")
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

    # 飞书通知
    if not args.dry_run:
        send_feishu_webhook(results, date_str)


if __name__ == "__main__":
    main()
