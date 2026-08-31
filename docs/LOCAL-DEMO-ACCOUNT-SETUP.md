# 本地演示账号设置

## 设置密码

在项目根目录运行：

```powershell
.\scripts\set_local_demo_password.ps1
```

脚本会隐藏输入并要求重复确认，密码长度必须为 12 至 128 个字符。它只写入被 Git 忽略的：

```text
config/phase4-demo.env
backend/.env.local
```

脚本不会打印密码。

## LAN-only 独立临时密码

四账号可以在被 Git 忽略的 `config/phase4-demo.env` 中分别使用
`MEDTRUST_DEMO_HOSPITAL_PASSWORD`、`MEDTRUST_DEMO_MODEL_PASSWORD`、
`MEDTRUST_DEMO_REQUESTER_PASSWORD` 和 `MEDTRUST_DEMO_OPERATOR_PASSWORD`。
运行 `scripts/setup_roadshow_accounts.ps1` 只轮换现有账号的 scrypt 哈希，
不会初始化或重置业务数据。用户名与密码相同的弱演示凭据仅允许用于
`local` 和 `lan-roadshow`；远程预览及生产模板会拒绝加载该配置。

## 初始化账号

设置密码后运行：

```powershell
.\scripts\prepare_roadshow.ps1
```

准备流程会确保四个用户名和 Membership 存在，并为当前本地密码建立 scrypt 哈希。再次使用不同密码准备时会轮换哈希并撤销旧会话。

## 禁止事项

- 不把真实密码写入示例配置、README、交付报告或 Git。
- 不在命令行参数、日志或截图中显示密码。
- 不提交 `.env.local`、`phase4-demo.env`、Cookie、Token 或密钥。
- 本地演示账号不是开放注册、邮箱验证或生产身份系统。
