from __future__ import annotations

import json
import os
import platform
import sys
import time

import numpy
import psutil
import torch


def main() -> None:
    assert sys.version_info[:3] == (3, 12, 13)
    assert torch.__version__ == "2.13.0+cpu"
    assert numpy.__version__ == "2.3.5"
    assert psutil.__version__ == "7.2.2"
    assert torch.version.cuda is None
    assert torch.cuda.is_available() is False
    assert platform.system() == "Linux"
    assert platform.machine() == "x86_64"
    torch.manual_seed(20260726)
    layer = torch.nn.Linear(4, 2).eval()
    value = torch.arange(8, dtype=torch.float32).reshape(2, 4)
    started = time.perf_counter()
    with torch.inference_mode():
        first = layer(value)
        second = layer(value)
    assert torch.equal(first, second)
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "torch": torch.__version__,
                "numpy": numpy.__version__,
                "psutil": psutil.__version__,
                "cuda": torch.version.cuda,
                "cuda_available": torch.cuda.is_available(),
                "device": str(first.device),
                "runtime_user_non_root": os.geteuid() != 0,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "output_digest": torch.sum(first).item(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
