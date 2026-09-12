# MedTrust Space Web3 本地原型

本目录是 MedTrust Space Phase 5.4–5.6 的**独立链上验证原型**，用于在本地演示四方确认、角色资格凭证、费用托管和双证明结算。它不是生产支付系统，也不处理或上传患者数据。

## 能演示什么

| 阶段 | 合约 | 本地原型能力 |
| --- | --- | --- |
| Phase 5.4 | `MedTrustAgreementRegistry` | 锚定合同条款摘要；需求方、数据方、模型方依次确认，空间运营方必须最后确认；有效期已开始时，第 4 次确认自动激活 |
| Phase 5.5 | `MedTrustRoleCredential` | 由平台准入方签发可撤销、可过期且不可转让的角色凭证；合约只保存 DID、机构和审核证据的摘要 |
| Phase 5.6 | `MedTrustEscrow` | 需求方用本地 `mCNY` Mock 代币托管费用；成功执行证明和获批交付证明全部到齐后，分别生成数据方、模型方和平台方的可提现余额 |

这里的“4-of-4”是固定四方地址对同一合同摘要的一致确认状态机，不是一个通用 DAO，也不是已经审计的多签钱包实现。当前端到端版本只支持 Hardhat EOA 测试账户；未来若接入 Safe 等合约钱包，后端还必须增加 EIP-1271 验签和内部调用回执核验，不能只替换一个地址就宣称支持。

## 网站集成演示（一键启动）

先确认 Docker Desktop、项目原有前后端依赖和 `config/phase4-demo.env` 已准备完成，再从仓库根目录运行：

```powershell
cd "D:\罗小罗\blockchian_for_2026"
.\scripts\prepare_web3_roadshow.ps1 -Open
```

脚本会启动一次性 Hardhat 节点，部署合约，将地址写入 Git 忽略的 `blockchain/deployments/localhost.json` 和 `backend/.env.local`，重建专用演示库并启动网站。**该命令每次都会重置 `medtrust_phase4_demo` 专用演示库。** 完整角色顺序、钱包设置与故障排查见 [Phase 5.4–5.6 Web3 本地路演手册](../docs/PHASE54_56_WEB3_ROADSHOW.md)。

只停止本次本地链：

```powershell
.\scripts\stop_web3_demo.ps1
```

## 合约独立验证

要求 Node.js 20+ 和 pnpm。首次进入本目录后安装依赖并验证：

```powershell
cd "D:\罗小罗\blockchian_for_2026\blockchain"
pnpm install --frozen-lockfile
pnpm build
pnpm test
pnpm demo
```

`pnpm demo` 会在一次临时 Hardhat 网络中完成并断言以下完整流程：

1. 按 `RoleCredential → AgreementRegistry → MockSettlementToken → Escrow` 部署；
2. 给四个参与方签发有效角色凭证；
3. 注册固定合同摘要并完成 4-of-4 确认与自动激活；
4. 用本地 Mock 代币托管数据费、模型费和平台费；
5. 分别提交执行证据摘要和交付证据摘要；
6. 验证三方可提现余额，并实际执行本地提现。

### 部署到持续运行的本地节点

先启动节点：

```powershell
pnpm node
```

在另一个 PowerShell 窗口执行：

```powershell
pnpm deploy:local
```

部署脚本严格按依赖顺序部署四份合约，把合约地址、链 ID 和本地演示账户地址打印到终端，同时写入 Git 忽略的 `deployments/localhost.json`。清单只包含地址和公开部署信息，不包含私钥；Hardhat 节点输出的一次性测试私钥只能用于本机临时链，严禁提交或复用。

## 链上与链下边界

- 链上只放合同、任务、执行证据和交付证据的摘要，以及必要的公开地址和状态。
- 患者记录、身份材料、合同正文、模型权重、执行日志和结果文件必须留在受控链下系统。
- 角色凭证是不可转让、可撤销、可过期的 ERC-5192 风格声明；它只证明“某准入方曾对该地址及角色作出声明”，不等同于国家认证的 DID、法定 KYC、医疗资质或机构尽调。
- 每套 `RoleCredential → AgreementRegistry → Escrow` 部署都绑定同一个不可变空间摘要，三个合约在构造时拒绝跨空间错配；网站启动时还会把清单摘要与当前演示空间复核。不同数据空间必须独立部署。
- 浏览器钱包交易能证明链上调用者控制相应地址，但不等同于自行取得网站身份；集成路径使用服务端 challenge、nonce、防重放、链 ID、来源和 HttpOnly 会话绑定，并继续以平台已审核账号、机构和角色为准。
- 后端同步链上状态时应等待约定确认数，并用交易哈希、日志序号和业务 ID 做幂等处理，不能在数据库事务中阻塞等待链上交易。
- 自动结算不会绕过原有结果审核：必须先存在成功执行证据和已审批安全结果包。两项证明齐备后生成三方 `claimable` 余额，并为 Web3 订单解锁链下结果下载授权；实际领取仍由收款方单独调用 pull-payment 提现。

## 明确限制

`MockSettlementToken` 是可随意部署的本地演示代币，**没有真实价值、没有真实资金、没有支付或法币结算能力**。本目录合约尚未经过独立安全审计，也没有完成密钥托管、多签治理、预言机、争议处理、链重组、隐私和监管验证，**不得部署到主网或承载真实业务**。本地自动结算使用两个不同的 Hardhat 测试证明账户，但链上交易与 PostgreSQL 镜像不是原子事务；生产环境必须改用持久任务、事件索引器和彼此独立的证明服务。进一步风险和上线前检查见 [SECURITY.md](./SECURITY.md)。
