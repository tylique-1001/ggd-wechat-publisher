# 广大大公众号云端发布系统

GitHub Actions + AI 驱动的微信公众号自动发布方案。

## 功能
- **每日自动发布**: 工作日 9:00 自动生成3篇文章（游戏/工具/短剧）并推送到草稿箱
- **每周数据复盘**: 每周一自动拉取阅读数据，AI 分析并生成复盘报告
- **完全云端**: 不依赖本地机器，关机不影响推送

## 工作流
- `.github/workflows/publish-daily.yml` — 每日发布
- `.github/workflows/review-weekly.yml` — 每周复盘
