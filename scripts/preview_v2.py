#!/usr/bin/env python3
"""只生成预览文件（不推草稿）：封面 2.35:1 + 逐产品一一对应截图嵌入 HTML。"""
import sys, os, io
sys.path.insert(0, os.path.dirname(__file__))
import cloud_daily_publish as cp
from PIL import Image

OUT = "/tmp/gd_shots4"
os.makedirs("outputs", exist_ok=True)

cover = cp.generate_cover("drama", product_name="ReelShort", article_title="ReelShort和DramaBox，卷出了两种活法")
cim = Image.open(io.BytesIO(cover))
print(f"[封面] {cim.size} 比例 {cim.size[0]/cim.size[1]:.3f}")
open("outputs/cover_drama.png", "wb").write(cover)

token = cp.wechat_get_token()
img_map = {}
for slug in ("analysis_reelshort", "creative_reelshort", "analysis_dramabox", "creative_dramabox"):
    p = os.path.join(OUT, f"{slug}.png")
    if os.path.exists(p):
        u = cp.wechat_upload_image(token, p)
        if u:
            img_map[slug] = u
print(f"[截图CDN] {list(img_map.keys())}")

md = """ReelShort和DramaBox，卷出了两种活法

先甩一张表，这俩在广大大后台的数据直接看：

| 产品 | 累计创意 | 热度 | 下载量 | 投放天数 | 近90天创意 |
| --- | --- | --- | --- | --- | --- |
| ReelShort | 3958832 | 2.7亿 | 1570万 | 1485 | 110万 |
| DramaBox | 3761599 | 2.9亿 | 915万 | 1180 | 72万 |

根据广大大后台数据，ReelShort这边累计创意395万条出头，热度2.7亿，下载量干到1570万，投放天数1485天，是真能熬~

{{IMG:analysis_reelshort}}

它玩的是"反转钩子"，前3秒必给一个反转点把人留住~ 翻它的创意库，素材基本是"误会—反转—再误会"的短平快结构。

{{IMG:creative_reelshort}}

DramaBox这边，累计创意376万条，热度反而更高到2.9亿，但下载量只有915万~

{{IMG:analysis_dramabox}}

它的打法是"情绪轰炸"，把婆媳、霸总、追妻这些情绪钩子拉满，一张素材就能把人看红眼~

{{IMG:creative_dramabox}}

有意思的是，DramaBox近90天创意降到72万条，比ReelShort的110万少一截~ 说明它意识到"量"换不来"质"，开始收着发了~

整体看，一个靠反转抓完播，一个靠情绪抓沉浸，路数不一样但都还活着~"""

html = cp.markdown_to_html(md, "competitor", img_map=img_map)
open("outputs/draft_competitor.html", "w", encoding="utf-8").write(html)
open("outputs/draft_competitor.md", "w", encoding="utf-8").write(md)
print(f"[HTML] <img>={html.count('<img')} 遗留占位符={'IMG:' in html}")
print("[完成] outputs/draft_competitor.html / cover_drama.png")
