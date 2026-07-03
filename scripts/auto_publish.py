#!/usr/bin/env python3
"""
广大大公众号独立发布脚本 v2
完全脱离WorkBuddy运行，支持：
 - 本地Markdown文件推送（--file）
 - 微信公众号草稿箱推送
 - launchd / cron 定时调度

环境变量（写入 ~/.zshrc）：
  WECHAT_APPID_GGD  = 广大大公众号AppID
  WECHAT_SECRET_GGD = 广大大公众号AppSecret
  COVER_IMAGE       = 封面图路径（可选，默认找 /tmp/wechat_cover_latest.png）

依赖安装：
  pip3 install requests pillow

用法：
  # 从本地文件推送
  python3 auto_publish.py --file outputs/wechat_draft_0703.md

  # 指定封面图
  python3 auto_publish.py --file outputs/wechat_draft_0703.md --cover /tmp/cover.png

  # 只生成HTML不推送
  python3 auto_publish.py --file outputs/wechat_draft_0703.md --dry-run
"""

import os
import sys
import json
import time
import argparse
import re
import textwrap
from datetime import datetime
from pathlib import Path

# ============================================================
# 配置 — 使用广大大专属环境变量（不会跟其他公众号冲突）
# ============================================================

WECHAT_APPID  = os.getenv("WECHAT_APPID_GGD")
WECHAT_SECRET = os.getenv("WECHAT_SECRET_GGD")
WECHAT_AUTHOR = os.getenv("WECHAT_AUTHOR", "广大大")

OUTPUT_DIR = Path.home() / "WorkBuddy" / "2026-07-02-11-07-02" / "outputs"
WECHAT_API = "https://api.weixin.qq.com/cgi-bin"
TOKEN_CACHE = Path("/tmp/wechat_token_cache_ggd.json")

# ============================================================
# 微信 API
# ============================================================

def get_wechat_token():
    """获取微信公众号 access_token（带缓存）"""
    import requests
    if TOKEN_CACHE.exists():
        try:
            cache = json.loads(TOKEN_CACHE.read_text())
            if cache.get("expires_at", 0) > time.time() + 300:
                return cache["access_token"]
        except Exception:
            pass

    url = f"{WECHAT_API}/token"
    resp = requests.get(url, params={
        "grant_type": "client_credential",
        "appid": WECHAT_APPID,
        "secret": WECHAT_SECRET
    }, timeout=15)
    data = resp.json()
    if "access_token" not in data:
        raise Exception(f"获取Token失败: {data}")

    token = data["access_token"]
    TOKEN_CACHE.write_text(json.dumps({
        "access_token": token,
        "expires_at": time.time() + data.get("expires_in", 7200) - 300
    }))
    return token


def upload_cover(token, image_path):
    """上传封面图（900x383），返回 thumb_media_id"""
    import requests
    from PIL import Image as PILImage

    # 先检查/调整尺寸
    try:
        img = PILImage.open(image_path)
        w, h = img.size
        if abs(w/h - 900/383) > 0.05:
            print(f"   ⚠️ 封面尺寸 {w}x{h} 不符合 900x383，将自动裁剪")
            target_h = int(w * 383 / 900)
            if target_h < h:
                # 居中裁剪
                top = (h - target_h) // 2
                img = img.crop((0, top, w, top + target_h))
            img = img.resize((900, 383), PILImage.LANCZOS)
            adjusted_path = str(Path(image_path).parent / "cover_adjusted.png")
            img.save(adjusted_path, "PNG")
            image_path = adjusted_path
            print(f"   ✅ 已调整为 900x383")
    except ImportError:
        print("   ⚠️ Pillow未安装，跳过尺寸检查（pip3 install pillow）")
    except Exception as e:
        print(f"   ⚠️ 封面图处理失败: {e}，使用原图上传")

    url = f"{WECHAT_API}/material/add_material"
    with open(image_path, "rb") as f:
        resp = requests.post(url, params={
            "access_token": token, "type": "image"
        }, files={"media": (os.path.basename(image_path), f, "image/png")},
        timeout=30)
    data = resp.json()
    if "media_id" not in data:
        raise Exception(f"上传封面失败: {data}")
    return data["media_id"]


def create_draft(token, title, digest, content_html, thumb_media_id):
    """创建微信公众号草稿，返回 media_id"""
    import requests
    url = f"{WECHAT_API}/draft/add"
    payload = {
        "articles": [{
            "title": title,
            "author": WECHAT_AUTHOR,
            "digest": digest,
            "content": content_html,
            "thumb_media_id": thumb_media_id,
            "need_open_comment": 1,
            "only_fans_can_comment": 0
        }]
    }
    resp = requests.post(url, params={"access_token": token},
                         data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                         headers={"Content-Type": "application/json"},
                         timeout=30)
    data = resp.json()
    if "media_id" not in data:
        raise Exception(f"创建草稿失败: {data}")
    return data["media_id"]


# ============================================================
# Markdown → 微信公众号HTML 排版引擎
# ============================================================

def clean_html(html):
    """清洗HTML：去除字面\\n、压缩空白、验证无残留"""
    html = html.replace('\\n', ' ')
    html = re.sub(r'>\s+<', '><', html).strip()
    if '\\n' in html:
        raise ValueError('HTML中检测到字面 \\n 字符，清洗失败！')
    return html


def md_to_wechat_html(body, title=""):
    """
    将Markdown正文转为专业微信公众号HTML
    
    支持标记：
    - ## 标题 → 带左边色条的小节标题
    - > 引用 → 引用块
    - | 表格 | → HTML表格
    - **加粗** → <strong>
    - --- 分隔 → 分隔线
    - 空行 → 段落分隔
    """
    lines = body.strip().split("\n")
    html_parts = []
    in_table = False
    table_rows = []

    # 引导关注区
    html_parts.append(
        '<section style="text-align:center;margin-bottom:28px;padding:14px 0;border-bottom:1px solid #eee;">'
        '<p style="color:#999;font-size:12px;letter-spacing:1px;margin:0;">'
        '点击上方蓝字关注，每天解锁出海买量新玩法</p>'
        '</section>'
    )

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 空行
        if not line:
            if in_table:
                # 表格结束
                html_parts.append(_render_table(table_rows))
                table_rows = []
                in_table = False
            i += 1
            continue

        # 表格行（以 | 开头）
        if line.startswith("|") and line.endswith("|"):
            in_table = True
            cells = [c.strip() for c in line.strip("|").split("|")]
            # 跳过分隔行（如 |---|---|）
            if all(re.match(r'^[-:]+$', c.strip()) for c in cells):
                i += 1
                continue
            table_rows.append(cells)
            i += 1
            continue

        # 如果之前在表格中，现在退出表格
        if in_table:
            html_parts.append(_render_table(table_rows))
            table_rows = []
            in_table = False

        # ## 标题 → h2 左边条
        if line.startswith("## "):
            text = line[3:].strip()
            html_parts.append(
                f'<h2 style="font-size:17px;font-weight:bold;color:#1a1a1a;'
                f'margin:30px 0 16px 0;padding-left:12px;border-left:4px solid #185FA5;'
                f'line-height:1.4;">{text}</h2>'
            )

        # > 引用
        elif line.startswith("> "):
            text = line[2:].strip()
            html_parts.append(
                f'<blockquote style="background:#f5f7fa;border-left:4px solid #185FA5;'
                f'padding:14px 18px;margin:22px 0;color:#555;font-size:14px;'
                f'line-height:1.85;border-radius:0 6px 6px 0;">'
                f'{text}</blockquote>'
            )

        # --- 分隔线
        elif line in ("---", "——"):
            html_parts.append(
                '<section style="text-align:center;margin:28px 0;">'
                '<span style="display:inline-block;width:40px;height:3px;'
                'background:#185FA5;border-radius:2px;"></span>'
                '</section>'
            )

        # 普通段落
        else:
            # 处理加粗 **text** → <strong>text</strong>
            line = _process_bold(line)
            html_parts.append(
                f'<p style="margin:0 0 20px 0;text-indent:0;">{line}</p>'
            )

        i += 1

    # 最后如果还在表格中
    if in_table and table_rows:
        html_parts.append(_render_table(table_rows))

    body_html = "\n".join(html_parts)

    # 整体容器
    full_html = f"""<section style="padding:12px 10px;font-size:15px;line-height:1.88;color:#333;word-break:break-all;font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;">
{body_html}
<section style="margin:24px 0 10px 0;text-align:center;padding-top:16px;border-top:1px solid #eee;">
<p style="margin:0;font-size:12px;color:#bbb;">广大大 | 出海买量数据专家</p>
</section>
</section>"""

    return clean_html(full_html)


def _process_bold(text):
    """处理 **text** → <strong>text</strong>"""
    result = []
    parts = text.split("**")
    for j, part in enumerate(parts):
        if j % 2 == 1:
            result.append(f"<strong>{part}</strong>")
        else:
            result.append(part)
    return "".join(result)


def _render_table(rows):
    """渲染HTML表格"""
    if not rows:
        return ""
    cells_html = []
    for ri, row in enumerate(rows):
        is_header = (ri == 0)
        td_style = "padding:10px 12px;border:1px solid #e0e0e0;"
        if is_header:
            td_style += "font-weight:bold;background:#f5f7fa;"
        row_html = "<tr>" + "".join(
            f'<td style="{td_style}">{cell}</td>' for cell in row
        ) + "</tr>"
        cells_html.append(row_html)
    return (
        f'<table style="width:100%;border-collapse:collapse;margin:22px 0;font-size:13px;">'
        f'{"".join(cells_html)}'
        f'</table>'
    )


# ============================================================
# 从Markdown文件解析文章
# ============================================================

def parse_markdown_file(filepath):
    """从Markdown文件解析标题、摘要、正文"""
    content = Path(filepath).read_text(encoding="utf-8")
    lines = content.strip().split("\n")

    # 标题：第一个 # 开头的行
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            body_start = i + 1
            break

    if not title:
        title = lines[0].strip().lstrip("# ").strip()
        body_start = 1

    # 摘要：标题后第一行非空行
    digest = "出海买量数据分析和策略分享~"
    for i in range(body_start, len(lines)):
        stripped = lines[i].strip()
        if stripped and not stripped.startswith("#"):
            digest = stripped[:80]
            body_start = i + 1
            break

    # 正文：剩余内容
    body_lines = []
    for i in range(body_start, len(lines)):
        body_lines.append(lines[i])
    body = "\n".join(body_lines).strip()

    return title, digest, body


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="广大大公众号独立发布工具 v2")
    parser.add_argument("--file", required=True, help="Markdown文件路径")
    parser.add_argument("--cover", help="封面图路径（默认按日期自动查找）")
    parser.add_argument("--dry-run", action="store_true", help="只生成HTML不推送")
    parser.add_argument("--output-dir", help="输出目录（默认自动）")
    args = parser.parse_args()

    # === 1. 校验配置 ===
    if not WECHAT_APPID or not WECHAT_SECRET:
        print("❌ 未配置环境变量！请确保 ~/.zshrc 中有：")
        print("   export WECHAT_APPID_GGD=\"wx94eb6ba27c82a203\"")
        print("   export WECHAT_SECRET_GGD=\"09dbad7d...\"")
        sys.exit(1)

    # === 2. 解析文章 ===
    md_file = Path(args.file)
    if not md_file.exists():
        print(f"❌ 文件不存在: {md_file}")
        sys.exit(1)

    print(f"📖 读取文章: {md_file.name}")
    title, digest, body = parse_markdown_file(md_file)

    print(f"📝 标题: {title}")
    print(f"📄 摘要: {digest[:50]}...")
    print(f"📊 字数: {len(body)}")

    # === 3. 生成HTML ===
    html = md_to_wechat_html(body, title)
    print(f"🎨 HTML长度: {len(html)}")

    # 保存
    date_str = datetime.now().strftime("%m%d")
    out_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"wechat_draft_{date_str}.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"💾 HTML: {html_path}")

    if args.dry_run:
        print("\n🔍 Dry-run模式，跳过推送")
        print(f"   可手动打开 {html_path} 预览")
        return

    # === 4. 获取封面图 ===
    cover_path = args.cover
    if not cover_path:
        # 自动查找：按日期 /tmp/wechat_cover_{MMDD}.png
        auto_cover = Path(f"/tmp/wechat_cover_{date_str}.png")
        if auto_cover.exists():
            cover_path = str(auto_cover)
        else:
            # 降级：找最新的封面图
            import glob
            candidates = sorted(glob.glob("/tmp/wechat_cover_*.png"), reverse=True)
            if candidates:
                cover_path = candidates[0]
                print(f"📸 使用最新封面: {cover_path}")

    if not cover_path or not Path(cover_path).exists():
        print("❌ 未找到封面图！请用 --cover 指定")
        print("   或确保 /tmp/wechat_cover_{MMDD}.png 存在")
        sys.exit(1)

    # === 5. 推送草稿 ===
    print(f"\n📤 推送草稿到广大大公众号...")
    try:
        token = get_wechat_token()
        print(f"   ✅ Token获取成功")

        thumb_media_id = upload_cover(token, cover_path)
        print(f"   ✅ 封面上传成功: {thumb_media_id[:15]}...")

        media_id = create_draft(token, title, digest, html, thumb_media_id)
        print(f"\n✅ 草稿推送成功！")
        print(f"   media_id: {media_id}")
        print(f"   前往审核: https://mp.weixin.qq.com → 内容管理 → 草稿箱")
    except Exception as e:
        print(f"\n❌ 推送失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
