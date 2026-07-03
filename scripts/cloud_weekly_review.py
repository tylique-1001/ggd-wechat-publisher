#!/usr/bin/env python3
"""
云端每周复盘脚本 — 运行在 GitHub Actions 上

触发方式：
  GitHub Actions cron: 每周一 UTC 2:00 (= 北京时间 10:00)

环境变量：
  GEMINI_API_KEY  — Google Gemini API 密钥（免费额度，用于生成复盘报告）
  WEIXIN_APPID    — 广大大公众号 AppID
  WEIXIN_SECRET   — 广大大公众号 AppSecret

数据来源：
  - 单篇数据：/datacube/getarticlesummary
  - 汇总数据：/datacube/getarticletotal
  - 用户阅读：/datacube/getuserread
  - 用户分享：/datacube/getusershare
"""

import os
import sys
import json
import logging
import subprocess
import tempfile
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

WEIXIN_APPID = os.environ.get("WEIXIN_APPID", "wx94eb6ba27c82a203")
WEIXIN_SECRET = os.environ.get("WEIXIN_SECRET", "")

GEMINI_MODEL = "gemini-2.0-flash"

# 三大赛道关键词（用于自动分类文章）
TRACK_KEYWORDS = {
    "游戏": ["游戏", "SLG", "休闲", "RPG", "模拟", "卡牌", "MMORPG", "手游", "Last War", "王者荣耀", "Arrows", "Puzzle"],
    "工具": ["工具", "清理", "壁纸", "AI助手", "识别", "遥控", "效率", "Cleanup", "Dola", "Cal AI", "扫描"],
    "短剧": ["短剧", "AI短剧", "ReelShort", "DramaBox", "VibeShort", "ShortMax", "剧场", "剧目", "漫剧"],
}


def get_token():
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={WEIXIN_APPID}&secret={WEIXIN_SECRET}"
    result = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=30)
    data = json.loads(result.stdout)
    token = data.get("access_token", "")
    if not token:
        raise RuntimeError(f"获取 token 失败: {data}")
    log.info(f"✅ Token 获取成功")
    return token


def api_post(token, endpoint, payload):
    """通用 POST 请求"""
    url = f"https://api.weixin.qq.com{endpoint}?access_token={token}"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        tmp = f.name
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", url, "-H", "Content-Type: application/json", "-d", f"@{tmp}"],
        capture_output=True, text=True, timeout=30
    )
    os.unlink(tmp)
    data = json.loads(result.stdout)
    if "errcode" in data and data["errcode"] != 0:
        log.warning(f"API 调用失败 {endpoint}: {data.get('errmsg', 'unknown')}")
        return None
    return data


def get_date_range():
    """获取上周日期范围 (周一-周日)"""
    today = datetime.now()
    days_since_sunday = today.weekday() + 1  # Monday=0 -> 1
    last_sunday = today - timedelta(days=days_since_sunday)
    last_monday = last_sunday - timedelta(days=6)
    return last_monday.strftime("%Y-%m-%d"), last_sunday.strftime("%Y-%m-%d")


def classify_track(title):
    """根据标题关键词自动归类"""
    for track, keywords in TRACK_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in title.lower():
                return track
    return "其他"


def fetch_weekly_data(token):
    """拉取上周数据"""
    begin, end = get_date_range()
    log.info(f"📊 数据范围: {begin} ~ {end}")
    results = {"period": f"{begin} ~ {end}"}

    begin_fmt = begin.replace("-", "")
    end_fmt = end.replace("-", "")

    # 单篇文章数据
    summary = api_post(token, "/datacube/getarticlesummary", {
        "begin_date": begin_fmt,
        "end_date": end_fmt,
    })
    if summary and "list" in summary:
        results["articles"] = summary["list"]
        log.info(f"📰 获取到 {len(summary['list'])} 篇文章数据")

    # 文章汇总
    total = api_post(token, "/datacube/getarticletotal", {
        "begin_date": begin_fmt,
        "end_date": end_fmt,
    })
    if total and "list" in total:
        results["total"] = total["list"]

    # 用户阅读
    user_read = api_post(token, "/datacube/getuserread", {
        "begin_date": begin_fmt,
        "end_date": end_fmt,
    })
    if user_read and "list" in user_read:
        results["user_read"] = user_read["list"]

    # 用户分享
    user_share = api_post(token, "/datacube/getusershare", {
        "begin_date": begin_fmt,
        "end_date": end_fmt,
    })
    if user_share and "list" in user_share:
        results["user_share"] = user_share["list"]

    return results


def analyze_data(results):
    """分析数据，生成复盘报告"""
    report_lines = []
    report_lines.append("# 广大大公众号 每周数据复盘")
    report_lines.append(f"## 📅 统计周期: {results['period']}")
    report_lines.append("")

    articles = results.get("articles", [])
    total_data = results.get("total", [])
    user_read = results.get("user_read", [])
    user_share = results.get("user_share", [])

    if not articles:
        report_lines.append("⚠️ 上周没有发布文章或 datacube API 不可用~")
        report_lines.append("")
        report_lines.append("可能原因：")
        report_lines.append("- 账号类型不支持 datacube API（需认证服务号）")
        report_lines.append("- 上周没有发布文章")
        report_lines.append("- API 调用频率限制")
        return "\n".join(report_lines)

    # === 1. 汇总数据 ===
    report_lines.append("## 📈 整体数据")
    report_lines.append("")
    total_read = 0
    total_share = 0
    total_fav = 0
    daily_stats = []

    for day_data in total_data:
        ref_date = day_data.get("ref_date", "?")
        read = day_data.get("int_page_read_user", 0)
        share = day_data.get("share_user", 0)
        fav = day_data.get("add_to_fav_user", 0)
        msg_count = day_data.get("msg_count", 0)
        total_read += read
        total_share += share
        total_fav += fav
        daily_stats.append({"date": ref_date, "read": read, "share": share, "fav": fav, "msgs": msg_count})
        report_lines.append(f"- {ref_date}: 阅读 {read} | 分享 {share} | 收藏 {fav} | 推送 {msg_count} 篇")

    report_lines.append("")
    report_lines.append(f"**周总阅读**: {total_read} | **周总分享**: {total_share} | **周总收藏**: {total_fav}")
    report_lines.append("")

    # === 2. 单篇文章分析 ===
    report_lines.append("## 📰 单篇文章表现")
    report_lines.append("")
    report_lines.append("| 日期 | 标题 | 赛道 | 阅读 | 分享 | 收藏 |")
    report_lines.append("|------|------|------|------|------|------|")

    track_stats = {"游戏": {"read": 0, "count": 0}, "工具": {"read": 0, "count": 0}, "短剧": {"read": 0, "count": 0}, "其他": {"read": 0, "count": 0}}

    sorted_articles = sorted(articles, key=lambda x: x.get("int_page_read_user", 0), reverse=True)

    for art in sorted_articles:
        title = art.get("title", "?")[:30]
        read = art.get("int_page_read_user", 0)
        share = art.get("share_user", 0)
        fav = art.get("add_to_fav_user", 0)
        track = classify_track(title)
        track_stats[track]["read"] += read
        track_stats[track]["count"] += 1
        report_lines.append(f"| {art.get('ref_date', '?')} | {title}... | {track} | {read} | {share} | {fav} |")

    report_lines.append("")

    # === 3. 赛道分析 ===
    report_lines.append("## 🎯 赛道表现")
    report_lines.append("")
    report_lines.append("| 赛道 | 文章数 | 总阅读 | 篇均阅读 |")
    report_lines.append("|------|--------|--------|----------|")
    for track, stats in track_stats.items():
        if stats["count"] > 0:
            avg = stats["read"] / stats["count"]
            report_lines.append(f"| {track} | {stats['count']} | {stats['read']} | {avg:.0f} |")
    report_lines.append("")

    # === 4. 最佳/最差 ===
    if sorted_articles:
        report_lines.append("## 🏆 表现最佳")
        best = sorted_articles[0]
        report_lines.append(f"- **{best.get('title', '?')[:40]}**")
        report_lines.append(f"  - 阅读: {best.get('int_page_read_user', 0)}")
        report_lines.append(f"  - 分享: {best.get('share_user', 0)}")
        report_lines.append(f"  - 赛道: {classify_track(best.get('title', ''))}")
        report_lines.append("")

        if len(sorted_articles) > 1:
            report_lines.append("## ⚠️ 表现最弱")
            worst = sorted_articles[-1]
            report_lines.append(f"- **{worst.get('title', '?')[:40]}**")
            report_lines.append(f"  - 阅读: {worst.get('int_page_read_user', 0)}")
            report_lines.append(f"  - 分享: {worst.get('share_user', 0)}")
            report_lines.append(f"  - 赛道: {classify_track(worst.get('title', ''))}")
            report_lines.append("")

    # === 5. AI 驱动的复盘建议（使用 Gemini 免费额度）===
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        try:
            ai_report = generate_ai_analysis(articles, track_stats, total_read)
            report_lines.append("## 🤖 AI 复盘建议")
            report_lines.append("")
            report_lines.append(ai_report)
            report_lines.append("")
        except Exception as e:
            log.warning(f"AI 分析生成失败: {e}")
            report_lines.append("## 💡 下周建议（自动生成）")
            report_lines.append("")
            report_lines.extend(generate_basic_suggestions(track_stats, sorted_articles))
            report_lines.append("")
    else:
        report_lines.append("## 💡 下周建议（自动生成）")
        report_lines.append("")
        report_lines.extend(generate_basic_suggestions(track_stats, sorted_articles))
        report_lines.append("")

    report_lines.append("---")
    report_lines.append(f"*报告自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')} | 广大大公众号数据复盘系统*")

    return "\n".join(report_lines)


def generate_basic_suggestions(track_stats, sorted_articles):
    """生成基础建议（不依赖 AI）"""
    suggestions = []

    best_track = max(track_stats.items(), key=lambda x: x[1]["read"] / max(x[1]["count"], 1))
    worst_track = min(track_stats.items(), key=lambda x: x[1]["read"] / max(x[1]["count"], 1))

    suggestions.append(f"### 正面规律")
    suggestions.append(f"- 表现最好的赛道是 **{best_track[0]}**，篇均阅读 {best_track[1]['read'] / max(best_track[1]['count'], 1):.0f}")
    if sorted_articles:
        best = sorted_articles[0]
        suggestions.append(f"- 最高阅读文章: {best.get('title', '?')[:40]}（{best.get('int_page_read_user', 0)} 阅读）")
        suggestions.append(f"- 建议：下周重点复制该选题方向和标题风格")

    suggestions.append(f"")
    suggestions.append(f"### 负面问题")
    suggestions.append(f"- 表现最弱的赛道是 **{worst_track[0]}**，篇均阅读 {worst_track[1]['read'] / max(worst_track[1]['count'], 1):.0f}")
    if len(sorted_articles) > 1:
        worst = sorted_articles[-1]
        suggestions.append(f"- 最低阅读文章: {worst.get('title', '?')[:40]}（{worst.get('int_page_read_user', 0)} 阅读）")
    suggestions.append(f"- 建议：检查该赛道是否选题偏冷门，或标题吸引力不足")

    suggestions.append(f"")
    suggestions.append(f"### 下周选题调整")
    suggestions.append(f"- 增加 **{best_track[0]}** 赛道内容配比（表现好=读者爱看）")
    suggestions.append(f"- 减少或调整 **{worst_track[0]}** 赛道选题方向")
    suggestions.append(f"- 分享率高的文章→分析是否有争议性观点可复用")
    suggestions.append(f"- 收藏率高的文章→分析是否干货内容可系列化")

    return suggestions


def generate_ai_analysis(articles, track_stats, total_read):
    """使用 Gemini 生成深度分析"""
    import google.generativeai as genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 未设置")

    genai.configure(api_key=api_key)

    # 准备数据摘要
    article_summary = []
    for art in articles:
        article_summary.append({
            "title": art.get("title", "")[:40],
            "read": art.get("int_page_read_user", 0),
            "share": art.get("share_user", 0),
            "fav": art.get("add_to_fav_user", 0),
            "track": classify_track(art.get("title", "")),
        })

    track_summary = {}
    for track, stats in track_stats.items():
        if stats["count"] > 0:
            track_summary[track] = {
                "count": stats["count"],
                "total_read": stats["read"],
                "avg_read": stats["read"] / stats["count"],
            }

    prompt = f"""你是广大大公众号的运营分析师。以下是上周数据：

文章总阅读: {total_read}
赛道表现: {json.dumps(track_summary, ensure_ascii=False)}
单篇文章: {json.dumps(article_summary, ensure_ascii=False)}

请生成复盘建议，包括：
1. 正面规律（什么选题/标题/赛道表现好，为什么）
2. 负面问题（什么表现差，根因分析）
3. 下周选题和内容调整建议
4. 封面图/排版等视觉建议（如有观察）

请用口语化、直接的分析风格，不要套话。字数200-400字。"""

    model = genai.GenerativeModel(model_name=GEMINI_MODEL)
    response = model.generate_content(
        prompt,
        generation_config={"temperature": 0.8, "max_output_tokens": 1000},
    )
    return response.text


def save_report(report_text, output_dir="/tmp/gzh_output"):
    """保存复盘报告"""
    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, f"weekly_review_{datetime.now().strftime('%Y%m%d')}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    log.info(f"📄 报告已保存: {report_path}")
    return report_path


def main():
    log.info(f"🚀 广大大公众号每周复盘启动")
    log.info(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # 1. 获取 token
    token = get_token()

    # 2. 拉取数据
    results = fetch_weekly_data(token)

    # 3. 分析并生成报告
    report = analyze_data(results)

    # 4. 保存
    report_path = save_report(report)

    # 5. 输出到 stdout（供 GitHub Actions 日志查看）
    print(report)

    # 6. 输出 JSON 摘要
    articles = results.get("articles", [])
    total_read = sum(a.get("int_page_read_user", 0) for a in articles)
    print(f"\n📊 JSON 摘要: {json.dumps({'total_read': total_read, 'article_count': len(articles), 'report_path': report_path}, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
