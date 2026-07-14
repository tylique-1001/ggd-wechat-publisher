#!/usr/bin/env python3
"""
云端每日发布脚本 — GitHub Actions 运行，不依赖本地机器

内容方向：对齐《广大大公众号定制化运营方案》
- 5大内容支柱：竞品拆解台/方法论实战/数据月报/客户故事/互动参与
- 发布频率：周二/周四（方案规定）
- 文章结构：每个支柱有明确模板
- 搜索真实行业新闻作为数据上下文

环境变量（GitHub Secrets）：
  DEEPSEEK_API_KEY    — DeepSeek API 密钥
  WEIXIN_APPID         — 广大大公众号 AppID
  WEIXIN_SECRET        — 广大大公众号 AppSecret
  FEISHU_WEBHOOK_URL   — 飞书群机器人 Webhook
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
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from io import BytesIO

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============================================================
# 配置
# ============================================================
WEIXIN_APPID = os.environ.get("WEIXIN_APPID", "wx94eb6ba27c82a203")
WEIXIN_SECRET = os.environ.get("WEIXIN_SECRET", "")
AUTHOR = "zylon"

# DeepSeek 官方 API（国内，兼容 OpenAI SDK，新用户送 ¥5 免费额度）
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"  # DeepSeek-V4 Flash (Chat alias)，速度高质量好

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

# 赛道（用于封面配色和搜索方向）
TRACKS = {
    "game": {
        "name": "游戏",
        "search_hint": "出海手游 SLG 休闲 买量 素材 2026",
        "products": ["点点互动", "4399", "米哈游", "莉莉丝", "三七互娱", "FunPlus", "IGG", "江娱互动"],
    },
    "tool": {
        "name": "工具",
        "search_hint": "出海工具App 清理 壁纸 AI助手 买量 2026",
        "products": ["Lemon", "CapCut", "Remini", "Photoroom", "Bumble", "Bigo"],
    },
    "drama": {
        "name": "短剧",
        "search_hint": "出海短剧 ReelShort DramaBox 买量 2026",
        "products": ["ReelShort", "DramaBox", "ShortMax", "FlexTV", "TopShort", "DramaWave"],
    },
}

# ============================================================
# 5大内容支柱（对齐定制方案）
# ============================================================
PILLARS = {
    "competitor": {
        "name": "竞品拆解台",
        "description": "用广大大数据拆解一个热门出海产品的完整买量策略",
        "structure": [
            "开篇：用一个具体场景或发现引入，说清楚你在拆解哪个产品，最让你震惊的发现是什么",
            "数据总览：该产品近30天广告量、素材量、投放地区分布（数据穿插在叙述中，不要列表）",
            "素材拆解：它跑量最好的素材是什么类型？前3秒什么钩子？什么创意逻辑？为什么能跑量？",
            "策略复盘：它的投放节奏是什么？前期怎么测素材、中期怎么扩量、后期怎么优化？",
            "结尾：你的判断和建议，引导回复关键词获取完整素材包",
        ],
        "cta_keyword": "拆解",
    },
    "methodology": {
        "name": "方法论实战",
        "description": "优化师和设计师可以直接用的工作方法",
        "structure": [
            "问题场景：描述优化师或设计师遇到的真实困境，要有代入感",
            "常见错误：大多数人会怎么做？为什么效果不好？",
            "正确方法：应该怎么做？分步骤说清楚，每步都有具体操作",
            "数据验证：用具体数字证明这个方法有效（比如CPA从多少降到多少）",
            "结尾：引导回复关键词获取操作手册",
        ],
        "topics": [
            "Meta广告素材优化：如何从测素材到扩量的完整流程",
            "TikTok买量策略：短剧出海怎么投效果最好",
            "素材CTR提升：前3秒钩子设计的4种方法",
            "CPA优化：如何把获客成本从$12压到$7",
            "RTA投放策略：什么场景下该用RTA",
            "素材A/B测试：怎么设计测试方案才能快速找到跑量素材",
            "投放节奏管理：什么时候该加量什么时候该收",
            "多平台投放：Meta和TikTok的素材策略差异",
        ],
        "cta_keyword": "SOP",
    },
    "monthly_report": {
        "name": "数据月报",
        "description": "行业大盘风向标，建立权威感",
        "structure": [
            "月度关键数据速览：3个核心数字开篇抓眼球",
            "分品类趋势：游戏、短剧、工具的买量趋势对比",
            "分地区差异：美国、日韩、东南亚市场的投放成本和效果对比",
            "分平台效能：Meta、Google、TikTok的投放表现对比",
            "结尾：下月趋势预判，引导回复「月报」下载完整报告",
        ],
        "cta_keyword": "月报",
    },
    "customer_story": {
        "name": "客户故事",
        "description": "用真实的客户案例证明工具价值",
        "structure": [
            "客户遇到了什么问题（可以匿名，但问题描述要具体）",
            "他们用了什么方法或工具来解决",
            "结果是什么（必须有具体数据：提升X%、节省X万、效率翻X倍）",
            "客户评价（可以匿名引用一句话）",
            "结尾：引导回复关键词获取类似案例分析",
        ],
        "cta_keyword": "案例",
    },
}


def check_no_competitor(text):
    for banned in BANNED_COMPETITORS:
        if banned.lower() in text.lower():
            return False, banned
    return True, None


# ============================================================
# 搜索真实行业新闻（给AI提供数据上下文）
# ============================================================
def search_news(query, num=5):
    """通过Google News RSS搜索最近新闻，返回标题列表"""
    try:
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ggd-bot/1.0)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        items = root.findall(".//item")[:num]
        results = []
        for item in items:
            title = item.findtext("title", "").strip()
            desc = item.findtext("description", "").strip()
            if title:
                # 同时收集标题和描述，给AI更多信息
                results.append(f"- {title}" + (f"（{desc[:100]}）" if desc and desc != title else ""))
        log.info(f"搜索「{query[:30]}...」获取到 {len(results)} 条新闻")
        return "\n".join(results) if results else ""
    except Exception as e:
        log.warning(f"[WARN] 搜索失败（不影响生成）: {e}")
        return ""


def search_product_data(track_key, products):
    """搜索具体产品的买量数据，返回多维度搜索结果"""
    all_results = []
    # 搜索2-3个具体产品
    for product in products[:3]:
        query = f"{product} 出海 买量 广告 投放 2026"
        news = search_news(query, num=3)
        if news:
            all_results.append(f"【{product}】\n{news}")
    # 再搜一个行业趋势
    trend_query = f"出海买量 {track_key} 趋势 2026"
    trend_news = search_news(trend_query, num=3)
    if trend_news:
        all_results.append(f"【行业趋势】\n{trend_news}")
    return "\n\n".join(all_results) if all_results else "（未搜索到相关新闻）"


# ============================================================
# 微信 API
# ============================================================
def wechat_get_token():
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={WEIXIN_APPID}&secret={WEIXIN_SECRET}"
    r = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=30)
    data = json.loads(r.stdout)
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"Token 失败: {data.get('errmsg')}")
    log.info(f"Token OK")
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
    log.info(f"封面: {mid}")
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
# 硅基流动 文本生成
# ============================================================
def get_deepseek_client():
    from openai import OpenAI
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未设置")
    return OpenAI(api_key=key, base_url=DEEPSEEK_API_BASE)


# ============================================================
# SYSTEM_PROMPT（对齐定制方案的写作风格）
# ============================================================
SYSTEM_PROMPT = f"""你是广大大(SocialPeta)公众号的资深编辑，在出海买量行业干了多年~

## 最重要的一条规则（违反=废稿）
你手里没有广大大的实时数据。你不能编造任何具体数字。
如果你在搜索结果里看到了某个数据，可以引用并标注来源。
如果没看到，就写你的分析、观点、判断、行业观察——但不要编一个精确数字出来。
比如可以写"从行业整体来看，短剧出海的买量成本在持续走高"，但不能写"ReelShort上月投放了12万条广告"——除非搜索结果里有这个数据。

## 写作风格
- 像跟同行吃饭时聊起一个案例：有故事、有数据、有观点、有建议
- 有情绪和态度：会震惊、会困惑、会兴奋、会吐槽
- 给读者可落地的行动建议，不是空谈理论
- 用具体的产品名和具体的场景，不要泛泛而谈

## 写作铁律
1. 句号用~代替（不用。！）
2. 禁止AI套话：随着/近年来/在当今/首先/其次/最后/此外/值得注意的是/综上所述/总的来说/不难发现/可以看出
3. 禁止破折号——/禁止"不仅而且"/禁止硬凑三点
4. 正文禁止emoji
5. 不低于1000字
6. 只用自然段落写作，绝对不要列表/编号/表格/加粗标题
7. 绝对不要写小标题或章节标记，不要出现"数据总览""素材拆解""问题场景"这种分段标题，让结构自然融入叙述
8. 不要用#号或任何markdown标记
9. 开头直接说事，不要铺垫背景
10. 每篇结尾引导回复关键词获取资料

## 标题要求
- 像朋友发你的微信消息，不像新闻标题
- 不超过25字
- 禁止：XX%/暴涨/飙升/头部玩家/月入XX万
- 不要用"|"分隔符
- 不要包含"竞品拆解台""方法论"等栏目名
- 不要加#号或任何标记
- 第一行直接写标题文字，不加任何前缀

## 数据规则
- 只引用搜索结果中出现的数据，标注来源
- 没有搜索结果支撑的，写趋势性判断（"大概""可能""我记得好像是"）
- 绝对禁止编造精确数字
- 绝对禁止写"据广大大数据显示"——你手里没有广大大数据
- 可以写"从广大大的行业观察来看"这种定性描述

## 竞品黑名单（绝对禁止出现）
{', '.join(BANNED_COMPETITORS)}

## 输出格式
第一行：标题（纯文字，不加#不加书名号不加前缀）
空一行
然后是正文段落~"""


# ============================================================
# 内容清洗和质量检查
# ============================================================
def clean_ai_content(text):
    """清洗AI生成的内容"""
    if not text:
        return ""

    # 去除body中的#小标题行（如"### 数据总览"）
    # 保留第一行（可能是标题）
    lines = text.split("\n")
    cleaned_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        # 如果是#开头的行且不是第一行，去掉#号保留内容（或整行去掉如果是短标题）
        if i > 0 and stripped.startswith("#"):
            txt = stripped.lstrip("#").strip()
            # 如果是短小标题（如"数据总览""问题场景"），直接去掉
            if len(txt) <= 10 and not any(c in txt for c in "~。，；！？"):
                continue
            # 否则保留内容但去掉#号
            line = txt
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    dialogue_markers = ["user", "assistant", "system", "human", "请拆解", "请重新生成", "请修改", "/Dk", "Dk", "**1", "—**"]
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower() in ("user", "assistant", "system", "human"):
            continue
        if stripped in ("1", "2", "3"):
            continue
        if re.match(r'^[A-Za-z\*\s\-/]+$', stripped) and len(stripped) <= 10:
            continue
        if any(marker in stripped for marker in dialogue_markers) and len(stripped) < 30:
            continue
        # 去除所有**加粗**标记（正文不使用加粗）
        line = re.sub(r'\*\*(.+?)\*\*', r'\1', line)
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)

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
        line = line.lstrip("，,.。~ ")
        if line.strip():
            cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    text = re.sub(r'~{2,}', '~', text)
    text = re.sub(r'~\s*~', '~', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


def check_content_quality(text):
    """检查内容质量"""
    if not text or len(text) < 500:
        return False, f"内容太短({len(text)}字)"
    dialogue_words = ["user", "assistant", "system", "请拆解", "请重新生成", "请分析"]
    for word in dialogue_words:
        if word.lower() in text.lower():
            return False, f"包含对话标记'{word}'"
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    total_chars = len(text)
    if total_chars > 0 and chinese_chars / total_chars < 0.3:
        return False, "中文比例过低"
    weird_lines = [l for l in text.split("\n") if re.match(r'^[A-Z\*\s\-/]{1,10}$', l.strip())]
    if len(weird_lines) > 3:
        return False, "包含过多乱码行"
    return True, ""


# ============================================================
# 内容支柱调度（对齐方案：周二/四发布）
# ============================================================
def get_daily_pillar(date_str):
    """根据日期确定今天的内容支柱和赛道
    
    方案规定：
    - 周二：竞品拆解台（默认），每月最后一个周二=数据月报
    - 周四：方法论实战（默认），每月第二/四周四=客户故事
    - 其他日期不发布
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = dt.weekday()  # 0=Monday, 1=Tuesday, 3=Thursday

    if weekday == 1:  # Tuesday
        # 检查是否是本月最后一个周二
        next_week = dt + timedelta(days=7)
        if next_week.month != dt.month:
            return "monthly_report", None
        return "competitor", get_track_rotation(date_str)

    if weekday == 3:  # Thursday
        week_of_month = (dt.day - 1) // 7 + 1
        if week_of_month in (2, 4):
            return "customer_story", None
        return "methodology", None

    return None, None


def get_track_rotation(date_str):
    """在竞品拆解台中轮换赛道（游戏/工具/短剧）"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    tracks = ["game", "tool", "drama"]
    # 用周数轮换
    week_num = dt.isocalendar()[1]
    return tracks[week_num % 3]


def get_methodology_topic(date_str):
    """轮换方法论主题"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    topics = PILLARS["methodology"]["topics"]
    idx = dt.timetuple().tm_yday % len(topics)
    return topics[idx]


# 标题反模式检测
TITLE_BANNED_PATTERNS = [
    r'\d+%', r'暴涨', r'飙升', r'猛涨', r'头部玩家', r'月入', r'万美元',
    r'还在.*就是', r'彻底', r'颠覆', r'降维打击', r'\|',
]
TITLE_BANNED_REGEX = [re.compile(p, re.IGNORECASE) for p in TITLE_BANNED_PATTERNS]


def check_title_quality(title):
    if not title or len(title) < 5:
        return False, "标题太短"
    if len(title) > 30:
        return False, f"标题太长({len(title)}字)"
    for pat in TITLE_BANNED_REGEX:
        if pat.search(title):
            return False, f"标题包含套路模式: {pat.pattern}"
    return True, ""


# ============================================================
# 文章生成（对齐5大内容支柱）
# ============================================================
def generate_article(pillar_key, track_key, date_str, retry=0, max_retry=3):
    pillar = PILLARS[pillar_key]
    structure_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(pillar["structure"]))
    cta_keyword = pillar.get("cta_keyword", "资料")

    # 搜索具体产品数据
    if track_key:
        products = TRACKS[track_key]["products"]
        news_context = search_product_data(TRACKS[track_key]["name"], products)
    elif pillar_key == "monthly_report":
        news_context = search_news("出海买量 数据 月报 游戏 短剧 工具 2026", num=5)
    elif pillar_key == "customer_story":
        news_context = search_news("出海买量 案例 优化师 素材 2026", num=5)
    else:
        news_context = search_news("出海买量 优化 素材 Meta TikTok 2026", num=5)

    # 构建prompt（根据支柱类型）
    if pillar_key == "competitor":
        track_name = TRACKS[track_key]["name"]
        products = TRACKS[track_key]["products"]
        product_hint = f"从这些产品里选1-2个写：{', '.join(products[:5])}（优先选搜索结果里有新闻的）"
        prompt = f"""今天是{date_str}，写一篇竞品拆解文章~

拆解方向：出海{track_name}买量
{product_hint}

以下是搜索到的真实行业新闻（这是你唯一的数据来源，只能引用这些里面的数据，不能编造其他数字）：
{news_context}

文章结构（必须遵循，但绝对不要出现小标题或分段标记，让结构自然融入叙述中）：
{structure_text}

写作要点：
- 开头用一个具体场景引入，说你在看哪个产品的什么现象
- 如果搜索结果里有具体数据（广告量、素材量、下载量等），引用它并说"根据XX报道"
- 如果搜索结果里没有具体数据，就写你的分析和判断，不要编数字
- 绝对不要写"据广大大数据显示"——你没有广大大数据
- 写你的个人判断和观点，不是中立报道
- 有情绪：会震惊、会困惑、会兴奋
- 句号用~，禁止emoji，禁止竞品名
- 第一行是标题（纯文字，不超过25字，口语化，不加#不加前缀）
- 标题后空一行，然后是正文
- 正文不低于1000字
- 正文不要出现任何小标题、不要用#号、不要用加粗
- 结尾引导回复「{cta_keyword}」获取完整素材包

直接输出，标题+正文~"""

    elif pillar_key == "methodology":
        topic = get_methodology_topic(date_str)
        prompt = f"""今天是{date_str}，写一篇方法论文章~

主题：{topic}

以下是搜索到的行业动态（可以引用里面的数据，不能编造其他数字）：
{news_context}

文章结构（必须遵循，但绝对不要出现小标题或分段标记）：
{structure_text}

写作要点：
- 问题场景要有代入感，像在说一个你认识的优化师遇到的真实困境
- 常见错误要说清楚为什么不行，举具体例子
- 正确方法用自然段落描述，不要列表不要编号
- 如果搜索结果里有具体数据，引用它
- 如果没有具体数据，写你的经验判断（"一般来讲""我观察到的规律是"），不要编精确数字
- 绝对不要写"据广大大数据显示"
- 句号用~，禁止emoji，禁止竞品名
- 第一行是标题（纯文字，不超过25字，口语化，不加#不加前缀）
- 标题后空一行，然后是正文
- 正文不低于1000字
- 正文不要出现任何小标题、不要用#号、不要用加粗
- 结尾引导回复「{cta_keyword}」获取操作手册

直接输出，标题+正文~"""

    elif pillar_key == "monthly_report":
        month_name = datetime.strptime(date_str, "%Y-%m-%d").strftime("%m月")
        prompt = f"""今天是{date_str}，写一篇{month_name}数据月报文章~

以下是搜索到的行业动态（可以引用里面的数据，不能编造其他数字）：
{news_context}

文章结构（必须遵循，但绝对不要出现小标题或分段标记）：
{structure_text}

写作要点：
- 如果搜索结果里有具体数据，引用它并标注来源
- 如果没有具体数据，写趋势性判断（"整体来看XX在涨""XX品类投放量在增加"），不要编精确数字
- 绝对不要写"据广大大数据显示"——你没有广大大数据
- 可以写"从行业公开信息来看"
- 有你的趋势预判
- 句号用~，禁止emoji，禁止竞品名
- 第一行是标题（纯文字，不超过25字）
- 标题后空一行，然后是正文
- 正文不低于1000字
- 正文不要出现任何小标题、不要用#号、不要用加粗
- 结尾引导回复「{cta_keyword}」下载完整报告

直接输出，标题+正文~"""

    elif pillar_key == "customer_story":
        prompt = f"""今天是{date_str}，写一篇客户故事文章~

以下是搜索到的行业动态（可以引用里面的数据，不能编造其他数字）：
{news_context}

文章结构（必须遵循，但绝对不要出现小标题或分段标记）：
{structure_text}

写作要点：
- 客户可以匿名（"某SLG团队""某短剧平台"），但遇到的问题要具体
- 写成叙事，像在讲一个真实的故事
- 如果搜索结果里有具体数据可以引用
- 如果没有具体数据，写你的经验判断，不要编精确数字
- 绝对不要写"据广大大数据显示"
- 句号用~，禁止emoji，禁止竞品名
- 第一行是标题（纯文字，不超过25字，口语化，不加#不加前缀）
- 标题后空一行，然后是正文
- 正文不低于1000字
- 正文不要出现任何小标题、不要用#号、不要用加粗
- 结尾引导回复「{cta_keyword}」获取类似案例分析

直接输出，标题+正文~"""
    else:
        prompt = f"写一篇出海买量相关文章~\n结构：{structure_text}\n句号用~，第一行标题~"

    log.info(f"生成 {pillar['name']}..." + (f" 赛道: {TRACKS[track_key]['name']}" if track_key else ""))

    client = get_deepseek_client()

    # DeepSeek官方API：直接使用deepseek-chat（V4 Flash，速度快质量高）
    text = None
    try:
        log.info(f"使用模型: {DEEPSEEK_MODEL}")
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=8000,
        )
        text = resp.choices[0].message.content
    except Exception as e:
        log.error(f"DeepSeek API 调用失败: {e}")
        raise
    text = clean_ai_content(text)

    # 质量检查
    ok, reason = check_content_quality(text)
    if not ok:
        log.error(f"内容质量不通过: {reason}，重试({retry+1}/{max_retry})...")
        if retry < max_retry:
            return generate_article(pillar_key, track_key, date_str, retry=retry+1, max_retry=max_retry)
        else:
            log.error(f"重试耗尽，使用备用内容")
            return f"{pillar['name']}观察\n\n今天先聊到这里，数据更新中~回复「{cta_keyword}」获取资料~"

    # 检查竞品
    clean_check, banned = check_no_competitor(text)
    if not clean_check:
        log.error(f"发现竞品: {banned}，重新生成...")
        if retry < max_retry:
            return generate_article(pillar_key, track_key, date_str, retry=retry+1, max_retry=max_retry)
        else:
            text = text.replace(banned, "某平台")
            log.warning(f"手动替换竞品名: {banned} -> 某平台")

    log.info(f"{pillar['name']} {len(text)}字")
    return text


# ============================================================
# 封面图生成（PIL 本地）
# ============================================================
def generate_cover(style_key):
    """使用 Pollinations.ai (免费FLUX模型) 生成封面图，返回 bytes
    
    按赛道/支柱选择不同风格prompt，生成与文章内容相关的配图。
    失败时回退到PIL渐变封面。
    """
    cover_prompts = {
        "game": "abstract dark gaming analytics, data visualization with red and gold accents, mobile game marketing strategy, cinematic lighting, professional, no text no watermark",
        "tool": "clean minimalist technology app marketing concept, teal and green accents, productivity tools, digital marketing, professional, no text no watermark",
        "drama": "cinematic short drama streaming concept, warm purple and amber tones, film reel, storytelling atmosphere, professional, no text no watermark",
        "competitor": "abstract dark competitive analysis, data charts, red gold accents, mobile app marketing, cinematic, professional, no text no watermark",
        "methodology": "clean instructional marketing analytics concept, teal accents, workflow optimization, professional, no text no watermark",
        "monthly_report": "data dashboard analytics concept, purple amber tones, charts and graphs, industry report, professional, no text no watermark",
        "customer_story": "warm business success story concept, orange tones, growth chart, professional, no text no watermark",
    }

    prompt = cover_prompts.get(style_key, cover_prompts["competitor"])
    encoded_prompt = urllib.parse.quote(prompt)
    # Pollinations.ai 免费图片生成，不需要API key
    image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1792&height=1024&nologo=true&model=flux"

    try:
        log.info(f"下载封面图 (Pollinations.ai FLUX)...")
        req = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0 (compatible; ggd-bot/1.0)"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            image_data = resp.read()

        if len(image_data) < 5000:
            raise RuntimeError(f"图片太小({len(image_data)}字节)，可能生成失败")

        # 中心裁剪为 900x383
        from PIL import Image
        img = Image.open(BytesIO(image_data))
        w, h = img.size
        target_ratio = 900 / 383
        current_ratio = w / h
        if current_ratio > target_ratio:
            new_w = int(h * target_ratio)
            left = (w - new_w) // 2
            img = img.crop((left, 0, left + new_w, h))
        else:
            new_h = int(w / target_ratio)
            top = (h - new_h) // 2
            img = img.crop((0, top, w, top + new_h))
        img = img.resize((900, 383), Image.LANCZOS)

        buf = BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        log.info(f"封面图生成完成 (Pollinations.ai, style: {style_key})")
        return buf.getvalue()

    except Exception as e:
        log.warning(f"Pollinations.ai 失败({e})，回退到PIL渐变封面")
        return _generate_pil_cover(style_key)


def _generate_pil_cover(style_key):
    """PIL渐变封面（备用方案）"""
    from PIL import Image, ImageDraw
    import random

    configs = {
        "game": {"colors": [(26, 26, 46), (22, 33, 62)], "accent": (220, 38, 38), "accent2": (251, 191, 36), "shapes": "grid"},
        "tool": {"colors": [(240, 253, 244), (255, 255, 255)], "accent": (13, 148, 136), "accent2": (20, 184, 166), "shapes": "circles"},
        "drama": {"colors": [(250, 249, 247), (254, 243, 199)], "accent": (124, 58, 237), "accent2": (245, 158, 11), "shapes": "waves"},
        "competitor": {"colors": [(26, 26, 46), (22, 33, 62)], "accent": (220, 38, 38), "accent2": (251, 191, 36), "shapes": "grid"},
        "methodology": {"colors": [(240, 253, 244), (255, 255, 255)], "accent": (13, 148, 136), "accent2": (20, 184, 166), "shapes": "circles"},
        "monthly_report": {"colors": [(250, 249, 247), (254, 243, 199)], "accent": (124, 58, 237), "accent2": (245, 158, 11), "shapes": "waves"},
        "customer_story": {"colors": [(255, 247, 237), (255, 255, 255)], "accent": (194, 65, 12), "accent2": (234, 88, 12), "shapes": "circles"},
    }

    cfg = configs.get(style_key, configs["competitor"])
    w, h = 900, 383
    img = Image.new("RGB", (w, h), cfg["colors"][0])

    c1, c2 = cfg["colors"]
    for y in range(h):
        ratio = y / h
        r = int(c1[0] + (c2[0] - c1[0]) * ratio)
        g = int(c1[1] + (c2[1] - c1[1]) * ratio)
        b = int(c1[2] + (c2[2] - c1[2]) * ratio)
        for x in range(w):
            img.putpixel((x, y), (r, g, b))

    draw = ImageDraw.Draw(img)
    random.seed(hash(style_key + str(datetime.now().day)) % 10000)

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
    else:
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
    log.info(f"封面图生成完成 (PIL备用, style: {style_key})")
    return buf.getvalue()


# ============================================================
# HTML 排版
# ============================================================
def markdown_to_html(md_text, style_key):
    """将文本转换为微信兼容的 HTML
    
    排版原则：简洁自然，不像AI模板
    """
    styles = {
        "game": {"bg": "#ffffff", "text": "#2d2d2d", "accent": "#c0392b"},
        "tool": {"bg": "#ffffff", "text": "#2d2d2d", "accent": "#0d9488"},
        "drama": {"bg": "#ffffff", "text": "#2d2d2d", "accent": "#d97706"},
        "competitor": {"bg": "#ffffff", "text": "#2d2d2d", "accent": "#c0392b"},
        "methodology": {"bg": "#ffffff", "text": "#2d2d2d", "accent": "#0d9488"},
        "monthly_report": {"bg": "#ffffff", "text": "#2d2d2d", "accent": "#d97706"},
        "customer_story": {"bg": "#ffffff", "text": "#2d2d2d", "accent": "#c2410c"},
    }
    s = styles.get(style_key, styles["competitor"])

    parts = [f"""<section style="padding:12px 10px;font-size:15px;line-height:1.88;color:{s['text']};word-break:break-all;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;background:{s['bg']};">"""]
    parts.append(f"""<p style="text-align:center;margin-bottom:20px;color:#999;font-size:12px;">点击上方蓝字关注，每周解锁出海买量新玩法~</p>""")

    ai_filler_lines = [
        "首先", "其次", "最后", "综上所述", "总的来说", "值得注意的是",
        "不难发现", "可以看出", "众所周知", "随着", "近年来", "在当今",
        "接下来", "请拆解", "请分析", "请重新生成",
        "user", "assistant", "system", "human", "1", "2", "3",
    ]

    garbage_patterns = [
        r'^\s*A\s+A\s*$', r'^\s*A\s+AA\s*$',
        r'^\s*[\*\-/]+\s*\d+\s*$', r'^\s*[\*\-/]+\s*$',
        r'^\s*\d+\s*$', r'^\s*[/\D]k\w+\s*$',
    ]

    for line in md_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        line_lower = line.lower()
        if line_lower in ("user", "assistant", "system", "human"):
            continue
        if line in ("1", "2", "3"):
            continue

        is_garbage = False
        for pat in garbage_patterns:
            if re.match(pat, line, re.IGNORECASE):
                is_garbage = True
                break
        if is_garbage:
            continue

        if line_lower in ai_filler_lines or len(line) <= 3:
            continue

        for filler in ["首先", "其次", "最后", "综上所述", "总的来说", "值得注意的是", "不难发现", "可以看出", "众所周知"]:
            line = line.replace(filler, "")
        line = line.strip()
        if not line:
            continue

        # 去掉**加粗**标记（正文不使用加粗）
        line = re.sub(r'\*\*(.+?)\*\*', r'\1', line)

        # 所有行统一渲染为段落，不区分#标题
        # 这确保不会出现AI模板式的bold小标题
        parts.append(f"""<p style="margin:0 0 14px 0;font-size:15px;color:{s['text']};line-height:1.88;">{line}</p>""")

    parts.append(f"""<p style="margin:30px 0 10px 0;text-align:center;color:{s['accent']};font-size:14px;">想看完整买量数据？回复关键词获取更多~</p>""")
    parts.append(f"""<p style="margin:20px 0 0 0;text-align:center;color:#bbb;font-size:11px;border-top:1px solid #eee;padding-top:14px;">广大大 | 出海买量数据专家</p>""")
    parts.append("</section>")

    return "\n".join(parts)


def extract_title_digest(text, pillar_name, date_str):
    """从文章中提取标题和摘要。格式：第一行是标题，空行后是正文"""
    lines = text.strip().split("\n")

    title_line = ""
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped:
            title_line = stripped
            body_start = i + 1
            break

    # 清洗标题：去掉markdown的#号前缀
    title_line = re.sub(r'^#+\s*', '', title_line).strip()
    # 去掉支柱名称前缀（如"竞品拆解台："）
    pillar_prefixes = ["竞品拆解台", "方法论实战", "数据月报", "客户故事", "互动参与"]
    for prefix in pillar_prefixes:
        if title_line.startswith(prefix):
            title_line = title_line[len(prefix):].lstrip("：: -—").strip()

    ok, reason = check_title_quality(title_line)
    if not ok:
        log.warning(f"标题问题: {reason}，使用备用标题")
        for line in lines[body_start:]:
            stripped = line.strip()
            if stripped and len(stripped) > 5:
                stripped = re.sub(r'^#+\s*', '', stripped).strip()
                title_line = stripped[:25].rstrip("~") + "~"
                break
        else:
            title_line = f"出海买量观察 {date_str}"

    title = title_line[:30].rstrip("~")
    if not title:
        title = f"出海买量观察 {date_str}"

    body_lines = lines[body_start:]
    body_text = " ".join(l.strip() for l in body_lines if l.strip())
    body_text = body_text.replace("~", "").strip()
    digest = body_text[:90].rstrip() + "~"

    return title, digest


# ============================================================
# 飞书通知
# ============================================================
def send_feishu_webhook(results, date_str, errors=None):
    """通过飞书群机器人 webhook 发送推送通知
    
    注意：飞书webhook关键词是"公众号"，所有消息必须包含。
    """
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if not webhook_url:
        log.info("FEISHU_WEBHOOK_URL 未设置，跳过飞书通知")
        return

    success_list = [(k, v) for k, v in results.items()]
    fail_list = errors or []

    if success_list and not fail_list:
        titles = "\n".join([f"{i+1}. {v['title']}" for i, (k, v) in enumerate(success_list)])
        text = f"公众号推送完成({len(success_list)}篇) | {date_str}\n{titles}\n前往草稿箱审核: https://mp.weixin.qq.com"
    elif not success_list and fail_list:
        text = f"公众号推送全部失败({len(fail_list)}篇) | {date_str}\n" + "\n".join(fail_list)
    else:
        ok_titles = "\n".join([f"{i+1}. {v['title']}" for i, (k, v) in enumerate(success_list)])
        fail_text = "\n".join(fail_list)
        text = f"公众号推送成功({len(success_list)}篇) | {date_str}\n{ok_titles}\n\n失败({len(fail_list)}篇):\n{fail_text}"

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
            log.info(f"飞书通知已发送: {body}")
    except Exception as e:
        log.error(f"飞书通知发送失败: {e}")


# ============================================================
# 发布
# ============================================================
def publish_article(token, pillar_key, track_key, date_str, cover_data, output_dir):
    pillar = PILLARS[pillar_key]
    pillar_name = pillar["name"]
    log.info(f"\n{'='*40}\n{pillar_name} | {date_str}\n{'='*40}")

    # 生成文章
    article = generate_article(pillar_key, track_key, date_str)
    title, digest = extract_title_digest(article, pillar_name, date_str)
    log.info(f"标题: {title}")

    # 去掉标题行，只保留正文
    lines = article.strip().split("\n")
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip():
            body_start = i + 1
            break
    body_text = "\n".join(lines[body_start:]).strip()

    # 封面
    cover_style = track_key or pillar_key
    cover_path = os.path.join(output_dir, f"cover_{cover_style}.png")
    with open(cover_path, "wb") as f:
        f.write(cover_data)

    # HTML
    html = markdown_to_html(body_text, cover_style)

    clean, banned = check_no_competitor(html)
    if not clean:
        log.error(f"HTML中还有竞品: {banned}，跳过")
        return None, None

    # 保存文件
    html_path = os.path.join(output_dir, f"draft_{pillar_key}.html")
    md_path = os.path.join(output_dir, f"draft_{pillar_key}.md")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(article)

    # 推送
    thumb_id = wechat_upload_cover(token, cover_path)
    html_clean = re.sub(r">\s+<", "><", html.replace("\n", " ").strip())
    draft_id = wechat_create_draft(token, title, digest, html_clean, thumb_id)
    log.info(f"草稿ID: {draft_id}")

    return draft_id, {"title": title, "digest": digest}


def already_published_today(date_str):
    """检查今天是否已经有成功的 workflow 运行"""
    gh_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if not gh_token:
        log.info("无 GH_TOKEN，跳过去重检查")
        return False

    try:
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
        log.warning(f"去重检查失败（不影响执行）: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--pillar", choices=["competitor", "methodology", "monthly_report", "customer_story"],
                        help="手动指定内容支柱（默认按日期自动）")
    parser.add_argument("--track", choices=["game", "tool", "drama"], help="赛道（仅竞品拆解台）")
    parser.add_argument("--output", default="/tmp/gzh_output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="忽略去重检查，强制推送")
    args = parser.parse_args()

    date_str = args.date
    output_dir = args.output
    os.makedirs(output_dir, exist_ok=True)

    # 确定内容支柱
    if args.pillar:
        pillar_key = args.pillar
        track_key = args.track
        if pillar_key == "competitor" and not track_key:
            track_key = get_track_rotation(date_str)
    else:
        pillar_key, track_key = get_daily_pillar(date_str)
        if pillar_key is None:
            log.info(f"{date_str} 不是推送日（周二/周四），退出")
            print(json.dumps({"skipped": True, "reason": "not a publishing day", "date": date_str}))
            return

    pillar_name = PILLARS[pillar_key]["name"]
    track_info = f" | 赛道: {TRACKS[track_key]['name']}" if track_key else ""
    log.info(f"广大大云端发布 | {date_str} | 支柱: {pillar_name}{track_info}")
    log.info(f"模型: DeepSeek-V4 Flash ({DEEPSEEK_MODEL})")

    # 去重检查
    if not args.force and not args.dry_run:
        if already_published_today(date_str):
            log.info("今天已推送，退出")
            print(json.dumps({"skipped": True, "reason": f"{date_str} already published", "date": date_str}))
            return

    # 生成封面
    cover_style = track_key or pillar_key
    cover_data = generate_cover(cover_style)

    token = None if args.dry_run else wechat_get_token()

    results = {}
    try:
        did, info = publish_article(token, pillar_key, track_key, date_str, cover_data, output_dir)
        if did:
            results[pillar_key] = {"draft_id": did, **info}
    except Exception as e:
        log.error(f"{pillar_name} 失败: {e}")
        import traceback
        traceback.print_exc()

    log.info(f"\n{'='*50}")
    for pk, info in results.items():
        log.info(f"  {PILLARS[pk]['name']}: {info['title']}")
        log.info(f"    草稿ID: {info['draft_id']}")
    log.info(f"审核发布: https://mp.weixin.qq.com -> 草稿箱")

    print(json.dumps({k: {"draft_id": v["draft_id"], "title": v["title"]} for k, v in results.items()}, ensure_ascii=False))

    # 飞书通知
    if not args.dry_run:
        send_feishu_webhook(results, date_str)


if __name__ == "__main__":
    main()
