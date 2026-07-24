#!/usr/bin/env python3
"""
广大大（SocialPeta）数据采集模块
用途：为公众号文章提供真实的买量数据支撑
使用Playwright浏览器自动化登录广大大后台，提取指定产品/赛道的数据

模块说明：
- 广告主分析：基础信息、应用商店、商店活动、里程碑、创意素材、投放策略
- 热投广告主/新广告主榜/出海投放榜等：排行榜数据
- 竞品对比：多产品对比数据（需订阅）
- 创意灵感：每周热门榜/飙升榜/新创意榜等
"""

import asyncio
import json
import os
import re
import logging
import urllib.request
from datetime import datetime, timedelta
from PIL import Image

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".guangdada_config.json")


def load_config():
    """加载广大大登录配置"""
    if not os.path.exists(CONFIG_PATH):
        log.warning("未找到广大大配置，使用Google News兜底")
        return None
    with open(CONFIG_PATH, "r") as f:
        return json.load(f).get("guangdada")


def slugify(name):
    """把产品名转成截图占位符用的 slug，如 'DramaBox' -> 'dramabox' / 'ReelShort' -> 'reelshort'"""
    return re.sub(r'[^a-z0-9]', '', name.lower())


async def login(page):
    """登录广大大"""
    cfg = load_config()
    if not cfg:
        return False
    try:
        await page.goto(cfg["login_url"], timeout=60000)
        await page.wait_for_timeout(2000)
        await page.fill('input[type="text"]', cfg["username"])
        await page.fill('input[type="password"]', cfg["password"])
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(5000)
        log.info("登录成功")
        return True
    except Exception as e:
        log.error(f"登录失败: {e}")
        return False


async def get_advertiser_list(page, section="drama", limit=15):
    """获取广告主列表数据（短剧/游戏/工具赛道）
    
    section: drama=短剧, game=游戏, tool=工具
    返回广告主排名列表
    """
    urls = {
        "drama": "https://guangdada.net/modules/drama/advertiser-analysis",
        "game": "https://guangdada.net/modules/advertiser/analysis",
        "tool": "https://guangdada.net/modules/advertiser/analysis",
    }
    
    url = urls.get(section, urls["drama"])
    await page.goto(url, timeout=60000)
    await page.wait_for_timeout(5000)
    
    # 如果是游戏/工具赛道，需要先点击对应分类
    if section in ("game", "tool"):
        tab_text = {"game": "游戏", "tool": "工具"}[section]
        # 尝试点顶部tab（不是侧边栏）
        tabs = page.locator(f'[class*="tab"]:has-text("{tab_text}"), button:has-text("{tab_text}"), .ant-tabs-tab:has-text("{tab_text}")')
        if await tabs.count() > 0:
            await tabs.first.click()
            await page.wait_for_timeout(3000)
            log.info(f"点击了{tab_text}分类tab")
        if await tabs.count() > 0:
            await tabs.first.click()
            await page.wait_for_timeout(3000)
    
    # 提取表格数据
    advertisers = await page.evaluate(f"""
        (() => {{
            const rows = document.querySelectorAll('tr');
            const results = [];
            let headerPassed = false;
            for (const row of rows) {{
                const text = row.innerText.trim();
                if (!text || text.startsWith('#')) {{
                    if (text.startsWith('#')) headerPassed = true;
                    continue;
                }}
                if (!headerPassed) continue;
                if (results.length >= {limit}) break;
                
                const cells = row.querySelectorAll('td');
                if (cells.length < 3) continue;
                
                const name = (cells[1]?.innerText || '').split('\\n')[0].trim();
                if (!name) continue;

                const cell = (i) => (cells[i]?.innerText || '').split('\\n')[0].trim();
                results.push({{
                    name: name,
                    total_creatives: cell(2) || '',
                    platforms: cell(3) || '',
                    countries: cell(4) || '',
                    heat: cell(5) || '',
                    downloads: cell(6) || '',
                    recent_90d_creatives: cell(7) || '',
                    duration: cell(8) || '',
                }});
            }}
            return JSON.stringify(results);
        }})()
    """)
    advertisers = json.loads(advertisers)
    log.info(f"获取到 {len(advertisers)} 个广告主数据 ({section})")
    return advertisers


async def subscribe_advertiser(page, name):
    """订阅指定广告主"""
    sub_result = await page.evaluate(f"""
        (() => {{
            const rows = document.querySelectorAll('tr');
            for (const row of rows) {{
                if (row.innerText && row.innerText.includes('{name}')) {{
                    const star = row.querySelector('.anticon-star');
                    if (star) {{
                        const btn = star.closest('button');
                        if (btn) {{ btn.click(); return 'subscribed'; }}
                    }}
                }}
            }}
            return 'not_found';
        }})()
    """)
    await page.wait_for_timeout(2000)
    
    # 处理订阅弹窗
    if sub_result == "subscribed":
        ok_btn = page.locator('button:has-text("确 认")').first
        if await ok_btn.count() > 0:
            await ok_btn.click()
            await page.wait_for_timeout(1500)
            log.info(f"已订阅: {name}")
            return True
    
    log.warning(f"订阅失败: {name}")
    return False


async def get_subscribed_advertiser_detail(page, name):
    """获取已订阅广告主的详细信息"""
    # 先进入已订阅广告主页面
    await page.goto("https://guangdada.net/modules/dashboard/subscribed-advertisers", timeout=60000)
    await page.wait_for_timeout(5000)
    
    # 找到并点击对应的广告主
    link = page.locator(f'a:has-text("{name}")').first
    if await link.count() > 0:
        href = await link.get_attribute("href")
        if href:
            await page.goto(f"https://guangdada.net{href}", timeout=60000)
            await page.wait_for_timeout(5000)
    
    # 提取详情页数据
    detail = await page.evaluate("""
        (() => {
            const text = document.body.innerText;
            const result = {};
            
            // 基础信息区
            const lines = text.split('\\n').filter(l => l.trim());
            
            // 尝试找关键指标
            const patterns = [
                '广告量', '素材量', '投放平台', '投放地区', '热度',
                '下载量', '收入', 'CPI', 'CPA', 'CTR',
                'Facebook', 'Google', 'TikTok', 'SDK'
            ];
            
            patterns.forEach(p => {
                const idx = text.indexOf(p);
                if (idx > -1) {
                    result[p] = text.substring(Math.max(0, idx-20), idx + 100).trim();
                }
            });
            
            return JSON.stringify(result);
        })()
    """)
    return json.loads(detail)


async def get_hot_advertisers(page, chart_type="top"):
    """获取热投广告主/新广告主榜/出海投放榜
    chart_type: top=热投, new=新广告主, chinese=出海投放榜
    """
    urls = {
        "top": "https://guangdada.net/modules/advertiser/top-charts",
        "new": "https://guangdada.net/modules/advertiser/new-trending",
        "chinese": "https://guangdada.net/modules/advertiser/chinese-charts",
        "mini_fb": "https://guangdada.net/modules/advertiser/platform-mini-apps/fb-games-charts",
    }
    url = urls.get(chart_type, urls["top"])
    
    await page.goto(url, timeout=60000)
    await page.wait_for_timeout(5000)
    
    text = await page.inner_text("body")
    return text[:2000]


async def get_creative_charts(page, chart_type="hot"):
    """获取创意灵感模块数据
    chart_type: hot=每周热门榜, surge=飙升榜, new=新创意榜
    """
    urls = {
        "hot": "https://guangdada.net/modules/creative/charts/hot-charts",
        "surge": "https://guangdada.net/modules/creative/charts/surge-charts",
        "new": "https://guangdada.net/modules/creative/charts/new-charts",
        "playable": "https://guangdada.net/modules/creative/playable-ads",
        "marketing": "https://guangdada.net/modules/creative/marketing-calendar",
    }
    url = urls.get(chart_type, urls["hot"])
    
    await page.goto(url, timeout=60000)
    await page.wait_for_timeout(5000)
    
    text = await page.inner_text("body")
    return text[:2000]


async def scrape_section_data(section="drama", products_to_subscribe=None):
    """一键采集指定赛道的完整数据
    
    返回结构化数据字典
    """
    from playwright.async_api import async_playwright
    
    data = {
        "advertiser_list": [],
        "detail": {},
        "hot_charts": "",
        "creative_charts": "",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "广大大(SocialPeta)",
    }
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()
        
        if not await login(page):
            return data
        
        # 1. 获取广告主排行列表
        advertisers = await get_advertiser_list(page, section)
        data["advertiser_list"] = advertisers
        
        # 2. 如果有指定要订阅的产品，订阅并获取详情
        if products_to_subscribe:
            for prod in products_to_subscribe:
                # 先订阅
                ok = await subscribe_advertiser(page, prod)
                if ok:
                    # 获取详情
                    detail = await get_subscribed_advertiser_detail(page, prod)
                    data["detail"][prod] = detail
        
        # 3. 获取热门榜单
        data["hot_charts"] = await get_hot_advertisers(page, "top")
        
        # 4. 获取创意灵感
        data["creative_charts"] = await get_creative_charts(page, "hot")
        
        await browser.close()
    
    return data


async def _goto_analysis(page, section):
    """进入广告主分析页（游戏/工具赛道先点分类tab）"""
    urls = {
        "drama": "https://guangdada.net/modules/drama/advertiser-analysis",
        "game": "https://guangdada.net/modules/advertiser/analysis",
        "tool": "https://guangdada.net/modules/advertiser/analysis",
    }
    await page.goto(urls.get(section, urls["drama"]), timeout=60000)
    await page.wait_for_timeout(5000)
    if section in ("game", "tool"):
        tab_text = {"game": "游戏", "tool": "工具"}[section]
        tabs = page.locator(f'button:has-text("{tab_text}"), .ant-tabs-tab:has-text("{tab_text}")').first
        if await tabs.count() > 0:
            await tabs.click()
            await page.wait_for_timeout(3000)


async def screenshot_product_rows(page, products, section="drama", out_dir="/tmp/gd_shots", slug=None):
    """逐产品截「广告主分析」表里该产品**自己那一行**（元素级截图，不是整页长图）。

    产品在默认榜单里直接用名称定位；不在（如 ReelShort）则先用页内搜索过滤再定位。
    每个产品一张独立截图，和文中讲到的产品一一对应。
    slug: 可选的截图文件名 slug（默认用产品名 slugify）；用于和文章占位符保持一致。
    """
    os.makedirs(out_dir, exist_ok=True)
    await _goto_analysis(page, section)
    shots = {}
    for name in products:
        s = slug or slugify(name)
        path = os.path.join(out_dir, f"analysis_{s}.png")
        try:
            # 先用名称定位该行
            loc = page.locator(f'tr:has-text("{name}")').first
            if await loc.count() == 0:
                # 搜索过滤后再定位
                sb = page.locator('input[placeholder*="搜索"], input[placeholder*="Search"], input[type="text"]').first
                if await sb.count() > 0:
                    await sb.fill(name)
                    await sb.press("Enter")
                    await page.wait_for_timeout(5000)
                loc = page.locator(f'tr:has-text("{name}")').first
            if await loc.count() == 0:
                log.warning(f"分析表未找到: {name}")
                continue
            # 滚到可视区再截该元素本身（只截这一行，不截整页）
            await loc.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)
            try:
                await loc.screenshot(path=path)
            except Exception:
                box = await loc.bounding_box()
                if box:
                    await page.screenshot(path=path, clip=box)
            shots[f"analysis_{slug}"] = path
            log.info(f"产品行截图: {name} -> {path}")
        except Exception as e:
            log.warning(f"产品行截图失败 {name}: {e}")
    return shots


async def screenshot_product_creatives(page, products, out_dir="/tmp/gd_shots", slug=None):
    """逐产品截「创意展示」里该产品**自己的创意**（先按产品搜索过滤，只截该产品创意区，不是整页）。

    返回 {creative_<slug>: path}
    """
    os.makedirs(out_dir, exist_ok=True)
    shots = {}
    for name in products:
        s = slug or slugify(name)
        path = os.path.join(out_dir, f"creative_{s}.png")
        try:
            await page.goto("https://guangdada.net/modules/creative/display-ads", timeout=60000)
            await page.wait_for_timeout(4000)
            sb = page.locator('input[placeholder*="搜索"], input[type="text"]').first
            if await sb.count() > 0:
                await sb.fill(name)
                await sb.press("Enter")
                await page.wait_for_timeout(5000)

            # 截「该产品」的创意网格（已按产品过滤），只取顶部约1-2张创意，和产品一一对应，不截整页长图
            # 选择器要挑一个真实可见、有宽度的网格（避免匹配到隐藏的空容器）
            grid = None
            for sel in ('[class*="creative"]', '[class*="grid"]', '.ant-row', '[class*="list"]'):
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                try:
                    if await loc.is_visible():
                        b = await loc.bounding_box()
                        if b and b["width"] > 200:
                            grid = loc
                            break
                except Exception:
                    continue
            if grid is not None:
                try:
                    await grid.screenshot(path=path)
                except Exception:
                    await page.screenshot(path=path)
            else:
                await page.screenshot(path=path)
            shots[f"creative_{s}"] = path
            log.info(f"创意截图: {name} -> {path}")
        except Exception as e:
            log.warning(f"创意截图失败 {name}: {e}")
    return shots


async def search_advertiser_row(page, name, section="drama"):
    """在广告主分析页搜索某产品，返回其行指标 dict（或 None）。

    用于焦点产品不在默认榜单前15时，也能拿到它的真实指标。
    """
    await _goto_analysis(page, section)
    sb = page.locator('input[placeholder*="搜索"], input[placeholder*="Search"], input[type="text"]').first
    if await sb.count() > 0:
        await sb.fill(name)
        await sb.press("Enter")
        await page.wait_for_timeout(5000)
    rows = await page.evaluate("""() => {
        const rows = Array.from(document.querySelectorAll('tr'));
        const out = [];
        let headerPassed = false;
        for (const row of rows) {
            const t = row.innerText.trim();
            if (!t) continue;
            if (t.startsWith('#')) { headerPassed = true; continue; }
            if (!headerPassed) continue;
            const cells = row.querySelectorAll('td');
            if (cells.length < 3) continue;
            const nm = (cells[1]?.innerText||'').split('\\n')[0].trim();
            if (!nm) continue;
            out.push({
                name: nm,
                total_creatives: cells[2]?.innerText?.trim()||'',
                platforms: cells[3]?.innerText?.trim()||'',
                countries: cells[4]?.innerText?.trim()||'',
                heat: cells[5]?.innerText?.trim()||'',
                downloads: cells[6]?.innerText?.trim()||'',
                recent_90d_creatives: cells[7]?.innerText?.trim()||'',
                duration: cells[8]?.innerText?.trim()||'',
            });
            break;
        }
        return JSON.stringify(out);
    }""")
    found = json.loads(rows)
    return found[0] if found else None


async def collect_product_data(products=None, section="drama", out_dir="/tmp/gd_shots"):
    """一键采集：真实排行榜数据 + 逐产品元素级截图，返回结构化结果。

    products: 焦点产品列表（文章要讲的几个产品，如 ["ReelShort","DramaBox"]）。
              每个焦点产品都会拿到「真实指标 + 自己的分析行截图 + 自己的创意截图」，
              三者一一对应。焦点产品不在默认榜单前15时，会自动用搜索定位。
    返回 lead_data: [{name, slug, 各指标}], screenshots: {"analysis_<slug>":path,"creative_<slug>":path}
    """
    from playwright.async_api import async_playwright

    result = {
        "section": section,
        "products": products or [],
        "ranking": [],
        "advertiser_list": [],
        "detail": {},
        "hot_charts": "",
        "creative_charts": "",
        "lead_products": [],
        "lead_data": [],
        "screenshots": {},
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "广大大(SocialPeta)",
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        page = await context.new_page()

        if not await login(page):
            return result

        # 1. 广告主分析排行榜（真实数据，背景/上下文用）
        result["ranking"] = await get_advertiser_list(page, section)
        result["advertiser_list"] = result["ranking"]

        # 2. 焦点产品：用传入列表；空则取热度前3
        focus = list(products or [])
        if not focus and result["ranking"]:
            focus = [a["name"] for a in result["ranking"][:3]]
        focus = focus[:3]

        lead_data = []
        screenshots = {}
        for name in focus:
            # 指标：先在榜单里按子串找（榜单名带后缀，如 "DramaBox - Stream Drama Shorts"）
            hit = next((a for a in result["ranking"] if name.lower() in a["name"].lower()), None)
            metrics = dict(hit) if hit else None
            real_name = hit["name"] if hit else name
            if metrics is None:
                m = await search_advertiser_row(page, name, section)
                if m:
                    metrics = m
                    real_name = m["name"]
            # slug 用传入的焦点名（干净，如 reelshort/dramabox），和文章占位符一致
            slug = slugify(name)
            # 分析行截图（内部会按名称/搜索定位到该产品那一行）
            screenshots.update(await screenshot_product_rows(page, [real_name], section, out_dir, slug=slug))
            # 创意截图（按产品搜索过滤）
            screenshots.update(await screenshot_product_creatives(page, [real_name], out_dir, slug=slug))
            lead_data.append({
                "name": real_name,
                "slug": slug,
                "total_creatives": (metrics or {}).get("total_creatives", ""),
                "heat": (metrics or {}).get("heat", ""),
                "downloads": (metrics or {}).get("downloads", ""),
                "duration": (metrics or {}).get("duration", ""),
                "recent_90d_creatives": (metrics or {}).get("recent_90d_creatives", ""),
            })
            log.info(f"焦点产品: {real_name} (slug={slug}) 指标={'有' if metrics else '无'}")

        result["lead_data"] = lead_data
        result["lead_products"] = [d["name"] for d in lead_data]
        result["screenshots"] = screenshots

        await browser.close()

    return result


def format_data_for_prompt(data, pillar, track):
    """将广大大数据格式化为AI提示词上下文：聚焦产品表格 + 一一对应的截图占位符"""
    lines = []
    lines.append(f"【广大大实时数据】时间: {data['timestamp']}")
    lines.append("")

    # 优先用 collect_product_data 产出的 lead_data（含焦点产品的真实指标+slug）
    lead = data.get("lead_data") or []
    if not lead:
        ranking = data.get("advertiser_list", []) or data.get("ranking", [])
        lead = [{
            "name": a["name"], "slug": slugify(a["name"]),
            "total_creatives": a.get("total_creatives", ""),
            "heat": a.get("heat", ""),
            "downloads": a.get("downloads", ""),
            "duration": a.get("duration", ""),
            "recent_90d_creatives": a.get("recent_90d_creatives", ""),
        } for a in ranking[:3]]

    if lead:
        lines.append("下面是你这篇文章要讲的产品，在广大大后台抓到的真实数据，请直接采用（不要改数字，也不要补其他产品的数字）：")
        lines.append("")
        lines.append("| 产品 | 累计创意 | 热度 | 下载量 | 投放天数 | 近90天创意 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for a in lead:
            lines.append(
                f"| {a['name']} | {a.get('total_creatives','')} | {a.get('heat','')} | "
                f"{a.get('downloads','')} | {a.get('duration','')} | {a.get('recent_90d_creatives','')} |"
            )
        lines.append("")

        lines.append("下面这些截图占位符，每个都对应一个具体产品（系统会自动插入该产品在广大大后台的真实截图，不是整页长图）：")
        for a in lead:
            slug = a.get("slug") or slugify(a["name"])
            lines.append(f"  - {a['name']}：讲它的数据时紧跟 {{IMG:analysis_{slug}}}；讲它的创意/素材打法时紧跟 {{IMG:creative_{slug}}}")
        lines.append("")
        lines.append("硬要求：截图必须和所讲的产品严格一一对应。讲到A产品就放A的 {{IMG:analysis_<slug>}} / {{IMG:creative_<slug>}}，绝不能把B产品的截图配到A产品上，也绝不能放整页界面截图。")
        lines.append("")

    # 榜单其余产品（仅作背景，不配截图）
    ranking = data.get("advertiser_list", []) or data.get("ranking", [])
    lead_names = {a["name"] for a in lead}
    others = [a for a in ranking if a.get("name") not in lead_names][:7]
    if others:
        lines.append("榜单其他产品（背景参考，文章里提一句即可，不用逐个配图）：")
        for a in others:
            lines.append(f"  - {a.get('name','')}：累计创意 {a.get('total_creatives','')} | 热度 {a.get('heat','')} | 下载量 {a.get('downloads','')}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    # 测试：采集短剧赛道数据
    import sys
    section = sys.argv[1] if len(sys.argv) > 1 else "drama"
    
    async def test():
        data = await scrape_section_data(section, products_to_subscribe=["ReelShort"])
        print(format_data_for_prompt(data, "competitor", section))
    
    asyncio.run(test())
