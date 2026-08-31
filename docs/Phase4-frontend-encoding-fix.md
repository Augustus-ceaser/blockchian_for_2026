# Phase 4 前端编码修复记录

日期：2026-07-24

## 问题

`frontend/src/roadshow/RoadshowPages.tsx` 发生了文件级中文错码。中文 UTF-8 字节曾被按 GBK 解释并再次保存，形成了 `鑽`、`涓`、`宸` 等乱码。

部分无法往返转换的字节已经变成字面量 `?`，进一步破坏了字符串引号、模板字符串和 JSX 属性边界。Vite 首个报告位置为第 19 行，但损坏并不局限于状态标签。

## 修复范围

已处理：

- 对 `RoadshowPages.tsx` 执行一次机械编码反向恢复；
- 补回错码过程中丢失的中文字符、引号和 JSX 分隔符；
- 恢复状态标签、页面标题、操作文案、提示信息和路演流程文案；
- 扫描 `frontend/src/roadshow` 及整个 `frontend/src` 的已知乱码和 Unicode 替代字符。

未修改：

- 业务条件和角色权限；
- API 路径和请求参数；
- 状态机及状态判断；
- 后端、数据库、Docker 和部署配置。

## 修复文件

- `frontend/src/roadshow/RoadshowPages.tsx`

其余 `frontend/src/roadshow` 下的 TypeScript/TSX 文件未发现同类乱码。

## 验证

在 `frontend` 目录执行：

```powershell
pnpm typecheck
pnpm build
```

结果：

- TypeScript 类型检查通过；
- Vite 生产构建通过，完成 3693 个模块转换；
- `RoadshowPages.tsx` 通过严格 UTF-8 解码检查，且不带 BOM；
- `frontend/src` 未检出本次已知乱码标记或 Unicode 替代字符。

构建仍提示主 JavaScript chunk 大于 500 kB。该提示与编码修复无关，本次未调整代码拆分或构建配置。
