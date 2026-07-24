#!/usr/bin/env python3
"""生成广大大公众号当周发布排期，并发送到钉钉群

- 排期严格对齐 cloud_daily_publish.get_daily_pillar 的真实执行逻辑
  （工作日每天1篇，月末最后工作日改为数据月报）
- 每周一由自动化触发，也可手动运行：python3 scripts/weekly_plan.py
- 加 --no-send 参数只生成本地文件、不推送钉钉
"""
import sys
import os
import json
import datetime
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cloud_daily_publish as g

DINGTALK_WEBHOOK = (
    "https://oapi.dingtalk.com/robot/send"
    "?access_token=4998794fa3693eccf5f7f75c829b6da490a5c625852dd9d8edd01da929978328"
)

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def build_plan():
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)

    lines = []
    lines.append(
        f"广大大 公众号本周发布排期（{monday.strftime('%m/%d')}-{sunday.strftime('%m/%d')}）"
    )
    lines.append("")

    has_monthly = False
    for i in range(7):
        d = monday + datetime.timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        pillar_key, track_key = g.get_daily_pillar(ds)
        if pillar_key is None:
            lines.append(f"{WEEKDAY_CN[i]} {d.strftime('%m/%d')}  周末不发布")
            continue
        pillar = g.PILLARS[pillar_key]
        name = pillar["name"]
        if pillar_key == "monthly_report":
            has_monthly = True
        if track_key:
            track = g.TRACKS[track_key]
            entry = f"{WEEKDAY_CN[i]} {d.strftime('%m/%d')}  {name}·{track['name']}"
        else:
            entry = f"{WEEKDAY_CN[i]} {d.strftime('%m/%d')}  {name}"
        lines.append(entry)

    lines.append("")
    lines.append("推送时间：每个工作日早上（北京时间约 7-10 点）自动推送至草稿箱，需登录后台手动审核发布")
    if has_monthly:
        lines.append("注：本周含月末工作日，当天改为「数据月报」")
    lines.append("如需调整某天选题，可手动指定 pillar/track 触发 workflow_dispatch 重推")
    return "\n".join(lines)


def send_dingtalk(text):
    payload = json.dumps({"msgtype": "text", "text": {"content": text}}).encode("utf-8")
    req = urllib.request.Request(
        DINGTALK_WEBHOOK,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


if __name__ == "__main__":
    plan = build_plan()
    print(plan)

    out_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs"
    )
    os.makedirs(out_dir, exist_ok=True)
    monday = datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())
    out_path = os.path.join(out_dir, f"weekly_plan_{monday.strftime('%Y-%m-%d')}.md")
    with open(out_path, "w") as f:
        f.write(plan)
    print(f"\n已保存: {out_path}")

    if "--no-send" not in sys.argv:
        res = send_dingtalk("广大大 公众号本周发布排期\n" + plan)
        print("钉钉发送结果:", res)
    else:
        print("（--no-send：未推送钉钉）")
