from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Sequence


PATHMNIST_ENTRYPOINT_ID = "pathmnist_resnet18_v1"
PATHMNIST_LABELS = (
    "adipose",
    "background",
    "debris",
    "lymphocytes",
    "mucus",
    "smooth muscle",
    "normal colon mucosa",
    "cancer-associated stroma",
    "colorectal adenocarcinoma epithelium",
)
PATHMNIST_OUTPUT_FILES = (
    "aggregate_metrics.json",
    "confusion_matrix.csv",
    "execution_summary.json",
)


class PathMNISTExecutionError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


@dataclass(frozen=True)
class PathMNISTAssetBinding:
    dataset_path: Path
    model_path: Path
    dataset_digest: str
    model_digest: str

    def validate(self) -> None:
        for path in (self.dataset_path, self.model_path):
            if not path.is_file() or path.is_symlink():
                raise PathMNISTExecutionError("registered asset is missing or unsafe")
        if sha256_file(self.dataset_path) != self.dataset_digest:
            raise PathMNISTExecutionError("registered dataset digest mismatch")
        if sha256_file(self.model_path) != self.model_digest:
            raise PathMNISTExecutionError("registered model digest mismatch")


@dataclass(frozen=True)
class PathMNISTExecutionResult:
    output_manifest: tuple[dict[str, Any], ...]
    output_digest: str
    execution_summary: dict[str, Any]
    resource_usage_summary: dict[str, Any]
    prediction_digest: str


def _build_resnet18(torch: Any) -> Any:
    nn = torch.nn
    functional = torch.nn.functional

    class BasicBlock(nn.Module):
        expansion = 1

        def __init__(self, in_planes: int, planes: int, stride: int = 1) -> None:
            super().__init__()
            self.conv1 = nn.Conv2d(
                in_planes, planes, kernel_size=3, stride=stride, padding=1, bias=False
            )
            self.bn1 = nn.BatchNorm2d(planes)
            self.conv2 = nn.Conv2d(
                planes, planes, kernel_size=3, stride=1, padding=1, bias=False
            )
            self.bn2 = nn.BatchNorm2d(planes)
            self.shortcut = nn.Sequential()
            if stride != 1 or in_planes != planes:
                self.shortcut = nn.Sequential(
                    nn.Conv2d(in_planes, planes, kernel_size=1, stride=stride, bias=False),
                    nn.BatchNorm2d(planes),
                )

        def forward(self, value: Any) -> Any:
            output = functional.relu(self.bn1(self.conv1(value)))
            output = self.bn2(self.conv2(output))
            output += self.shortcut(value)
            return functional.relu(output)

    class ResNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.in_planes = 64
            self.conv1 = nn.Conv2d(3, 64, kernel_size=3, stride=1, padding=1, bias=False)
            self.bn1 = nn.BatchNorm2d(64)
            self.layer1 = self._make_layer(BasicBlock, 64, 2, stride=1)
            self.layer2 = self._make_layer(BasicBlock, 128, 2, stride=2)
            self.layer3 = self._make_layer(BasicBlock, 256, 2, stride=2)
            self.layer4 = self._make_layer(BasicBlock, 512, 2, stride=2)
            self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
            self.linear = nn.Linear(512, 9)

        def _make_layer(
            self, block: type[Any], planes: int, blocks: int, *, stride: int
        ) -> Any:
            strides = [stride, *([1] * (blocks - 1))]
            layers = []
            for layer_stride in strides:
                layers.append(block(self.in_planes, planes, layer_stride))
                self.in_planes = planes
            return nn.Sequential(*layers)

        def forward(self, value: Any) -> Any:
            output = functional.relu(self.bn1(self.conv1(value)))
            output = self.layer1(output)
            output = self.layer2(output)
            output = self.layer3(output)
            output = self.layer4(output)
            output = self.avgpool(output)
            return self.linear(output.view(output.size(0), -1))

    return ResNet()


def _load_model(binding: PathMNISTAssetBinding) -> tuple[Any, Any]:
    try:
        import torch
    except ImportError as exc:
        raise PathMNISTExecutionError("PyTorch runtime is unavailable") from exc

    checkpoint = torch.load(
        binding.model_path,
        map_location="cpu",
        weights_only=True,
    )
    if not isinstance(checkpoint, dict) or set(checkpoint) != {"net"}:
        raise PathMNISTExecutionError("checkpoint must contain only the net state dict")
    state_dict = checkpoint["net"]
    if not isinstance(state_dict, dict):
        raise PathMNISTExecutionError("checkpoint net value is not a state dict")
    model = _build_resnet18(torch)
    model.load_state_dict(state_dict, strict=True)
    if any(not torch.isfinite(value).all().item() for value in state_dict.values()):
        raise PathMNISTExecutionError("checkpoint contains NaN or Inf")
    model.to("cpu")
    model.eval()
    return torch, model


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _content_digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def run_pathmnist_smoke(
    *,
    binding: PathMNISTAssetBinding,
    test_indices: Sequence[int],
    output_dir: Path,
    verify_reproducibility: bool,
) -> PathMNISTExecutionResult:
    if len(test_indices) != 20 or len(set(test_indices)) != 20:
        raise PathMNISTExecutionError("exactly 20 unique test indices are required")
    if any(not isinstance(index, int) or not 0 <= index < 7180 for index in test_indices):
        raise PathMNISTExecutionError("test index is outside the registered split")
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise PathMNISTExecutionError("output workspace is unsafe")
    binding.validate()

    try:
        import numpy as np
        import psutil
    except ImportError as exc:
        raise PathMNISTExecutionError("registered numerical runtime is unavailable") from exc

    with np.load(binding.dataset_path, allow_pickle=False) as archive:
        if set(archive.files) != {
            "train_images",
            "train_labels",
            "val_images",
            "val_labels",
            "test_images",
            "test_labels",
        }:
            raise PathMNISTExecutionError("PathMNIST archive schema mismatch")
        images = np.ascontiguousarray(archive["test_images"][list(test_indices)])
        labels = np.ascontiguousarray(archive["test_labels"][list(test_indices), 0])
    if images.shape != (20, 28, 28, 3) or images.dtype != np.uint8:
        raise PathMNISTExecutionError("PathMNIST image batch mismatch")
    if labels.shape != (20,) or labels.dtype != np.uint8 or np.any(labels > 8):
        raise PathMNISTExecutionError("PathMNIST label batch mismatch")

    torch, model = _load_model(binding)
    torch.manual_seed(20260723)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    input_tensor = torch.from_numpy(images).permute(0, 3, 1, 2).to(torch.float32)
    input_tensor = (input_tensor / 255.0 - 0.5) / 0.5

    process = psutil.Process()
    memory_before = process.memory_info()
    rss_before = memory_before.rss
    started = time.perf_counter()
    with torch.inference_mode():
        logits = model(input_tensor)
        probabilities = torch.softmax(logits, dim=1)
        if verify_reproducibility:
            repeated = torch.softmax(model(input_tensor), dim=1)
            if not torch.equal(probabilities, repeated):
                raise PathMNISTExecutionError("CPU inference is not reproducible")
    elapsed = time.perf_counter() - started
    memory_after = process.memory_info()
    rss_after = memory_after.rss
    peak_rss = max(
        rss_before,
        rss_after,
        int(getattr(memory_after, "peak_wset", 0)),
    )
    if tuple(probabilities.shape) != (20, 9):
        raise PathMNISTExecutionError("model output shape mismatch")
    if not torch.isfinite(probabilities).all().item():
        raise PathMNISTExecutionError("model output contains NaN or Inf")

    prediction_digest = _content_digest(
        probabilities.detach().cpu().numpy().astype("<f4", copy=False).tobytes()
    )
    predictions = probabilities.argmax(dim=1).cpu().numpy()
    confidence = probabilities.max(dim=1).values.cpu().numpy()
    confusion = np.zeros((9, 9), dtype=np.int64)
    for expected, predicted in zip(labels.tolist(), predictions.tolist(), strict=True):
        confusion[int(expected), int(predicted)] += 1
    distribution = np.bincount(predictions, minlength=9)
    correct_predictions = int((predictions == labels).sum())
    accuracy = float(correct_predictions / len(labels))
    mean_confidence = float(confidence.mean())

    aggregate_metrics = {
        "schema_version": "pathmnist-aggregate-metrics/v1",
        "sample_count": 20,
        "accuracy": format(accuracy, ".12g"),
        "mean_confidence": format(mean_confidence, ".12g"),
        "confusion_matrix": confusion.tolist(),
        "prediction_digest": prediction_digest,
    }
    resource_usage = {
        "device": "cpu",
        "torch_threads": 1,
        "inference_seconds": format(elapsed, ".12g"),
        "rss_before_mb": format(rss_before / 1024 / 1024, ".12g"),
        "rss_after_mb": format(rss_after / 1024 / 1024, ".12g"),
        "peak_rss_mb": format(peak_rss / 1024 / 1024, ".12g"),
        "hard_isolation": False,
    }
    execution_summary = {
        "schema_version": "pathmnist-execution-summary/v1",
        "entrypoint_id": PATHMNIST_ENTRYPOINT_ID,
        "sample_count": 20,
        "processed_count": 20,
        "failed_count": 0,
        "correct_predictions": correct_predictions,
        "accuracy": format(accuracy, ".12g"),
        "mean_confidence": format(mean_confidence, ".12g"),
        "split": "test",
        "model_digest": binding.model_digest,
        "dataset_digest": binding.dataset_digest,
        "dataset_digest_after": sha256_file(binding.dataset_path),
        "dataset_digest_unchanged": sha256_file(binding.dataset_path)
        == binding.dataset_digest,
        "model_digest_verified": sha256_file(binding.model_path)
        == binding.model_digest,
        "prediction_digest": prediction_digest,
        "network_access": False,
        "inference_only": True,
        "non_clinical": True,
        "unexpected_output_count": 0,
        "resource_usage": resource_usage,
    }

    documents = {
        "aggregate_metrics.json": aggregate_metrics,
        "execution_summary.json": execution_summary,
    }
    manifest_items = []
    for name, document in documents.items():
        content = _json_bytes(document)
        (output_dir / name).write_bytes(content)
        manifest_items.append(
            {
                "name": name,
                "media_type": "application/json",
                "size_bytes": len(content),
                "digest": _content_digest(content),
            }
        )
    confusion_rows = [",".join(["expected/predicted", *PATHMNIST_LABELS])]
    for label, row in zip(PATHMNIST_LABELS, confusion.tolist(), strict=True):
        confusion_rows.append(",".join([label, *(str(value) for value in row)]))
    confusion_bytes = ("\n".join(confusion_rows) + "\n").encode("utf-8")
    (output_dir / "confusion_matrix.csv").write_bytes(confusion_bytes)
    manifest_items.append(
        {
            "name": "confusion_matrix.csv",
            "media_type": "text/csv",
            "size_bytes": len(confusion_bytes),
            "digest": _content_digest(confusion_bytes),
        }
    )
    output_manifest_document = {
        "schema_version": "pathmnist-output-manifest/v1",
        "outputs": manifest_items,
    }
    output_manifest_bytes = _json_bytes(output_manifest_document)
    if {item["name"] for item in manifest_items} != set(PATHMNIST_OUTPUT_FILES):
        raise PathMNISTExecutionError("output file allowlist mismatch")
    return PathMNISTExecutionResult(
        output_manifest=tuple(manifest_items),
        output_digest=_content_digest(output_manifest_bytes),
        execution_summary=execution_summary,
        resource_usage_summary=resource_usage,
        prediction_digest=prediction_digest,
    )
