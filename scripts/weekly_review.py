#!/usr/bin/env python3
"""
广大大公众号 每周数据复盘脚本
每周一自动拉取上周数据，生成复盘报告

数据来源：
- 单篇数据：/cgi-bin/datacube/getarticlesummary
- 汇总数据：/cgi-bin/datacube/getarticletotal  
- 用户阅读：/cgi-bin/datacube/getuserread
- 用户分享：/cgi-bin/datacube/getusershare
- 粉丝数据：/cgi-bin/user/get (需通过 tags API)
"""

import json
import sys
import os
import subprocess
from datetime import datetime, timedelta

# 硬编码防串号
GGD_APPID = "wx94eb6ba27c82a203"
GGD_SECRET = "09dbad7d7ebfd9304d1b39e136170db3"

def get_token():
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={GGD_APPID}&secret={GGD_SECRET}"
    result = subprocess.run(["curl", "-s", url], capture_output=True, text=True)
    data = json.loads(result.stdout)
    return data.get("access_token", "")

def api_post(token, endpoint, payload):
    """通用POST请求"""
    url = f"https://api.weixin.qq.com{endpoint}?access_token={token}"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        tmp = f.name
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", url, "-H", "Content-Type: application/json", "-d", f"@{tmp}"],
        capture_output=True, text=True
    )
    os.unlink(tmp)
    data = json.loads(result.stdout)
    if "errcode" in data and data["errcode"] != 0:
        print(f"  [WARN] {endpoint}: {data.get('errmsg', 'unknown')}", file=sys.stderr)
        return None
    return data

def get_date_range(days_ago_start, days_ago_end):
    """获取日期范围 YYYY-MM-DD"""
    end = datetime.now() - timedelta(days=days_ago_end)
    start = datetime.now() - timedelta(days=days_ago_start)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

def get_article_stats(token):
    """获取上周单篇和汇总数据"""
    begin, end = get_date_range(7, 1)
    print(f"\n📊 数据范围: {begin} ~ {end}")
    
    import tempfile
    global tempfile as _tempfile
    _tempfile = tempfile
    
    results = {}
    
    # 单篇文章数据
    summary = api_post(token, "/datacube/getarticlesummary", {"begin_date": begin.replace("-", ""), "end_date": end.replace("-", "")})
    if summary and "list" in summary:
        results["articles"] = summary["list"]
    
    # 文章汇总
    total = api_post(token, "/datacube/getarticletotal", {"begin_date": begin.replace("-", ""), "end_date": end.replace("-", "")})
    if total and "list" in total:
        results["total"] = total["list"]
    
    # 用户阅读
    read = api_post(token, "/datacube/getuserread", {"begin_date": begin.replace("-", ""), "end_date": end.replace("-", "")})
    if read and "list" in read:
        results["user_read"] = read["list"]
    
    # 用户分享
    share = api_post(token, "/datacube/getusershare", {"begin_date": begin.replace("-", ""), "end_date": end.replace("-", "")})
    if share and "list" in share:
        results["user_share"] = share["list"]
    
    return results

def report(results):
    """生成复盘报告"""
    print("\n" + "=" * 60)
    print("📊 广大大公众号 每周数据复盘")
    print("=" * 60)
    
    if not results:
        print("\n⚠️ 未能获取到数据~可能原因：")
        print("  1. 账号类型不支持 datacube API（需认证服务号）")
        print("  2. 上周没有发布文章")
        print("  3. API 调用频率限制")
        return
    
    # 汇总数据
    if "total" in results:
        t = results["total"]
        for day in t:
            ref_date = day.get("ref_date", "?")
            msg_count = day.get("msg_count", 0)
            user_read = day.get("int_page_read_user", 0)
            user_share = day.get("share_user", 0)
            fav_user = day.get("add_to_fav_user", 0)
            print(f"\n📅 {ref_date}: 阅读{user_read} | 分享{user_share} | 收藏{fav_user} | 文章{msg_count}")
    
    # 单篇数据
    if "articles" in results:
        print(f"\n📰 单篇文章数据:")
        a = results["articles"]
        if isinstance(a, list):
            for art in a:
                title = art.get("title", "?")[:30]
                read = art.get("int_page_read_user", 0)
                share = art.get("share_user", 0)
                fav = art.get("add_to_fav_user", 0)
                print(f"  📝 {title}... → 阅读{read} | 分享{share} | 收藏{fav}")
    
    print(f"\n{'=' * 60}")
    print("📌 复盘建议：")
    print("  1. 阅读最高的文章 → 分析选题方向，下周重点复制")
    print("  2. 分享最高的文章 → 分析标题/观点是否有争议性/共鸣点")
    print("  3. 阅读最低的文章 → 检查是选题问题还是标题问题")
    print("  4. 关注三个赛道的阅读差异，调整配比")
    print("=" * 60)

if __name__ == "__main__":
    import tempfile as _tf
    global tempfile
    tempfile = _tf
    token = get_token()
    if not token:
        print("[FATAL] 获取token失败", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] Token 获取成功 (广大大 {GGD_APPID})")
    results = get_article_stats(token)
    report(results)
