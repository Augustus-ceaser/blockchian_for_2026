# 四浏览器 Profile 设置

## 推荐布局

创建四个独立浏览器 Profile，并固定名称：

```text
MedTrust-Hospital
MedTrust-Model
MedTrust-Requester
MedTrust-Operator
```

每个 Profile 只登录一个本地演示账号。不要用同一 Profile 的四个普通标签页代替，因为它们共享 Cookie。

## 桌面布局

- 1920×1080：四窗口 2×2 排列。
- 1366×768：主讲窗口占左侧，另外三个窗口竖排或通过任务栏切换。
- 390×844：仅用于移动端布局验收，不作为四窗口路演布局。

## 首次准备

1. 运行 `scripts/prepare_roadshow.ps1`。
2. 在四个 Profile 分别打开 `/demo-login`。
3. 使用对应用户名登录。
4. 确认页头显示正确账号、组织和角色门户。
5. 在运营端打开 `/lifecycle`，其余三端确认该路由显示“无权访问”。
6. 退出医院端，确认另外三个窗口仍保持登录。

## 安全要求

- 不把浏览器 Profile 目录提交到 Git。
- 不导出 Cookie，不截图密码输入过程。
- 不在文档、日志或剪贴板中保存会话值。
- 路演结束后可退出账号；需要彻底清理时删除本地 Profile，而不是修改数据库业务事实。
