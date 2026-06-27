"""Run a trained localizer on a real radar crop.

Drop-in alternative to :func:`modac.localize.localize`: returns ``(x, y, yaw)``
in the same convention (x, y are the player position in full-map pixels; yaw is
degrees clockwise from north).

    from modac.localize_net.infer import Localizer
    loc = Localizer("checkpoints/best.pt")
    x, y, yaw = loc.predict_path("map_bound.png")
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import torch

from .dataset import _IMAGENET_MEAN, _IMAGENET_STD
from .model import build_model, decode


class Localizer:
    def __init__(self, checkpoint: str | Path, device: str = "auto", out_size: int = 200):
        self.device = self._pick_device(device)
        ck = torch.load(str(checkpoint), map_location=self.device)
        meta = ck.get("meta", {})
        self.W = int(meta.get("W", 164))   # patched_map.png dims
        self.H = int(meta.get("H", 487))
        self.out_size = out_size
        self.model = build_model(pretrained=False).to(self.device).eval()
        self.model.load_state_dict(ck["model"])

    @staticmethod
    def _pick_device(arg: str) -> torch.device:
        if arg != "auto":
            return torch.device(arg)
        if torch.cuda.is_available():
            return torch.device("cuda")
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")

    def _preprocess(self, bgr: np.ndarray) -> torch.Tensor:
        if bgr.shape[:2] != (self.out_size, self.out_size):
            bgr = cv2.resize(bgr, (self.out_size, self.out_size), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        rgb = (rgb - _IMAGENET_MEAN) / _IMAGENET_STD
        t = torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1)))
        return t.unsqueeze(0).to(self.device)

    @torch.no_grad()
    def predict(self, radar_bgr: np.ndarray) -> tuple[float, float, float]:
        out = self.model(self._preprocess(radar_bgr))
        x, y, yaw = decode(out, self.W, self.H)[0].tolist()
        return x, y, yaw

    def predict_path(self, radar_path: str | Path) -> tuple[float, float, float]:
        img = cv2.imread(str(radar_path), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"could not read radar: {radar_path}")
        return self.predict(img)


if __name__ == "__main__":
    import sys

    ck = sys.argv[1] if len(sys.argv) > 1 else "checkpoints/best.pt"
    radar = sys.argv[2] if len(sys.argv) > 2 else "map_bound.png"
    print(Localizer(ck).predict_path(radar))
