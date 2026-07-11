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


SYSTEM_PROMPT = f"""你是在出海买量行业干了多年的人，在广大大(SocialPeta)公众号写文章~

你不是在写报告，是在跟朋友聊天分享你看到的东西~

核心人设（非常重要）：
- 有主观判断，会说"我觉得""说实话""不太看好""这个挺有意思""有点离谱"
- 会跑题、会吐槽、会说不确定的话（"大概""可能""我记得好像是"）
- 句子长短不一，有时一句话就几个字，有时一大段
- 不会面面俱到，只聊自己觉得值得聊的
- 有情绪：会兴奋、会困惑、会不屑

写作铁律（违反=废稿）：
1. 句号用~代替（不用。！）
2. 禁止任何框架：不要开头铺垫/不要结尾总结/不要"首先其次最后/接下来/随着/近年来/在当今"
3. 禁止AI词汇：此外/值得注意的是/毋庸置疑/由此可见/总的来说/综上所述/不难发现/不难看出/可以说
4. 禁止破折号——/禁止"不仅而且"/禁止硬凑三点
5. 正文禁止emoji
6. 不低于800字
7. 只用自然段落，不要列表/编号/表格/加粗标题/引用块
8. 数据穿插在叙述里，不要单独列出来
9. 开头直接说事，不要铺垫背景
10. 每篇结尾引导回复关键词获取资料

标题铁律（非常重要）：
- 禁止用这些套路：XX%、暴涨、飙升、头部玩家、月入XX万、XX万美元、还在XX就是XX
- 标题要像朋友发你的微信消息，不像新闻标题
- 可以用问句、感叹句、口语化的表达
- 不超过25字
- 不要用"|"分隔符

数据规则：
- 数据来源只能用：{', '.join(SAFE_SOURCES)}
- 不确定就写范围或趋势，不要编精确数字
- 数据是辅助，观点才是主体

竞品黑名单（绝对禁止）：
{', '.join(BANNED_COMPETITORS)}

输出格式：
第一行：标题（不加#号，不加书名号）
空一行
然后是正文段落~
不要加任何格式标记（如**加粗**、#### 标题等）"""


def clean_ai_content(text):
    """清洗AI生成的内容，去除对话标记、AI套话、乱码行"""
    if not text:
        return ""
    
    # 1. 逐行处理：去除对话标记和角色标签行
    dialogue_markers = ["user", "assistant", "system", "human", "请拆解", "请重新生成", "请修改", "/Dk", "Dk", "**1", "—**"]
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # 跳过纯对话标记行
        if stripped.lower() in ("user", "assistant", "system", "human"):
            continue
        if stripped in ("1", "2", "3"):
            continue
        # 跳过看起来是乱码的短行（只有A、*、数字等）
        if re.match(r'^[A-Za-z\*\s\-/]+$', stripped) and len(stripped) <= 10:
            continue
        # 跳过包含对话指令的行（如果整行都很短）
        if any(marker in stripped for marker in dialogue_markers) and len(stripped) < 30:
            continue
        cleaned_lines.append(line)
    
    text = "\n".join(cleaned_lines)
    
    # 2. 逐行去除AI套话（用 replace，比正则更可靠）
    ai_openers = [
        "随着", "近年来", "在当今", "首先", "其次", "最后", 
        "综上所述", "总的来说", "值得注意的是", "不难发现", 
        "可以看出", "众所周知", "接下来", "请拆解", "请分析", "请重新生成",
    ]
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        for opener in ai_openers:
            line = line.replace(opener, "")
        # 去除开头的逗号、句号、空格
        line = line.lstrip("，,.。~ ")
        if line.strip():
            cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)
    
    # 3. 去除末尾不完整的"据数据显示~数字"（逐行检查）
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        if re.search(r'据数据显示~\d+$', line.strip()):
            # 去掉末尾的"据数据显示~数字"
            line = re.sub(r'据数据显示~\d+$', '', line.strip()).strip()
        if line.strip():
            cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)
    
    # 4. 去除多余的 "~" 符号（连续多个）
    text = re.sub(r'~{2,}', '~', text)
    text = re.sub(r'~\s*~', '~', text)
    
    # 5. 去除多余空行
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


def get_daily_track(date_str):
    """根据日期确定今天写哪个赛道（每日1篇，按星期轮换）"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = dt.weekday()  # 0=Monday
    # 周一: game, 周二: tool, 周三: drama, 周四: game, 周五: tool
    rotation = ["game", "tool", "drama", "game", "tool", "drama", "drama"]
    return rotation[weekday]


def get_daily_angle(track_key, date_str):
    """根据日期获取当天的写作角度，确保同赛道文章不重复"""
    angles = {
        "game": [
            "挑一款你觉得买量策略最有意思的手游，分析它为什么这么做，你觉得聪明在哪",
            "聊聊某个游戏品类最近的买量变化，你觉得背后的逻辑是什么",
            "对比你观察到的两款同品类游戏，它们的买量打法有什么不同",
            "说一个你觉得买量策略有问题的游戏，哪里有问题，你会怎么改",
            "聊聊最近手游买量素材的变化趋势，什么素材类型在涨什么在跌",
        ],
        "tool": [
            "挑一个工具App聊聊它的增长策略，你觉得聪明在哪或者蠢在哪",
            "工具出海这个品类最近有什么变化让你觉得值得说",
            "吐槽一个工具App的买量操作，你觉得哪里做得不对",
            "聊聊工具App买量和游戏买量的本质区别，为什么策略不一样",
            "说一个你觉得被低估的工具品类，为什么觉得它有机会",
        ],
        "drama": [
            "聊一部你觉得推广做得好的短剧，分析它的素材策略",
            "短剧买量最近有什么新打法让你注意到了",
            "对比两个短剧平台的策略差异，你更看好谁",
            "说一部你觉得买量砸钱但效果一般的短剧，为什么觉得效果一般",
            "聊聊短剧出海不同市场的差异，哪个市场你觉得有机会",
        ],
    }
    track_angles = angles.get(track_key, angles["game"])
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    idx = dt.timetuple().tm_yday % len(track_angles)
    return track_angles[idx]


# 标题反模式检测
TITLE_BANNED_PATTERNS = [
    r'\d+%', r'暴涨', r'飙升', r'猛涨', r'头部玩家', r'月入', r'万美元',
    r'还在.*就是', r'彻底', r'颠覆', r'降维打击', r'\|',
]
TITLE_BANNED_REGEX = [re.compile(p, re.IGNORECASE) for p in TITLE_BANNED_PATTERNS]


def check_title_quality(title):
    """检查标题是否符合要求，返回 (is_ok, reason)"""
    if not title or len(title) < 5:
        return False, "标题太短"
    if len(title) > 30:
        return False, f"标题太长({len(title)}字)"
    for pat in TITLE_BANNED_REGEX:
        if pat.search(title):
            return False, f"标题包含套路模式: {pat.pattern}"
    return True, ""


def generate_article(track_key, date_str, retry=0, max_retry=3):
    track = TRACKS[track_key]
    angle = get_daily_angle(track_key, date_str)

    prompt = f"""今天是{date_str}，写一篇出海{track['name']}相关的文章~

今天聊的角度：{angle}

要求：
1. 有你自己的判断和观点，不要面面俱到
2. 像和朋友聊天一样写，可以跑题、可以吐槽、可以说不确定的话
3. 如果提到数据，穿插在叙述里，不要单独列出来
4. 句号用~，禁止emoji，禁止竞品名
5. 第一行是标题（不超过25字，禁止用"XX%""暴涨""头部玩家""月入XX万"等套路）
6. 标题后空一行，然后是正文
7. 正文不低于800字
8. 只输出纯文本段落，不要加粗、不要列表、不要编号
9. 结尾引导回复关键词获取资料

直接输出，标题+正文~"""

    log.info(f"🤖 生成 {track['name']} (角度: {angle[:30]}...)")

    client = get_siliconflow_client()

    resp = client.chat.completions.create(
        model=SILICONFLOW_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.85,
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
            return generate_article(track_key, date_str, retry=retry+1, max_retry=max_retry)
        else:
            log.error(f"❌ 重试耗尽，使用备用内容")
            return f"{track['name']}出海买量观察\n\n今天先聊到这里，数据更新中~回复「买量」获取最新数据~"

    # 检查竞品
    clean_check, banned = check_no_competitor(text)
    if not clean_check:
        log.error(f"❌ 发现竞品: {banned}，重新生成...")
        if retry < max_retry:
            return generate_article(track_key, date_str, retry=retry+1, max_retry=max_retry)
        else:
            text = text.replace(banned, "某平台")
            log.warning(f"⚠️ 手动替换竞品名: {banned} -> 某平台")

    log.info(f"✅ {track['name']} {len(text)}字")
    return text


# ============================================================
# 封面图生成（PIL 本地 — 零外部依赖）
# ============================================================
def generate_covers(track_key=None):
    """生成封面图，返回 {track_key: bytes}。传入 track_key 只生成1张"""
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

    # 只生成指定赛道的封面
    keys = [track_key] if track_key else list(configs.keys())
    covers = {}

    for tk in keys:
        cfg = configs[tk]
        w, h = 900, 383
        img = Image.new("RGB", (w, h), cfg["colors"][0])

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

        import random
        random.seed(hash(tk + str(datetime.now().day)) % 10000)

        if cfg["shapes"] == "grid":
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

        buf = BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        covers[tk] = buf.getvalue()

    log.info(f"🎨 封面图生成完成 ({len(covers)}张)")
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
    """从文章中提取标题和摘要。新格式：第一行是标题，空行后是正文"""
    lines = text.strip().split("\n")

    # 第一行是标题
    title_line = ""
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped:
            title_line = stripped
            body_start = i + 1
            break

    # 检查标题质量
    ok, reason = check_title_quality(title_line)
    if not ok:
        log.warning(f"⚠️ 标题问题: {reason}，使用备用标题")
        # 如果标题不合格，从正文第一句提取
        for line in lines[body_start:]:
            stripped = line.strip()
            if stripped and len(stripped) > 5:
                title_line = stripped[:25].rstrip("~") + "~"
                break
        else:
            title_line = f"{track_name}出海买量观察"

    title = title_line[:30].rstrip("~")
    if not title:
        title = f"{track_name}出海买量观察"

    # 摘要从正文提取
    body_lines = lines[body_start:]
    body_text = " ".join(l.strip() for l in body_lines if l.strip())
    body_text = body_text.replace("~", "").strip()
    digest = body_text[:90].rstrip() + "~"

    return title, digest


# ============================================================
# 飞书通知（Webhook）
# ============================================================
def send_feishu_webhook(results, date_str, errors=None):
    """通过飞书群机器人 webhook 发送推送通知（每篇一行带编号）
    
    注意：飞书webhook设置了关键词安全验证，关键词是"公众号"，
    所有消息必须包含"公众号"才能发送成功。
    """
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if not webhook_url:
        log.info("[INFO] FEISHU_WEBHOOK_URL 未设置，跳过飞书通知")
        return

    import urllib.request

    success_list = [(k, v) for k, v in results.items()]
    fail_list = errors or []

    # 飞书webhook关键词是"公众号"，每条消息必须包含
    if success_list and not fail_list:
        titles = "\n".join([f"{i+1}. {v['title']}" for i, (k, v) in enumerate(success_list)])
        text = f"✅ 公众号推送完成({len(success_list)}篇) | {date_str}\n{titles}\n👉 前往草稿箱审核: https://mp.weixin.qq.com"
    elif not success_list and fail_list:
        text = f"⚠️ 公众号推送全部失败({len(fail_list)}篇) | {date_str}\n" + "\n".join(fail_list)
    else:
        ok_titles = "\n".join([f"{i+1}. {v['title']}" for i, (k, v) in enumerate(success_list)])
        fail_text = "\n".join(fail_list)
        text = f"✅ 公众号推送成功({len(success_list)}篇) | {date_str}\n{ok_titles}\n\n❌ 失败({len(fail_list)}篇):\n{fail_text}"

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
    log.info(f"\n{'='*40}\n📝 {track_name} | {date_str}\n{'='*40}")

    # 生成文章
    article = generate_article(track_key, date_str)
    title, digest = extract_title_digest(article, track_name, date_str)
    log.info(f"📌 {title}")

    # 去掉标题行，只保留正文
    lines = article.strip().split("\n")
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip():
            body_start = i + 1
            break
    body_text = "\n".join(lines[body_start:]).strip()

    # 保存封面
    cover_path = os.path.join(output_dir, f"cover_{track_key}.png")
    with open(cover_path, "wb") as f:
        f.write(cover_data)

    # HTML
    html = markdown_to_html(body_text, track_key)

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


def already_published_today(date_str):
    """检查今天是否已经有成功的 workflow 运行（防重复推送）"""
    gh_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if not gh_token:
        log.info("[INFO] 无 GH_TOKEN，跳过去重检查")
        return False

    try:
        import urllib.request
        url = (
            f"https://api.github.com/repos/tylique-1001/ggd-wechat-publisher/actions/workflows/publish-daily.yml/runs"
            f"?status=success&created={date_str}&per_page=5"
        )
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {gh_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ggd-publisher",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        count = data.get("total_count", 0)
        if count > 0:
            log.info(f"[DEDUP] {date_str} 已有 {count} 次成功运行，跳过")
            return True
        log.info(f"[DEDUP] {date_str} 无历史成功运行，继续执行")
        return False
    except Exception as e:
        log.warning(f"[WARN] 去重检查失败（不影响执行）: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--track", choices=["game", "tool", "drama"], help="手动指定赛道（默认按日期轮换）")
    parser.add_argument("--output", default="/tmp/gzh_output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="忽略去重检查，强制推送")
    args = parser.parse_args()

    date_str = args.date
    output_dir = args.output
    os.makedirs(output_dir, exist_ok=True)

    # 每日1篇，按星期轮换赛道
    track_key = args.track or get_daily_track(date_str)
    track_name = TRACKS[track_key]["name"]
    angle = get_daily_angle(track_key, date_str)

    log.info(f"🚀 广大大云端发布 | {date_str} | 赛道: {track_name}")
    log.info(f"🤖 硅基流动(国内永久免费) | 🎨 PIL本地封面")
    log.info(f"📝 角度: {angle[:50]}...")

    # 去重检查
    if not args.force and not args.dry_run:
        if already_published_today(date_str):
            log.info("✅ 今天已推送，退出")
            print(json.dumps({"skipped": True, "reason": f"{date_str} already published", "date": date_str}))
            return

    # 只生成1张封面
    cover_datas = generate_covers(track_key)

    token = None if args.dry_run else wechat_get_token()

    results = {}
    try:
        if track_key in cover_datas:
            did, info = publish_track(token, track_key, date_str, cover_datas[track_key], output_dir)
            if did:
                results[track_key] = {"draft_id": did, **info}
    except Exception as e:
        log.error(f"❌ {track_name} 失败: {e}")
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
