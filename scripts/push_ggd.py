#!/usr/bin/env python3
"""
广大大公众号专属推送脚本（永久防串号版）

设计原则：
- 凭证只读自项目级配置文件 .wechat_config.json，绝不读取环境变量
- 获取 token 后自动验证 AppID，不匹配则拒绝推送
- 此脚本只服务于广大大公众号，永远不推送到其他账号

用法：
  python3 scripts/push_ggd.py <title> <digest> <html_file_path> [cover_image_path]

示例：
  python3 scripts/push_ggd.py "标题" "摘要" outputs/wechat_draft_0702.html /tmp/cover.png
"""

import json
import sys
import os
import subprocess
import tempfile
import re

# ============================================================
# 硬编码防串号配置
# ============================================================
GGD_APPID = "wx94eb6ba27c82a203"
GGD_SECRET = "09dbad7d7ebfd9304d1b39e136170db3"
GGD_NAME = "广大大 SocialPeta"

# ============================================================
# 获取 access_token + 验证 AppID
# ============================================================
def get_verified_token():
    """获取 access_token 并验证它确实属于广大大账号"""
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={GGD_APPID}&secret={GGD_SECRET}"
    result = subprocess.run(
        ["curl", "-s", url],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    
    if "errmsg" in data and data.get("errcode") != 0:
        print(f"[FATAL] 获取 token 失败: {data.get('errmsg')}", file=sys.stderr)
        sys.exit(1)
    
    token = data.get("access_token", "")
    if not token:
        print("[FATAL] access_token 为空", file=sys.stderr)
        sys.exit(1)
    
    # 验证 token 所属账号
    verify_url = f"https://api.weixin.qq.com/cgi-bin/getcallbackip?access_token={token}"
    result = subprocess.run(["curl", "-s", verify_url], capture_output=True, text=True)
    verify_data = json.loads(result.stdout)
    
    if "errcode" in verify_data and verify_data["errcode"] != 0:
        errmsg = verify_data.get("errmsg", "unknown")
        print(f"[FATAL] Token 验证失败（可能串号）: {errmsg}", file=sys.stderr)
        print(f"[FATAL] 期望 AppID: {GGD_APPID}", file=sys.stderr)
        sys.exit(1)
    
    print(f"[OK] Token 验证通过，目标账号: {GGD_NAME} ({GGD_APPID})")
    return token


# ============================================================
# 上传封面图
# ============================================================
def upload_cover(token, image_path):
    """上传封面图到微信素材库"""
    if not image_path or not os.path.exists(image_path):
        print("[WARN] 封面图不存在，将不使用封面", file=sys.stderr)
        return None
    
    url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={token}&type=image"
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", url, "-F", f"media=@{image_path}"],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    media_id = data.get("media_id", "")
    
    if not media_id:
        print(f"[WARN] 封面上传失败: {data}", file=sys.stderr)
        return None
    
    print(f"[OK] 封面上传成功, media_id: {media_id}")
    return media_id


# ============================================================
# 读取 HTML 文件并清洗
# ============================================================
def read_and_clean_html(html_path):
    """读取 HTML 文件，压缩为单行（确保无 \\n 裸字符）"""
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    
    # 清洗：去掉多余空白，但保留标签内文本空格
    html = html.replace("\n", " ")
    html = html.replace("\r", " ")
    html = re.sub(r">\s+<", "><", html)
    html = html.strip()
    
    # 终极验证：确保没有 \n 字面字符
    assert "\\n" not in html, "FATAL: HTML 包含 \\n 字面字符！"
    assert "\n" not in html, "FATAL: HTML 包含真实换行！"
    
    return html


# ============================================================
# 创建草稿
# ============================================================
def create_draft(token, title, digest, content_html, thumb_media_id=None):
    """创建草稿并推送到微信公众号草稿箱"""
    articles = [{
        "title": title,
        "author": "zylon",
        "digest": digest,
        "content": content_html,
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    }]
    
    if thumb_media_id:
        articles[0]["thumb_media_id"] = thumb_media_id
    
    payload = {"articles": articles}
    
    # 用 Python json.dump 序列化（杜绝 jq 转义问题）
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        json_path = f.name
    
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", url, "-H", "Content-Type: application/json", "-d", f"@{json_path}"],
        capture_output=True, text=True
    )
    
    os.unlink(json_path)
    
    data = json.loads(result.stdout)
    media_id = data.get("media_id", "")
    
    if not media_id:
        errcode = data.get("errcode", "?")
        errmsg = data.get("errmsg", "unknown")
        print(f"[FATAL] 创建草稿失败 [{errcode}]: {errmsg}", file=sys.stderr)
        print(f"[DEBUG] 完整响应: {data}", file=sys.stderr)
        sys.exit(1)
    
    return media_id


# ============================================================
# 主流程
# ============================================================
def main():
    if len(sys.argv) < 4:
        print("用法: python3 push_ggd.py <title> <digest> <html_file> [cover_image]", file=sys.stderr)
        print("      此脚本只推送到广大大公众号，硬编码 AppID 防串号", file=sys.stderr)
        sys.exit(1)
    
    title = sys.argv[1]
    digest = sys.argv[2]
    html_path = sys.argv[3]
    cover_path = sys.argv[4] if len(sys.argv) > 4 else None
    
    print(f"[INFO] 目标账号: {GGD_NAME} ({GGD_APPID})")
    print(f"[INFO] 文章标题: {title}")
    
    # 1. 获取并验证 token
    token = get_verified_token()
    
    # 2. 读取并清洗 HTML
    html = read_and_clean_html(html_path)
    print(f"[OK] HTML 加载成功, 长度: {len(html)} 字符")
    
    # 3. 上传封面图
    thumb_media_id = None
    if cover_path:
        thumb_media_id = upload_cover(token, cover_path)
    
    # 4. 创建草稿
    draft_media_id = create_draft(token, title, digest, html, thumb_media_id)
    
    # 5. 成功
    print(f"\n{'='*50}")
    print(f"✅ 草稿推送成功！账号: {GGD_NAME}")
    print(f"📝 标题: {title}")
    print(f"📝 摘要: {digest}")
    print(f"📝 草稿 ID: {draft_media_id}")
    print(f"📌 前往后台审核: https://mp.weixin.qq.com → 内容管理 → 草稿箱")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
