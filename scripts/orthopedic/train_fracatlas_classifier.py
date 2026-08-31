from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import torch
from PIL import Image, ImageFile
from torch import nn
from torch.utils.data import DataLoader, Dataset
from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small


SEED = 20260829
MODEL_ID = "medtrust-fracatlas-mobilenet-v3-small-v1"
SCHEMA_VERSION = "medtrust.fixed-model-manifest/v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, document: Any) -> None:
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def normalize_image_id(value: str) -> str | None:
    text = value.strip().strip('"').strip("'")
    if not text:
        return None
    if text.lower().endswith((".jpg", ".jpeg", ".png")):
        return Path(text).name
    if text.isdigit():
        return f"IMG{int(text):07d}.jpg"
    return None


def read_dataset_labels(dataset_root: Path) -> dict[str, int]:
    metadata_path = dataset_root / "dataset.csv"
    if not metadata_path.is_file():
        matches = list(dataset_root.rglob("dataset.csv"))
        if len(matches) != 1:
            raise RuntimeError("FracAtlas dataset.csv was not found unambiguously")
        metadata_path = matches[0]
    labels: dict[str, int] = {}
    with metadata_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            image_id = normalize_image_id(str(row.get("image_id") or row.get("filename") or ""))
            raw_label = str(row.get("fractured") or "").strip()
            if image_id is None or raw_label not in {"0", "1"}:
                continue
            labels[image_id.casefold()] = int(raw_label)
    if not labels:
        raise RuntimeError("FracAtlas dataset.csv did not yield image labels")
    return labels


def deterministic_stratified_splits(labels: dict[str, int]) -> dict[str, list[str]]:
    grouped: dict[int, list[str]] = {0: [], 1: []}
    for image_id, label in labels.items():
        grouped[label].append(image_id)
    splits: dict[str, list[str]] = {"train": [], "valid": [], "test": []}
    for label, image_ids in grouped.items():
        rng = random.Random(SEED + label)
        rng.shuffle(image_ids)
        train_end = int(len(image_ids) * 0.70)
        valid_end = train_end + int(len(image_ids) * 0.15)
        splits["train"].extend(image_ids[:train_end])
        splits["valid"].extend(image_ids[train_end:valid_end])
        splits["test"].extend(image_ids[valid_end:])
    for index, split in enumerate(("train", "valid", "test")):
        random.Random(SEED + 10 + index).shuffle(splits[split])
    return splits


def locate_split_files(dataset_root: Path) -> dict[str, Path]:
    aliases = {"train": {"train.csv"}, "valid": {"valid.csv", "val.csv"}, "test": {"test.csv"}}
    result: dict[str, Path] = {}
    for split, names in aliases.items():
        matches = [
            path
            for path in dataset_root.rglob("*.csv")
            if path.name.casefold() in names
            and "fracture split" in str(path.parent).replace("_", " ").casefold()
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one official FracAtlas {split} split, found {len(matches)}")
        result[split] = matches[0]
    return result


def read_split_ids(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise RuntimeError(f"Official split is empty: {path}")
    image_ids: list[str] = []
    for row in rows:
        candidates = [normalize_image_id(cell) for cell in row]
        image_id = next((item for item in candidates if item is not None), None)
        if image_id is not None:
            image_ids.append(image_id)
    unique = list(dict.fromkeys(image_ids))
    if not unique:
        raise RuntimeError(f"Official split did not contain image identifiers: {path}")
    return unique


def build_image_index(dataset_root: Path) -> dict[str, Path]:
    candidates = [
        path
        for path in dataset_root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".jpg", ".jpeg", ".png"}
        and "images" in {part.casefold() for part in path.parts}
    ]
    index = {path.name.casefold(): path for path in candidates}
    if len(index) < 100:
        raise RuntimeError("FracAtlas image directory does not look complete")
    return index


def audit_image_integrity(image_index: dict[str, Path]) -> list[str]:
    truncated: list[str] = []
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    for image_id, path in image_index.items():
        try:
            with Image.open(path) as image:
                image.load()
        except OSError as exc:
            if "truncated" not in str(exc).casefold():
                raise RuntimeError(f"Unreadable FracAtlas image: {path}") from exc
            truncated.append(image_id)
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    return sorted(truncated)


class FracAtlasSplit(Dataset[tuple[torch.Tensor, int, str]]):
    def __init__(
        self,
        image_ids: Iterable[str],
        labels: dict[str, int],
        image_index: dict[str, Path],
        transform: Any,
    ) -> None:
        self.records: list[tuple[Path, int, str]] = []
        for image_id in image_ids:
            key = image_id.casefold()
            if key not in labels or key not in image_index:
                raise RuntimeError(f"Missing FracAtlas label or image for {image_id}")
            self.records.append((image_index[key], labels[key], image_id))
        self.transform = transform

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int, str]:
        path, label, image_id = self.records[index]
        with Image.open(path) as image:
            tensor = self.transform(image.convert("RGB"))
        return tensor, label, image_id


@torch.inference_mode()
def extract_embeddings(
    model: nn.Module,
    loader: DataLoader[tuple[torch.Tensor, int, str]],
) -> tuple[torch.Tensor, torch.Tensor, list[str]]:
    model.eval()
    feature_batches: list[torch.Tensor] = []
    label_batches: list[torch.Tensor] = []
    image_ids: list[str] = []
    for images, labels, batch_ids in loader:
        features = model.features(images)
        features = model.avgpool(features)
        features = torch.flatten(features, 1)
        feature_batches.append(features.cpu())
        label_batches.append(labels.cpu())
        image_ids.extend(batch_ids)
    return torch.cat(feature_batches), torch.cat(label_batches), image_ids


def confusion_counts(labels: torch.Tensor, predictions: torch.Tensor) -> dict[str, int]:
    y = labels.to(torch.int64)
    p = predictions.to(torch.int64)
    return {
        "true_negative": int(((y == 0) & (p == 0)).sum().item()),
        "false_positive": int(((y == 0) & (p == 1)).sum().item()),
        "false_negative": int(((y == 1) & (p == 0)).sum().item()),
        "true_positive": int(((y == 1) & (p == 1)).sum().item()),
    }


def safe_ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


@torch.inference_mode()
def evaluate_head(head: nn.Module, features: torch.Tensor, labels: torch.Tensor) -> dict[str, Any]:
    logits = head(features)
    predictions = logits.argmax(dim=1)
    counts = confusion_counts(labels, predictions)
    tn, fp = counts["true_negative"], counts["false_positive"]
    fn, tp = counts["false_negative"], counts["true_positive"]
    sensitivity = safe_ratio(tp, tp + fn)
    specificity = safe_ratio(tn, tn + fp)
    precision = safe_ratio(tp, tp + fp)
    metrics = {
        "sample_count": int(labels.numel()),
        "class_counts": {"non_fractured": int((labels == 0).sum()), "fractured": int((labels == 1).sum())},
        "metrics_valid": bool((labels == 0).any() and (labels == 1).any()),
        "accuracy": safe_ratio(tp + tn, labels.numel()),
        "balanced_accuracy": (sensitivity + specificity) / 2,
        "sensitivity": sensitivity,
        "specificity": specificity,
        "precision": precision,
        "f1": safe_ratio(2 * precision * sensitivity, precision + sensitivity),
        "confusion_matrix": [[tn, fp], [fn, tp]],
    }
    return metrics


def train_head(
    train_features: torch.Tensor,
    train_labels: torch.Tensor,
    valid_features: torch.Tensor,
    valid_labels: torch.Tensor,
    epochs: int,
) -> tuple[nn.Module, list[dict[str, Any]], int]:
    head = nn.Sequential(nn.Dropout(p=0.2), nn.Linear(train_features.shape[1], 2))
    counts = torch.bincount(train_labels, minlength=2).to(torch.float32)
    weights = counts.sum() / (2 * counts.clamp_min(1))
    criterion = nn.CrossEntropyLoss(weight=weights)
    optimizer = torch.optim.AdamW(head.parameters(), lr=3e-3, weight_decay=1e-3)
    generator = torch.Generator().manual_seed(SEED)
    dataset = torch.utils.data.TensorDataset(train_features, train_labels)
    loader = DataLoader(dataset, batch_size=128, shuffle=True, generator=generator)
    best_state: dict[str, torch.Tensor] | None = None
    best_epoch = 0
    best_score = -1.0
    history: list[dict[str, Any]] = []
    patience = 8
    stale = 0
    for epoch in range(1, epochs + 1):
        head.train()
        total_loss = 0.0
        seen = 0
        for features, labels in loader:
            optimizer.zero_grad(set_to_none=True)
            logits = head(features)
            loss = criterion(logits, labels)
            loss.backward()
            optimizer.step()
            total_loss += float(loss.item()) * labels.numel()
            seen += labels.numel()
        head.eval()
        metrics = evaluate_head(head, valid_features, valid_labels)
        score = float(metrics["balanced_accuracy"])
        history.append({"epoch": epoch, "train_loss": total_loss / seen, "validation": metrics})
        print(
            f"epoch={epoch:02d} loss={total_loss / seen:.4f} "
            f"val_balanced_accuracy={score:.4f} val_f1={metrics['f1']:.4f}",
            flush=True,
        )
        if score > best_score + 1e-6:
            best_score = score
            best_epoch = epoch
            best_state = {key: value.detach().clone() for key, value in head.state_dict().items()}
            stale = 0
        else:
            stale += 1
            if stale >= patience:
                break
    if best_state is None:
        raise RuntimeError("Training did not produce a model state")
    head.load_state_dict(best_state)
    return head, history, best_epoch


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the fixed FracAtlas engineering-demo classifier")
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--backbone-weights", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()

    dataset_root = args.dataset_root.resolve(strict=True)
    backbone_weights = args.backbone_weights.resolve(strict=True)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    random.seed(SEED)
    torch.manual_seed(SEED)
    torch.set_num_threads(min(8, max(1, torch.get_num_threads())))
    torch.use_deterministic_algorithms(True)

    labels = read_dataset_labels(dataset_root)
    official_split_files = locate_split_files(dataset_root)
    official_split_ids = {name: read_split_ids(path) for name, path in official_split_files.items()}
    official_class_counts = {
        name: Counter(labels[item.casefold()] for item in ids)
        for name, ids in official_split_ids.items()
    }
    official_split_is_binary = all(set(counts) == {0, 1} for counts in official_class_counts.values())
    official_split_covers_dataset = len({
        item.casefold() for values in official_split_ids.values() for item in values
    }) == len(labels)
    if official_split_is_binary and official_split_covers_dataset:
        split_ids = official_split_ids
        split_policy = "official image-level train/valid/test"
    else:
        split_ids = deterministic_stratified_splits(labels)
        split_policy = "deterministic stratified image-level 70/15/15 generated by MedTrust"
    split_sets = {name: {item.casefold() for item in values} for name, values in split_ids.items()}
    if split_sets["train"] & split_sets["valid"] or split_sets["train"] & split_sets["test"] or split_sets["valid"] & split_sets["test"]:
        raise RuntimeError("Official FracAtlas splits overlap")
    image_index = build_image_index(dataset_root)
    truncated_image_ids = audit_image_integrity(image_index)
    if truncated_image_ids:
        print(f"truncated_images_loaded_with_pillow_tolerance={len(truncated_image_ids)}", flush=True)

    enum_weights = MobileNet_V3_Small_Weights.IMAGENET1K_V1
    transform = enum_weights.transforms()
    backbone = mobilenet_v3_small(weights=None)
    state = torch.load(backbone_weights, map_location="cpu", weights_only=True)
    backbone.load_state_dict(state, strict=True)
    backbone.eval()
    for parameter in backbone.parameters():
        parameter.requires_grad_(False)

    datasets = {
        name: FracAtlasSplit(ids, labels, image_index, transform)
        for name, ids in split_ids.items()
    }
    loaders = {
        name: DataLoader(dataset, batch_size=args.batch_size, shuffle=False, num_workers=0)
        for name, dataset in datasets.items()
    }
    embeddings: dict[str, tuple[torch.Tensor, torch.Tensor, list[str]]] = {}
    for name in ("train", "valid", "test"):
        print(f"extracting_{name}_embeddings={len(datasets[name])}", flush=True)
        embeddings[name] = extract_embeddings(backbone, loaders[name])

    head, history, best_epoch = train_head(
        embeddings["train"][0],
        embeddings["train"][1],
        embeddings["valid"][0],
        embeddings["valid"][1],
        args.epochs,
    )
    backbone.classifier = head
    model_path = output_dir / "fracatlas_mobilenet_v3_small_v1.pt"
    torch.save(backbone.state_dict(), model_path)

    evaluation = {
        "schema_version": "medtrust.model-evaluation/v1",
        "model_id": MODEL_ID,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "validation": evaluate_head(head, embeddings["valid"][0], embeddings["valid"][1]),
        "test": evaluate_head(head, embeddings["test"][0], embeddings["test"][1]),
        "best_epoch": best_epoch,
        "evidence_scope": "image_level_technical_validation",
        "limitations": [
            "FracAtlas does not expose patient identifiers; the deterministic split is image-level, so patient-level leakage cannot be excluded.",
            "The classifier head was trained locally on frozen ImageNet features and has not been externally or clinically validated.",
            "Results apply only to the downloaded FracAtlas v6 files and the recorded immutable split lists.",
        ],
    }
    evaluation_path = output_dir / "evaluation.json"
    write_json(evaluation_path, evaluation)
    write_json(output_dir / "training_history.json", history)

    split_inventory = {
        "schema_version": "medtrust.dataset-split-inventory/v1",
        "dataset": "FracAtlas",
        "dataset_version": "v6",
        "split_source": split_policy,
        "split_scope": "image_level",
        "image_integrity_audit": {
            "truncated_image_count": len(truncated_image_ids),
            "truncated_image_ids": truncated_image_ids,
            "handling": "decoded with Pillow LOAD_TRUNCATED_IMAGES after explicit audit",
        },
        "official_fracture_split_audit": {
            "used_for_classifier_training": split_policy.startswith("official"),
            "counts": {
                name: {str(label): count for label, count in sorted(counts.items())}
                for name, counts in official_class_counts.items()
            },
            "reason_not_used": None if split_policy.startswith("official") else (
                "The bundled Fracture Split covers fractured-positive images only and cannot validate a binary classifier."
            ),
        },
        "splits": {
            name: {
                "count": len(ids),
                "class_counts": dict(Counter(labels[item.casefold()] for item in ids)),
                "source_file": (
                    str(official_split_files[name].relative_to(dataset_root)).replace("\\", "/")
                    if split_policy.startswith("official") else None
                ),
                "id_list_sha256": hashlib.sha256(
                    ("\n".join(ids) + "\n").encode("utf-8")
                ).hexdigest(),
            }
            for name, ids in split_ids.items()
        },
    }
    split_path = output_dir / "split_inventory.json"
    write_json(split_path, split_inventory)

    manifest_path = output_dir / "model_manifest.json"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "model_id": MODEL_ID,
        "version": "1.0.0",
        "task": "fracture_presence_image_classification",
        "disease_domain": "musculoskeletal_fracture",
        "modality": "xray",
        "architecture": "mobilenet_v3_small_frozen_backbone_linear_head",
        "classes": ["non_fractured", "fractured"],
        "input": {"color_space": "RGB", "transform": str(enum_weights.transforms())},
        "weights": {
            "path": model_path.name,
            "sha256": sha256_file(model_path),
            "bytes": model_path.stat().st_size,
        },
        "training": {
            "seed": SEED,
            "dataset": "FracAtlas v6",
            "split_policy": split_policy,
            "frozen_backbone_source_sha256": sha256_file(backbone_weights),
            "best_epoch": best_epoch,
        },
        "evidence": {
            "evaluation_path": evaluation_path.name,
            "evaluation_sha256": sha256_file(evaluation_path),
            "split_inventory_path": split_path.name,
            "split_inventory_sha256": sha256_file(split_path),
            "level": "image_level_technical_validation",
        },
        "readiness": {
            "catalog_registered": False,
            "executor_registered": False,
            "application_eligible": False,
            "compute_eligible": False,
            "clinical_use": False,
            "hard_isolation": False,
        },
        "license_notes": {
            "dataset": "FracAtlas v6 is distributed under CC BY 4.0; retain source attribution.",
            "backbone": "TorchVision pretrained weights retain their upstream terms and documented training recipe.",
            "local_head": "Generated by the MedTrust engineering validation script.",
        },
    }
    write_json(manifest_path, manifest)
    print(json.dumps({"ready": True, "manifest": str(manifest_path), "evaluation": evaluation}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
