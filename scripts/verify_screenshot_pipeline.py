"""验证截图上传+嵌入+草稿全流程（用真实的广大大截图与之前的LLM正文）。
不调用LLM，只验证：截图->微信CDN->HTML嵌入->推草稿->删废稿。
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(__file__))
import cloud_daily_publish as cp

BROKEN_DRAFT = "xo5vTK_FXB9_PG0oNS8ylFzZhJrPXz9Ar1A6LEDbcH6u6Nc66tDCmfm4nrpqihBK"

def main():
    token = cp.wechat_get_token()
    print("TOKEN OK")

    # 1) 上传真实截图到微信
    shots = {
        "analysis": "/tmp/gd_shots/analysis_drama.png",
        "creative": "/tmp/gd_shots/creative_ReelShort.png",
    }
    img_map = {}
    for k, p in shots.items():
        u = cp.wechat_upload_image(token, p)
        if not u:
            raise SystemExit(f"截图 {k} 上传失败")
        img_map[k] = u
    print("IMG_MAP:", img_map)

    # 2) 用之前的真实LLM正文（含 {IMG:analysis}/{IMG:creative} 单花括号）
    md = open("outputs/draft_competitor.md", encoding="utf-8").read()
    lines = md.strip().split("\n")
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip():
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip()

    html = cp.markdown_to_html(body, "competitor", img_map=img_map)
    assert "{IMG" not in html, "仍有遗留占位符!"
    n = html.count("<img")
    assert n == 3, f"期望3张图，实际 {n}"
    print(f"HTML 含 {n} 张 <img>，无遗留占位符 ✅")

    # 3) 合规检查
    clean, banned = cp.check_no_competitor(html)
    assert clean, f"竞品命中: {banned}"

    # 4) 封面 + 推草稿
    cover_path = "outputs/cover_drama.png"
    thumb = cp.wechat_upload_cover(token, cover_path)
    title, digest = cp.extract_title_digest(md, "竞品拆解台", "2026-07-24")
    html_clean = cp.re.sub(r">\s+<", "><", html.replace("\n", " ").strip())
    draft_id = cp.wechat_create_draft(token, title, digest, html_clean, thumb)
    print(f"新草稿ID: {draft_id} | 标题: {title}")

    # 5) 删除旧的废稿
    ok = cp.wechat_delete_draft(token, BROKEN_DRAFT)
    print(f"删除废稿: {'成功' if ok else '失败(可能已手动删)'}")

    print("DONE ✅")

if __name__ == "__main__":
    main()
