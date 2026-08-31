# Phase 2-B.8-E PathMNIST 资产只读审查

## 结论

检查点1通过。审查只访问了用户明确授权的两个文件，没有枚举其父目录，没有加载模型，也没有执行推理。

## 授权资产

| 资产 | 大小 | SHA-256 | 额外校验 |
|---|---:|---|---|
| `pathmnist.npz` | 205,615,438 bytes | `81823f52dc622e69db2db4c72f8e8e617938dd6864d3c1f23d4e49724a28ea72` | MD5 `a8b06965200029087d5bd730944a56c1` |
| `resnet18_28_1.pth` | 44,752,263 bytes | `64774e5fdf8786c7f0182eb6a7300d162b12a7a93455805cb2987eb0c12258e0` | CRC32 `2099cbc8` |

真实宿主机路径只保留在本地接入配置中，不进入 ExecutionRequest、AuditEvent、Outbox 或结果 Artifact。

## 数据集来源与许可

- [MedMNIST 官方元数据](https://github.com/MedMNIST/MedMNIST/blob/main/medmnist/info.py)将 PathMNIST 定义为九分类、28×28 RGB 数据，样本数为 89,996/10,004/7,180，许可为 CC BY 4.0。
- [MedMNIST 官方 Zenodo 记录](https://zenodo.org/records/10519652)发布 `pathmnist.npz`，MD5 为 `a8b06965200029087d5bd730944a56c1`，与本地文件完全一致。
- 官方记录明确说明数据不用于临床使用。本阶段仅做工程原型推理验证。

## NPZ结构

| 键 | shape | dtype |
|---|---|---|
| `train_images` | `[89996, 28, 28, 3]` | `uint8` |
| `train_labels` | `[89996, 1]` | `uint8` |
| `val_images` | `[10004, 28, 28, 3]` | `uint8` |
| `val_labels` | `[10004, 1]` | `uint8` |
| `test_images` | `[7180, 28, 28, 3]` | `uint8` |
| `test_labels` | `[7180, 1]` | `uint8` |

全部数组均为非 object dtype。归档中没有患者ID、姓名、文件路径、文本或其他直接身份字段。该判断只覆盖当前NPZ结构，不等于对上游原始病理数据完成隐私影响评估。

## 模型来源与格式

- [MedMNIST 官方 experiments 仓库](https://github.com/MedMNIST/experiments)给出2D ResNet-18实现，并指向[官方模型权重 Zenodo 记录](https://zenodo.org/records/7782114)。
- 官方 `weights_pathmnist.zip` 大小为 1,522,677,234 bytes，MD5 为 `90b4fb5cc399a4caaba8401c438d43c5`。
- 通过HTTP Range只读解析官方ZIP中央目录，找到成员 `resnet18_28_1.pth`：原始大小 44,752,263 bytes，CRC32 `2099cbc8`。二者与本地权重完全一致。
- 权重使用旧式 PyTorch protocol-2 pickle容器，不是现代ZIP checkpoint。静态、非执行性pickle检查仅发现 `OrderedDict`、Torch Float/LongStorage 与 `_rebuild_tensor_v2`，顶层对象包含 `net` state dict，未发现自定义Python类。
- 后续只允许 `torch.load(..., weights_only=True, map_location="cpu")`。如果安全模式失败，不回退到普通pickle加载。

## 静态兼容性证据

- `conv1.weight` 序列化shape为 `[64, 3, 3, 3]`：3通道输入。
- `linear.weight` 序列化shape为 `[9, 512]`，`linear.bias` 为 `[9]`：九分类输出。
- state dict包含 `conv1`、四个两块残差stage、`avgpool`、`linear`，与官方 `MedMNIST2D/models.py` 的小图ResNet-18结构一致。
- 官方训练代码使用 `ToTensor()` 后按 mean/std 0.5 归一化；该预处理将在Manifest中冻结。

## 安全边界

- 本检查点未调用 `torch.load`，未执行模型，未读取图像值，未导出样本。
- 未扫描授权路径以外的目录。
- 没有把源路径、凭据、访问令牌或图像内容写入审计链。
- 资产原文件保持只读、不修改；后续输出只能写入每次Run的隔离工作区。

## 检查点判定

`CHECKPOINT_1 = PASS`

可以进入Manifest、固定白名单entrypoint和正式Preflight。模型加载仍被检查点2门禁阻断。
