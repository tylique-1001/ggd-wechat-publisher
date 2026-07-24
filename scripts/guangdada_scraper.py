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
import logging
import urllib.request
from datetime import datetime, timedelta

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
                
                results.push({{
                    name: name,
                    total_creatives: cells[2]?.innerText?.trim() || '',
                    platforms: cells[3]?.innerText?.trim() || '',
                    countries: cells[4]?.innerText?.trim() || '',
                    heat: cells[5]?.innerText?.trim() || '',
                    downloads: cells[6]?.innerText?.trim() || '',
                    recent_90d_creatives: cells[7]?.innerText?.trim() || '',
                    duration: cells[8]?.innerText?.trim() || '',
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


async def screenshot_analysis_table(page, section="drama", out_dir="/tmp/gd_shots"):
    """截取「广告主分析」排行榜表格（数据区），返回截图路径

    只截表格数据区域，不含导航/筛选项，完整不裁切。
    """
    os.makedirs(out_dir, exist_ok=True)
    urls = {
        "drama": "https://guangdada.net/modules/drama/advertiser-analysis",
        "game": "https://guangdada.net/modules/advertiser/analysis",
        "tool": "https://guangdada.net/modules/advertiser/analysis",
    }
    await page.goto(urls.get(section, urls["drama"]), timeout=60000)
    await page.wait_for_timeout(6000)

    # 游戏/工具赛道需先点分类tab
    if section in ("game", "tool"):
        tab_text = {"game": "游戏", "tool": "工具"}[section]
        tabs = page.locator(f'button:has-text("{tab_text}"), .ant-tabs-tab:has-text("{tab_text}")').first
        if await tabs.count() > 0:
            await tabs.click()
            await page.wait_for_timeout(3000)

    # 定位表格容器
    table = page.locator('table, .ant-table-container, [class*="table"]').first
    path = os.path.join(out_dir, f"analysis_{section}.png")
    try:
        await table.screenshot(path=path)
    except Exception:
        # 兜底：截视口
        await page.screenshot(path=path)
    log.info(f"广告主分析截图: {path}")
    return path


async def screenshot_creative_grid(page, product=None, out_dir="/tmp/gd_shots"):
    """截取「创意展示」页创意网格（创意库），返回截图路径

    若传入 product，尝试用页内搜索过滤该产品创意；否则截顶部热门创意。
    只截创意网格区域，不含导航/筛选项。
    """
    os.makedirs(out_dir, exist_ok=True)
    await page.goto("https://guangdada.net/modules/creative/display-ads", timeout=60000)
    await page.wait_for_timeout(5000)

    if product:
        sb = page.locator('input[placeholder*="搜索"], input[type="text"]').first
        if await sb.count() > 0:
            await sb.fill(product)
            await sb.press("Enter")
            await page.wait_for_timeout(5000)

    # 定位创意网格容器（优先带 creative/grid 类，兜底视口）
    grid = page.locator('[class*="creative"], [class*="grid"], .ant-row, [class*="list"]').first
    path = os.path.join(out_dir, f"creative_{product or 'top'}.png")
    try:
        await grid.screenshot(path=path)
    except Exception:
        await page.screenshot(path=path)
    log.info(f"创意库截图: {path}")
    return path


async def collect_product_data(product=None, section="drama", out_dir="/tmp/gd_shots"):
    """一键采集：真实排行榜数据 + 两张数据区截图，返回结构化结果"""
    from playwright.async_api import async_playwright

    result = {
        "section": section,
        "product": product,
        "ranking": [],
        "advertiser_list": [],
        "detail": {},
        "hot_charts": "",
        "creative_charts": "",
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

        # 1. 广告主分析排行榜（真实数据）
        result["ranking"] = await get_advertiser_list(page, section)
        result["advertiser_list"] = result["ranking"]  # 兼容 format_data_for_prompt

        # 2. 截图：广告主分析数据区
        result["screenshots"]["analysis"] = await screenshot_analysis_table(page, section, out_dir)

        # 3. 截图：创意库（该产品创意网格）
        result["screenshots"]["creative"] = await screenshot_creative_grid(page, product, out_dir)

        await browser.close()

    return result


def format_data_for_prompt(data, pillar, track):
    """将广大大数据格式化为AI提示词上下文"""
    lines = []
    lines.append(f"【广大大实时数据】时间: {data['timestamp']}")
    lines.append("")
    
    # 广告主排行
    if data["advertiser_list"]:
        lines.append(f"📊 {track}赛道广告主排行:")
        for i, adv in enumerate(data["advertiser_list"][:10], 1):
            lines.append(f"  {i}. {adv['name']}")
            lines.append(f"     全部创意: {adv['total_creatives']} | 热度: {adv['heat']} | 下载量: {adv['downloads']}")
            lines.append(f"     近90天创意: {adv['recent_90d_creatives']} | 投放天数: {adv['duration']}")
        lines.append("")
    
    # 详情
    if data["detail"]:
        lines.append("📋 广告主详情:")
        for name, detail in data["detail"].items():
            lines.append(f"  {name}:")
            for k, v in detail.items():
                lines.append(f"    {k}: {v[:100]}")
        lines.append("")
    
    # 热门榜单
    if data["hot_charts"]:
        lines.append(f"🔥 热投广告主榜单(前500字):")
        lines.append(data["hot_charts"][:500])
        lines.append("")
    
    # 创意灵感
    if data["creative_charts"]:
        lines.append(f"💡 每周热门创意(前500字):")
        lines.append(data["creative_charts"][:500])
    
    return "\n".join(lines)


if __name__ == "__main__":
    # 测试：采集短剧赛道数据
    import sys
    section = sys.argv[1] if len(sys.argv) > 1 else "drama"
    
    async def test():
        data = await scrape_section_data(section, products_to_subscribe=["ReelShort"])
        print(format_data_for_prompt(data, "competitor", section))
    
    asyncio.run(test())
