#!/usr/bin/env python3
"""
云端每周复盘脚本 — GitHub Actions 运行

触发：每周一 UTC 2:00 (北京时间 10:00)

环境变量：
  SILICONFLOW_API_KEY — 硅基流动 API 密钥（国内永久免费）
  WEIXIN_APPID         — 广大大公众号 AppID
  WEIXIN_SECRET        — 广大大公众号 AppSecret
"""

import os
import json
import logging
import subprocess
import tempfile
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

WEIXIN_APPID = os.environ.get("WEIXIN_APPID", "wx94eb6ba27c82a203")
WEIXIN_SECRET = os.environ.get("WEIXIN_SECRET", "")

TRACK_KEYWORDS = {
    "游戏": ["游戏", "SLG", "休闲", "RPG", "模拟", "卡牌", "MMORPG", "手游", "Last War", "Arrows", "Puzzle"],
    "工具": ["工具", "清理", "壁纸", "AI助手", "识别", "遥控", "效率", "Cleanup", "Dola", "Cal AI", "扫描"],
    "短剧": ["短剧", "AI短剧", "ReelShort", "DramaBox", "VibeShort", "ShortMax", "剧场", "剧目", "漫剧"],
}


def get_token():
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={WEIXIN_APPID}&secret={WEIXIN_SECRET}"
    r = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=30)
    data = json.loads(r.stdout)
    token = data.get("access_token", "")
    if not token:
        raise RuntimeError(f"Token 失败: {data}")
    return token


def api_post(token, endpoint, payload):
    url = f"https://api.weixin.qq.com{endpoint}?access_token={token}"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        tmp = f.name
    r = subprocess.run(["curl", "-s", "-X", "POST", url, "-H", "Content-Type: application/json", "-d", f"@{tmp}"],
                       capture_output=True, text=True, timeout=30)
    os.unlink(tmp)
    data = json.loads(r.stdout)
    if data.get("errcode", 0) != 0:
        log.warning(f"API 失败 {endpoint}: {data.get('errmsg')}")
        return None
    return data


def get_date_range():
    today = datetime.now()
    last_sunday = today - timedelta(days=today.weekday() + 1)
    last_monday = last_sunday - timedelta(days=6)
    return last_monday.strftime("%Y-%m-%d"), last_sunday.strftime("%Y-%m-%d")


def classify_track(title):
    for track, keywords in TRACK_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in title.lower():
                return track
    return "其他"


def fetch_weekly_data(token):
    begin, end = get_date_range()
    log.info(f"📊 {begin} ~ {end}")
    results = {"period": f"{begin} ~ {end}"}
    b, e = begin.replace("-", ""), end.replace("-", "")

    for name, endpoint in [
        ("articles", "/datacube/getarticlesummary"),
        ("total", "/datacube/getarticletotal"),
        ("user_read", "/datacube/getuserread"),
        ("user_share", "/datacube/getusershare"),
    ]:
        data = api_post(token, endpoint, {"begin_date": b, "end_date": e})
        if data and "list" in data:
            results[name] = data["list"]

    return results


def analyze_data(results):
    report = []
    report.append("# 广大大公众号 每周数据复盘")
    report.append(f"## 统计周期: {results['period']}\n")

    articles = results.get("articles", [])
    total_data = results.get("total", [])
    if not articles:
        report.append("⚠️ 上周没有发布文章或 datacube API 不可用~\n可能原因：账号类型不支持（需认证服务号）/ 上周未发布 / API频率限制")
        return "\n".join(report)

    # 汇总
    report.append("## 整体数据\n")
    total_read = total_share = total_fav = 0
    for day in total_data:
        r, s, f = day.get("int_page_read_user", 0), day.get("share_user", 0), day.get("add_to_fav_user", 0)
        total_read += r; total_share += s; total_fav += f
        report.append(f"- {day.get('ref_date', '?')}: 阅读 {r} | 分享 {s} | 收藏 {f} | 推送 {day.get('msg_count', 0)} 篇")
    report.append(f"\n**周总阅读**: {total_read} | **周总分享**: {total_share} | **周总收藏**: {total_fav}\n")

    # 单篇
    report.append("## 单篇文章表现\n")
    report.append("| 日期 | 标题 | 赛道 | 阅读 | 分享 | 收藏 |")
    report.append("|------|------|------|------|------|------|")
    track_stats = {"游戏": {"read": 0, "count": 0}, "工具": {"read": 0, "count": 0}, "短剧": {"read": 0, "count": 0}, "其他": {"read": 0, "count": 0}}
    sorted_arts = sorted(articles, key=lambda x: x.get("int_page_read_user", 0), reverse=True)

    for art in sorted_arts:
        title = art.get("title", "?")[:30]
        r, s, f = art.get("int_page_read_user", 0), art.get("share_user", 0), art.get("add_to_fav_user", 0)
        track = classify_track(title)
        track_stats[track]["read"] += r; track_stats[track]["count"] += 1
        report.append(f"| {art.get('ref_date', '?')} | {title}... | {track} | {r} | {s} | {f} |")

    # 赛道
    report.append("\n## 赛道表现\n")
    report.append("| 赛道 | 文章数 | 总阅读 | 篇均 |")
    report.append("|------|--------|--------|------|")
    for tk, st in track_stats.items():
        if st["count"] > 0:
            report.append(f"| {tk} | {st['count']} | {st['read']} | {st['read'] / st['count']:.0f} |")

    # 最佳/最弱
    if sorted_arts:
        best, worst = sorted_arts[0], sorted_arts[-1]
        report.append(f"\n## 表现最佳: {best.get('title', '?')[:40]}（{best.get('int_page_read_user', 0)} 阅读）")
        report.append(f"\n## 表现最弱: {worst.get('title', '?')[:40]}（{worst.get('int_page_read_user', 0)} 阅读）")

    # AI 分析（硅基流动）
    report.append("\n## AI 复盘建议\n")
    api_key = os.environ.get("SILICONFLOW_API_KEY")
    if api_key:
        try:
            ai_text = generate_ai_analysis(articles, track_stats, total_read)
            report.append(ai_text)
        except Exception as e:
            log.warning(f"AI 分析失败: {e}")
            report.extend(basic_suggestions(track_stats, sorted_arts))
    else:
        report.extend(basic_suggestions(track_stats, sorted_arts))

    report.append("\n---")
    report.append(f"*报告生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    return "\n".join(report)


def basic_suggestions(track_stats, sorted_arts):
    s = []
    best_tk = max(track_stats.items(), key=lambda x: x[1]["read"] / max(x[1]["count"], 1))
    worst_tk = min(track_stats.items(), key=lambda x: x[1]["read"] / max(x[1]["count"], 1))
    s.append("\n### 正面规律")
    s.append(f"- 表现最好赛道: {best_tk[0]}，篇均 {best_tk[1]['read'] / max(best_tk[1]['count'], 1):.0f} 阅读")
    if sorted_arts:
        s.append(f"- 最高阅读: {sorted_arts[0].get('title', '?')[:40]}（{sorted_arts[0].get('int_page_read_user', 0)}）")
    s.append("\n### 负面问题")
    s.append(f"- 表现最弱赛道: {worst_tk[0]}，篇均 {worst_tk[1]['read'] / max(worst_tk[1]['count'], 1):.0f} 阅读")
    s.append("\n### 下周调整")
    s.append(f"- 增加 {best_tk[0]} 内容配比")
    s.append(f"- 调整 {worst_tk[0]} 选题方向")
    s.append("- 高分享文章→复用争议性观点；高收藏文章→系列化干货")
    return s


def generate_ai_analysis(articles, track_stats, total_read):
    from openai import OpenAI

    key = os.environ.get("SILICONFLOW_API_KEY")
    if not key:
        raise RuntimeError("SILICONFLOW_API_KEY 未设置")

    client = OpenAI(api_key=key, base_url="https://api.siliconflow.cn/v1")

    art_summary = [{"title": a.get("title", "")[:40], "read": a.get("int_page_read_user", 0),
                    "share": a.get("share_user", 0), "fav": a.get("add_to_fav_user", 0),
                    "track": classify_track(a.get("title", ""))} for a in articles]

    tk_summary = {}
    for tk, st in track_stats.items():
        if st["count"] > 0:
            tk_summary[tk] = {"count": st["count"], "total_read": st["read"], "avg": st["read"] / st["count"]}

    prompt = f"""你是广大大公众号运营分析师~上周数据：

总阅读: {total_read}
赛道: {json.dumps(tk_summary, ensure_ascii=False)}
文章: {json.dumps(art_summary, ensure_ascii=False)}

请复盘：
1. 正面规律（什么选题/标题/赛道表现好，为什么）
2. 负面问题（什么差，根因）
3. 下周选题调整建议
4. 排版/封面建议（如有）

口语化直接说，200-400字~"""

    resp = client.chat.completions.create(
        model="Qwen/Qwen2.5-7B-Instruct",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8,
        max_tokens=1000,
    )
    return resp.choices[0].message.content


def main():
    log.info("🚀 广大大每周复盘")
    token = get_token()
    results = fetch_weekly_data(token)
    report = analyze_data(results)
    path = f"/tmp/gzh_output/weekly_review_{datetime.now().strftime('%Y%m%d')}.md"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    arts = results.get("articles", [])
    tr = sum(a.get("int_page_read_user", 0) for a in arts)
    print(f"\n📊 {json.dumps({'total_read': tr, 'count': len(arts), 'path': path}, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
