#!/usr/bin/env python3
"""
云端每日发布脚本 — 运行在 GitHub Actions 上，完全不依赖本地机器

触发方式：
  GitHub Actions cron: 工作日 UTC 1:00 (= 北京时间 9:00)

环境变量（通过 GitHub Secrets 注入）：
  OPENAI_API_KEY  — OpenAI API 密钥（用于内容生成 + 封面图生成）
  WEIXIN_APPID    — 广大大公众号 AppID
  WEIXIN_SECRET   — 广大大公众号 AppSecret

用法：
  python3 scripts/cloud_daily_publish.py [--date 2026-07-03]

输出：
  - 3 篇 HTML 文章 + 3 张封面图 → 推送到广大大公众号草稿箱
  - 每篇推文草稿 ID 打印到 stdout
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
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============================================================
# 配置
# ============================================================
WEIXIN_APPID = os.environ.get("WEIXIN_APPID", "wx94eb6ba27c82a203")
WEIXIN_SECRET = os.environ.get("WEIXIN_SECRET", "")
WEIXIN_NAME = "广大大 SocialPeta"
AUTHOR = "zylon"

# 三大赛道配置
TRACKS = {
    "game": {
        "name": "游戏",
        "style": "dark-dashboard",
        "search_keywords": "2026出海手游 SLG 休闲游戏 买量 投放 最新数据",
        "data_sources": "广大大数据库、gamelook、白鲸出海、10100.com、公网新闻",
    },
    "tool": {
        "name": "工具",
        "style": "clean-list",
        "search_keywords": "2026出海工具类App 清理 壁纸 AI助手 买量 下载量 最新数据",
        "data_sources": "广大大数据库、白鲸出海、扬帆出海、10100.com、公网新闻",
    },
    "drama": {
        "name": "短剧",
        "style": "magazine-narrative",
        "search_keywords": "2026出海短剧 AI短剧 ReelShort DramaBox 买量 投放 排行榜 最新数据",
        "data_sources": "广大大数据库、扬帆出海、10100.com、公网新闻、App Store排行",
    },
}

# ============================================================
# 竞品黑名单（绝对禁止在文章中出现！出现=致命事故）
# ============================================================
BANNED_COMPETITORS = [
    "热云", "XMP", "Insightrackr", "insightrackr", "Mintegral", "mintegral",
    "有米云", "AppGrowing", "Appark",
    "DataEye", "dataeye", "ADX",
    "SensorTower", "sensortower",
    "橙果数杭", "独立出海联合体", "游戏茶馆", "AntGlobal", "游戏陀螺",
]

# 安全数据源白名单
SAFE_SOURCES = [
    "广大大", "SocialPeta", "10100.com", "大数跨境",
    "虎嗅", "36氪", "扬帆出海", "白鲸出海", "gamelook",
    "腾讯新闻", "搜狐", "网易", "新浪",
    "App Store", "Google Play", "上市公司财报",
]


def check_no_competitor(text):
    """扫描文本，确保没有任何竞品名"""
    for banned in BANNED_COMPETITORS:
        if banned.lower() in text.lower():
            log.warning(f"⚠️ 发现竞品名: {banned}")
            return False, banned
    return True, None


# ============================================================
# 微信 API（从 push_ggd.py 移植，适配云端环境）
# ============================================================
def wechat_get_token():
    """获取 access_token（云端版，不验证 IP callback）"""
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={WEIXIN_APPID}&secret={WEIXIN_SECRET}"
    result = subprocess.run(
        ["curl", "-s", url],
        capture_output=True, text=True, timeout=30
    )
    data = json.loads(result.stdout)

    if "errmsg" in data and data.get("errcode", 0) != 0:
        raise RuntimeError(f"获取 token 失败: {data.get('errmsg')}")

    token = data.get("access_token", "")
    if not token:
        raise RuntimeError("access_token 为空")

    log.info(f"✅ Token 获取成功 (AppID: {WEIXIN_APPID})")
    return token


def wechat_upload_cover(token, image_path):
    """上传封面图到微信素材库"""
    if not image_path or not os.path.exists(image_path):
        log.warning("封面图不存在，跳过")
        return None

    url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={token}&type=image"
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", url, "-F", f"media=@{image_path}"],
        capture_output=True, text=True, timeout=60
    )
    data = json.loads(result.stdout)
    media_id = data.get("media_id", "")

    if not media_id:
        log.warning(f"封面上传失败: {data}")
        return None

    log.info(f"✅ 封面上传成功: {media_id}")
    return media_id


def wechat_create_draft(token, title, digest, content_html, thumb_media_id=None):
    """创建草稿并推送到微信公众号草稿箱"""
    articles = [{
        "title": title,
        "author": AUTHOR,
        "digest": digest,
        "content": content_html,
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    }]
    if thumb_media_id:
        articles[0]["thumb_media_id"] = thumb_media_id

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"articles": articles}, f, ensure_ascii=False)
        json_path = f.name

    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", url, "-H", "Content-Type: application/json", "-d", f"@{json_path}"],
        capture_output=True, text=True, timeout=30
    )
    os.unlink(json_path)

    data = json.loads(result.stdout)
    media_id = data.get("media_id", "")

    if not media_id:
        errcode = data.get("errcode", "?")
        errmsg = data.get("errmsg", "unknown")
        raise RuntimeError(f"创建草稿失败 [{errcode}]: {errmsg}")

    return media_id


# ============================================================
# AI 内容生成（OpenAI API）
# ============================================================
def get_openai_client():
    """获取 OpenAI 客户端"""
    from openai import OpenAI
    return OpenAI()


def build_system_prompt():
    """构建完整的系统提示词（包含所有写作规则）"""
    return f"""你是广大大(SocialPeta)公众号的文案写手。广大大是出海广告买量素材分析+竞品策略+市场大盘分析工具。

## 写作铁律（必须严格遵守）

### 格式规则
1. 句号必须用~代替（不要用。！）
2. 禁止套任何写作框架（不要开头场景代入/结尾总结互动/首先其次最后/第一第二第三）
3. 禁止AI词汇：此外/值得注意的是/毋庸置疑/由此可见/总的来说/综上所述
4. 禁止虚假强调：破折号——/不仅……而且……/硬凑三点
5. 禁止空洞漂亮话：不上价值/不展望未来
6. 禁止套话开头："在当今……""随着……的发展""近年来……"
7. 说话像真人在微信聊天：可以废话/跑题/吐槽/口语/不确定
8. 正文禁止emoji
9. 字数：不低于800字，有话则长无话则短

### 数据规则（最高优先级）
- 数据必须是真实可查的，不确定的数字写范围或趋势描述
- 标注数据来源时只能用非竞品来源：{', '.join(SAFE_SOURCES)}
- 拿不到具体数字就写范围或趋势描述，不要硬造一个精确数字

### 竞品黑名单（绝对禁止，出现=致命事故）
以下品牌/产品名禁止以任何形式出现在文章中：
{', '.join(BANNED_COMPETITORS)}

### 文章结构
- 每篇必须有CTA引导：回复关键词XXX获取资料
- 内容核心：用广大大数据"量化拆解"具体产品的买量策略

### 排版外观
- 输出纯文本 Markdown 格式
- 用自然分段，不要编号列表
- 可以有小标题但不要用数字编号
"""


def build_track_prompt(track_key, date_str):
    """为特定赛道构建用户提示词"""
    track = TRACKS[track_key]

    style_guide = {
        "game": """游戏赛道：暗色数据仪表盘风格
- 用大数字卡片展示核心数据（下载量/收入/排名）
- 用表格展示产品对比
- 用排名列表展示TOP产品
- 关键数字加粗或突出显示
- 像一个数据分析师在解读报表""",

        "tool": """工具赛道：效率快读清单风格
- 用分类卡片展示不同工具品类
- 用简洁的列表展示关键数据
- 用对比说明变现模式差异
- 像一个懂行的朋友在分享行业观察""",

        "drama": """短剧赛道：杂志深度报道风格
- 用时间线展示产品发展脉络
- 用对比展示不同产品的买量策略差异
- 用引用块突出关键发现
- 像一个资深编辑在讲述行业故事""",
    }

    return f"""今天是{date_str}，写一篇关于出海{track['name']}买量的公众号文章。

赛道要求：
{style_guide.get(track_key, '')}

数据搜索关键词：{track['search_keywords']}
可用数据来源：{track['data_sources']}

要求：
1. 基于搜索结果中的真实数据撰写
2. 重点分析1-2个具体产品的买量策略
3. 用广大大的视角"量化拆解"这些产品的投放策略
4. 数据标注来源（只用安全来源）
5. 结尾引导：回复【关键词】获取更多数据
6. 正文禁止任何emoji
7. 句号用~代替
8. 绝对禁止出现任何竞品品牌名

请直接输出文章纯文本，不要加任何说明或注释。"""


def generate_article(track_key, date_str):
    """使用 OpenAI 生成一篇文章"""
    client = get_openai_client()

    system_prompt = build_system_prompt()
    user_prompt = build_track_prompt(track_key, date_str)

    log.info(f"🤖 开始生成 {TRACKS[track_key]['name']} 赛道文章...")

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.85,
        max_tokens=4000,
    )

    article_text = response.choices[0].message.content

    # 检查竞品名
    clean, banned = check_no_competitor(article_text)
    if not clean:
        log.error(f"❌ 文章中发现竞品名: {banned}，重新生成...")
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": article_text},
                {"role": "user", "content": f"上面文章里出现了竞品名「{banned}」，这是绝对禁止的。请重新生成，移除所有竞品引用，替换为安全数据源。"},
            ],
            temperature=0.85,
            max_tokens=4000,
        )
        article_text = response.choices[0].message.content

    log.info(f"✅ {TRACKS[track_key]['name']} 文章生成完成，{len(article_text)} 字符")
    return article_text


def generate_cover(track_key, article_title):
    """使用 DALL-E 生成封面图"""
    client = get_openai_client()

    cover_prompts = {
        "game": f"A dramatic dark battlefield scene with floating holographic data panels, deep red and gold color scheme, abstract gaming elements, no text or watermarks, professional editorial style, 1792x1024",
        "tool": f"A clean modern workspace with floating productivity icons and data visualizations, teal and gray color scheme, abstract app interface elements, no text or watermarks, minimalist editorial style, 1792x1024",
        "drama": f"A cinematic film strip with neural network nodes glowing, purple and amber gradients, dramatic lighting, abstract storytelling elements, no text or watermarks, magazine editorial style, 1792x1024",
    }

    prompt = cover_prompts.get(track_key, cover_prompts["game"])

    log.info(f"🎨 生成 {TRACKS[track_key]['name']} 封面图...")
    response = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1792x1024",
        quality="standard",
        n=1,
    )

    image_url = response.data[0].url
    log.info(f"✅ 封面图生成成功")

    # 下载图片
    import requests as req
    image_data = req.get(image_url).content
    return image_data


def crop_cover(image_data, output_path):
    """中心裁剪为 900x383（2.35:1），仿照 crop_cover.py 逻辑"""
    from PIL import Image

    img = Image.open(BytesIO(image_data))

    # 计算中心裁剪区域
    target_ratio = 900 / 383  # ≈ 2.35
    w, h = img.size
    current_ratio = w / h

    if current_ratio > target_ratio:
        # 图片太宽，裁两边
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        # 图片太高，裁上下
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))

    img = img.resize((900, 383), Image.LANCZOS)
    img.save(output_path, "PNG")
    log.info(f"✅ 封面图裁剪完成: {output_path}")


def markdown_to_html(md_text, track_key):
    """将 Markdown 文本转换为公众号 HTML（带赛道专属样式）"""
    # 基础 HTML 框架
    style_configs = {
        "game": {
            "bg": "#1a1a2e",
            "text": "#e0e0e0",
            "accent": "#dc2626",
            "accent2": "#fbbf24",
            "card_bg": "#16213e",
            "table_bg": "#1a1a2e",
            "table_header": "#dc2626",
            "border": "#333",
        },
        "tool": {
            "bg": "#ffffff",
            "text": "#333333",
            "accent": "#0d9488",
            "accent2": "#14b8a6",
            "card_bg": "#f0fdf4",
            "table_bg": "#ffffff",
            "table_header": "#0d9488",
            "border": "#e0e0e0",
        },
        "drama": {
            "bg": "#faf9f7",
            "text": "#333333",
            "accent": "#7c3aed",
            "accent2": "#f59e0b",
            "card_bg": "#fef3c7",
            "table_bg": "#faf9f7",
            "table_header": "#7c3aed",
            "border": "#e0d5c8",
        },
    }

    style = style_configs.get(track_key, style_configs["tool"])

    # 将 ~ 换回句号用于结构解析（文章用~但HTML不应影响渲染，保留~即可）
    # 简单的 Markdown → HTML 转换
    lines = md_text.strip().split("\n")
    html_parts = []

    # 开篇
    html_parts.append(f"""<section style="padding:12px 10px;font-size:15px;line-height:1.88;color:{style['text']};word-break:break-all;font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;background:{style['bg']};">""")

    # 关注引导条
    html_parts.append(f"""<section style="text-align:center;margin-bottom:24px;padding:14px 0;border-bottom:1px solid {style['border']};"><p style="color:#999;font-size:12px;letter-spacing:1px;margin:0;">点击上方蓝字关注，每周解锁出海买量新玩法</p></section>""")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 标题行（## 或 ### 开头）
        if line.startswith("### "):
            text = line[4:]
            html_parts.append(f"""<section style="margin:22px 0 12px 0;padding:10px 0;border-left:4px solid {style['accent']};padding-left:14px;"><p style="margin:0;font-size:17px;font-weight:bold;color:{style['accent']};">{text}</p></section>""")
        elif line.startswith("## "):
            text = line[3:]
            html_parts.append(f"""<section style="margin:28px 0 14px 0;text-align:center;"><p style="margin:0;font-size:20px;font-weight:bold;color:{style['accent']};border-bottom:2px solid {style['accent']};display:inline-block;padding-bottom:6px;">{text}</p></section>""")
        # 数字/数据行（包含 > 数字格式的）
        elif "万" in line or "亿" in line or "$" in line or "%" in line:
            html_parts.append(f"""<p style="margin:0 0 18px 0;font-size:15px;color:{style['text']};line-height:1.9;"><strong>{line}</strong></p>""")
        elif line.startswith("- "):
            text = line[2:]
            html_parts.append(f"""<p style="margin:0 0 12px 0;padding-left:14px;font-size:14px;color:{style['text']};line-height:1.8;border-left:3px solid {style['accent']};">{text}</p>""")
        else:
            # 普通段落
            html_parts.append(f"""<p style="margin:0 0 18px 0;font-size:15px;color:{style['text']};line-height:1.9;">{line}</p>""")

    # 结尾 CTA + 品牌条
    html_parts.append(f"""<section style="margin:30px 0;text-align:center;background:{style['card_bg']};border-radius:12px;padding:20px 18px;border:1px dashed {style['accent']};"><p style="margin:0 0 8px 0;font-size:14px;font-weight:bold;color:{style['accent']};">想看广大大完整买量数据？</p><p style="margin:0;font-size:14px;color:{style['text']};">回复关键词获取更多数据~</p></section>""")

    html_parts.append(f"""<section style="margin:20px 0 10px 0;text-align:center;padding-top:14px;border-top:1px solid {style['border']};"><p style="margin:0;font-size:11px;color:#999;">广大大 | 出海买量数据专家</p></section>""")

    html_parts.append("</section>")

    return "\n".join(html_parts)


def extract_title_digest(article_text, track_name, date_str):
    """从文章中提取或生成标题和摘要"""
    lines = article_text.strip().split("\n")
    # 取第一行非空行作为标题候选
    first_line = ""
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            first_line = line
            break

    # 生成标题（最多64字节/约21个中文字符）
    if len(first_line) > 40:
        title = first_line[:40].rstrip("~") + "..."
    elif first_line:
        title = first_line[:40].rstrip("~")
    else:
        title = f"{track_name}出海买量新观察 | {date_str}"

    # 生成摘要（约50字）
    preview = article_text[:120].replace("\n", " ").replace("~", "").strip()
    digest = preview[:90].rstrip() + "~" if len(preview) > 90 else preview + "~"

    return title, digest


# ============================================================
# 主流程
# ============================================================
def publish_track(token, track_key, date_str, output_dir):
    """生成并发布一个赛道的文章"""
    track_name = TRACKS[track_key]["name"]
    log.info(f"\n{'='*50}")
    log.info(f"📝 开始处理 {track_name} 赛道")
    log.info(f"{'='*50}")

    # 1. 生成文章
    article_text = generate_article(track_key, date_str)

    # 2. 提取标题和摘要
    title, digest = extract_title_digest(article_text, track_name, date_str)
    log.info(f"📌 标题: {title}")

    # 3. 生成封面图
    cover_data = None
    try:
        cover_data = generate_cover(track_key, title)
    except Exception as e:
        log.warning(f"封面图生成失败: {e}，将跳过封面")

    # 4. 裁剪封面图
    cover_path = os.path.join(output_dir, f"cover_{track_key}_{date_str.replace('-', '')}.png")
    if cover_data:
        try:
            crop_cover(cover_data, cover_path)
        except Exception as e:
            log.warning(f"封面裁剪失败: {e}")
            cover_path = None
    else:
        cover_path = None

    # 5. 转换为 HTML
    html_content = markdown_to_html(article_text, track_key)

    # 6. 再次检查竞品名
    clean, banned = check_no_competitor(html_content)
    if not clean:
        log.error(f"❌ HTML中发现竞品名: {banned}，跳过发布！")
        return None, None

    # 7. 保存文件
    html_path = os.path.join(output_dir, f"draft_{date_str.replace('-', '')}_{track_key}.html")
    md_path = os.path.join(output_dir, f"draft_{date_str.replace('-', '')}_{track_key}.md")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(article_text)

    # 8. 上传封面
    thumb_id = None
    if cover_path and os.path.exists(cover_path):
        thumb_id = wechat_upload_cover(token, cover_path)

    # 9. 推送到草稿箱
    # 清洗 HTML：去换行
    html_clean = html_content.replace("\n", " ")
    html_clean = html_clean.replace("\r", " ")
    html_clean = re.sub(r">\s+<", "><", html_clean)
    html_clean = html_clean.strip()

    draft_id = wechat_create_draft(token, title, digest, html_clean, thumb_id)
    log.info(f"✅ {track_name} 草稿推送成功！ID: {draft_id}")

    return draft_id, {
        "title": title,
        "digest": digest,
        "html_path": html_path,
        "md_path": md_path,
        "cover_path": cover_path,
    }


def main():
    parser = argparse.ArgumentParser(description="广大大公众号云端每日发布")
    parser.add_argument("--date", help="日期 YYYY-MM-DD，默认今天", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--tracks", nargs="+", choices=["game", "tool", "drama"], default=["game", "tool", "drama"])
    parser.add_argument("--output", default="/tmp/gzh_output")
    parser.add_argument("--dry-run", action="store_true", help="只生成不推送")
    args = parser.parse_args()

    date_str = args.date
    output_dir = args.output
    os.makedirs(output_dir, exist_ok=True)

    log.info(f"🚀 广大大公众号云端发布启动")
    log.info(f"📅 日期: {date_str}")
    log.info(f"📂 输出: {output_dir}")
    log.info(f"🏷️  赛道: {', '.join(args.tracks)}")

    # 获取微信 token
    token = None
    if not args.dry_run:
        token = wechat_get_token()

    results = {}
    for track_key in args.tracks:
        try:
            draft_id, info = publish_track(token, track_key, date_str, output_dir)
            if draft_id:
                results[track_key] = {"draft_id": draft_id, **info}
        except Exception as e:
            log.error(f"❌ {TRACKS[track_key]['name']} 赛道处理失败: {e}")
            import traceback
            traceback.print_exc()

    # 汇总
    log.info(f"\n{'='*60}")
    log.info(f"📊 发布完成")
    log.info(f"{'='*60}")
    for track_key, info in results.items():
        log.info(f"  {TRACKS[track_key]['name']}: {info['title']}")
        log.info(f"    草稿ID: {info['draft_id']}")
    log.info(f"📌 前往后台审核: https://mp.weixin.qq.com → 内容管理 → 草稿箱")

    # 输出 JSON 结果供 GitHub Actions 使用
    print(json.dumps({k: {"draft_id": v["draft_id"], "title": v["title"]} for k, v in results.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
