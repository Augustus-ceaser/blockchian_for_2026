from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch
from PIL import Image, ImageFile
from torch import nn
from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

from train_fracatlas_classifier import build_image_index, read_dataset_labels, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify and run the fixed FracAtlas engineering model")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    args = parser.parse_args()

    model_dir = args.model_dir.resolve(strict=True)
    dataset_root = args.dataset_root.resolve(strict=True)
    manifest_path = model_dir / "model_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    weights_path = model_dir / manifest["weights"]["path"]
    evaluation_path = model_dir / manifest["evidence"]["evaluation_path"]
    split_path = model_dir / manifest["evidence"]["split_inventory_path"]
    checks = {
        "weights_sha256": sha256_file(weights_path) == manifest["weights"]["sha256"],
        "evaluation_sha256": sha256_file(evaluation_path) == manifest["evidence"]["evaluation_sha256"],
        "split_inventory_sha256": sha256_file(split_path) == manifest["evidence"]["split_inventory_sha256"],
    }
    if not all(checks.values()):
        raise RuntimeError(f"Manifest verification failed: {checks}")

    model = mobilenet_v3_small(weights=None)
    in_features = model.classifier[0].in_features
    model.classifier = nn.Sequential(nn.Dropout(p=0.2), nn.Linear(in_features, 2))
    model.load_state_dict(torch.load(weights_path, map_location="cpu", weights_only=True), strict=True)
    model.eval()
    transform = MobileNet_V3_Small_Weights.IMAGENET1K_V1.transforms()
    labels = read_dataset_labels(dataset_root)
    image_index = build_image_index(dataset_root)
    selected = {
        label: next(image_id for image_id in sorted(labels) if labels[image_id] == label)
        for label in (0, 1)
    }
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    samples = []
    with torch.inference_mode():
        for expected_label, image_id in selected.items():
            with Image.open(image_index[image_id]) as image:
                inputs = transform(image.convert("RGB")).unsqueeze(0)
            probabilities = model(inputs).softmax(dim=1)[0]
            samples.append({
                "public_image_id": image_id,
                "expected_label": expected_label,
                "predicted_label": int(probabilities.argmax().item()),
                "probabilities": [round(float(value), 6) for value in probabilities],
            })
    print(json.dumps({
        "verified": True,
        "checks": checks,
        "evidence_scope": manifest["evidence"]["level"],
        "samples": samples,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
