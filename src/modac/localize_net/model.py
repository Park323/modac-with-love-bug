"""ResNet50 regression model for minimap localization.

Backbone: torchvision ResNet50 (optionally ImageNet-pretrained). The final fc
is replaced with a 4-d head: ``[x_norm, y_norm, sin_yaw, cos_yaw]``.

The yaw is regressed as a (sin, cos) pair instead of a raw angle so the loss is
continuous across the 0/360 wrap. At decode time we L2-normalize the pair and
take ``atan2``.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import models


@dataclass
class LossWeights:
    position: float = 1.0   # weight on (x, y) regression
    yaw: float = 1.0        # weight on (sin, cos) regression


def build_model(pretrained: bool = True, linear_probe: bool = False) -> nn.Module:
    """ResNet50 with a 4-d regression head.

    ``linear_probe``: freeze the whole backbone and train only the new ``fc``
    head (classic LP). The backbone should also be kept in ``eval`` mode during
    training so its BatchNorm running stats stay frozen — see
    :func:`set_train_mode`.
    """
    weights = models.ResNet50_Weights.IMAGENET1K_V2 if pretrained else None
    net = models.resnet50(weights=weights)
    net.fc = nn.Linear(net.fc.in_features, 4)
    if linear_probe:
        for p in net.parameters():
            p.requires_grad = False
        for p in net.fc.parameters():
            p.requires_grad = True
    return net


def set_train_mode(model: nn.Module, linear_probe: bool) -> None:
    """Put the model in the right mode for an epoch of training.

    For linear probing, the frozen backbone stays in eval (frozen BN stats);
    only the head trains. Otherwise everything trains.
    """
    if linear_probe:
        model.eval()
        model.fc.train()
    else:
        model.train()


def split_outputs(out: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Return (position[:, :2], yaw_vec[:, 2:]) with the yaw pair L2-normalized."""
    pos = out[:, :2].contiguous()
    yaw_vec = F.normalize(out[:, 2:4], dim=1, eps=1e-6)
    return pos, yaw_vec


def localization_loss(
    out: torch.Tensor, target: torch.Tensor, w: LossWeights = LossWeights()
) -> tuple[torch.Tensor, dict[str, float]]:
    """SmoothL1 on position + MSE on the (sin, cos) yaw pair.

    Returns ``(loss, metrics)`` where metrics carries detached scalars for
    logging, including the mean yaw angular error in degrees.
    """
    pos, yaw_vec = split_outputs(out)
    tpos, tyaw = target[:, :2].contiguous(), target[:, 2:4].contiguous()

    pos_loss = F.smooth_l1_loss(pos, tpos)
    yaw_loss = F.mse_loss(yaw_vec, tyaw)
    loss = w.position * pos_loss + w.yaw * yaw_loss

    with torch.no_grad():
        pred_ang = torch.atan2(yaw_vec[:, 0], yaw_vec[:, 1])
        true_ang = torch.atan2(tyaw[:, 0], tyaw[:, 1])
        d = torch.rad2deg(torch.atan2(torch.sin(pred_ang - true_ang),
                                      torch.cos(pred_ang - true_ang))).abs().mean()
        metrics = {
            "loss": float(loss),
            "pos_loss": float(pos_loss),
            "yaw_loss": float(yaw_loss),
            "yaw_err_deg": float(d),
        }
    return loss, metrics


def decode(out: torch.Tensor, W: int, H: int) -> torch.Tensor:
    """Model output -> (B, 3) tensor of (x_px, y_px, yaw_deg)."""
    pos, yaw_vec = split_outputs(out)
    x = pos[:, 0] * W
    y = pos[:, 1] * H
    yaw = torch.rad2deg(torch.atan2(yaw_vec[:, 0], yaw_vec[:, 1])) % 360.0
    return torch.stack([x, y, yaw], dim=1)
