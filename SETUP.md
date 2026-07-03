# GitHub 自动推送配置（一步一图）

## 第一步：创建仓库

1. 打开 https://github.com/new
2. Repository name 随便填，比如 `wechat-publisher`
3. 选 **Private**（私有仓库，别人看不到）
4. 不要勾选 "Add a README file"
5. 点 **Create repository**

---

## 第二步：推送代码

创建仓库后会显示一个 "…or push an existing repository" 的指令框，复制那三行命令。大致是：

```bash
cd /Users/zltang/Documents/workbuddy/2026-07-02-14-49-15/cloudstudio-publisher

git init
git remote add origin https://github.com/你的用户名/wechat-publisher.git
git add -A
git commit -m "初始提交"
git branch -M main
git push -u origin main
```

---

## 第三步：设置 Secrets

推送成功后，打开仓库页面：

```
仓库首页
  → 顶部点「Settings」（不是个人设置，是仓库设置）
    → 左侧菜单找到「Secrets and variables」
      → 点「Actions」
        → 绿色按钮「New repository secret」
```

添加第一个 Secret：

| Name | Secret |
|------|--------|
| `WECHAT_APPID` | `wx51779815b6bc189c` |

点 **Add secret**。

再点一次「New repository secret」，添加第二个：

| Name | Secret |
|------|--------|
| `WECHAT_SECRET` | `64e66fb2e99864d283759e1053f1ab23` |

---

## 第四步：启用 Actions

```
仓库首页
  → 顶部点「Actions」
    → 点绿色按钮「I understand my workflows, go ahead and enable them」
```

如果已经有这个绿色按钮的话点一下就行。没有就说明已经启用了。

---

## 完成

点「Actions」标签可以看到 "公众号每日自动推送" workflow。它会自动在每天北京时间 08:45 和 09:05 运行。

想手动测试的话，进 workflow 页面 → 右侧「Run workflow」下拉 → 绿色「Run workflow」按钮。
