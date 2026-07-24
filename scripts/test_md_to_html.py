"""单元测试 markdown_to_html 的截图占位符替换逻辑（不触发完整推送）。"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from cloud_daily_publish import markdown_to_html

URL_MAP = {
    "analysis": "https://mmbiz.qpic.cn/test_analysis.png",
    "creative": "https://mmbiz.qpic.cn/test_creative.png",
}

MD = """## 一、数据总览

根据广大大后台数据，ReelShort 这类短剧App投放很猛~

{IMG:analysis}

中间也插一句，下面这张是创意库截图 {IMG:creative} 看着还挺猛~

## 二、对比一下

| 产品 | 素材量 | 热度 |
| --- | --- | --- |
| ReelShort | 1.2万 | 5.6亿 |
| DramaBox | 9800 | 4.1亿 |

{{IMG:creative}}

结尾收束一下，整体来看短剧在卷~"""

html = markdown_to_html(MD, "drama", img_map=URL_MAP)
print("===== HTML 输出 =====")
print(html)
print("===== 断言检查 =====")
assert "<img src='https://mmbiz.qpic.cn/test_analysis.png'" in html, "analysis 占位符未替换!"
assert "<img src='https://mmbiz.qpic.cn/test_creative.png'" in html, "creative 占位符未替换!"
assert "{IMG" not in html, "仍有遗留占位符文本!"
assert html.count("<img") == 3, f"期望3张图，实际 {html.count('<img')}"
assert "<table" in html, "表格未生成!"
assert "## 一、数据总览" not in html, "小标题应已转为p!"
print("ALL ASSERTIONS PASSED ✅")
