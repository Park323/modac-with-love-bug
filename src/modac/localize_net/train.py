"""Train the ResNet50 minimap localizer on synthetic radar crops.

Example:
    python -m modac.localize_net.train \
        --full-map full_map_alpha.png --epochs 30 --batch 64 --out checkpoints

The synthetic dataset is effectively infinite, so an "epoch" is just
``--steps-per-epoch`` batches. Validation uses a fixed seed so the number is
comparable across runs. The best checkpoint (lowest val loss) is saved as
``best.pt``; ``last.pt`` is always overwritten.
"""

from __future__ import annotations

import argparse
import dataclasses
import time
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from .dataset import AugConfig, MinimapSynthDataset, SynthConfig
from .model import LossWeights, build_model, decode, localization_loss, set_train_mode
from .render import PATCHED_SPEC


def _loader(cfg: SynthConfig, batch: int, workers: int, *, train: bool) -> DataLoader:
    ds = MinimapSynthDataset(cfg, train=train)
    return DataLoader(
        ds, batch_size=batch, shuffle=False, num_workers=workers,
        pin_memory=True, drop_last=train, persistent_workers=workers > 0,
    )


def _device(arg: str) -> torch.device:
    if arg != "auto":
        return torch.device(arg)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


@torch.no_grad()
def evaluate(model, loader, device, W, H) -> dict[str, float]:
    model.eval()
    agg = {"loss": 0.0, "pos_loss": 0.0, "yaw_loss": 0.0, "yaw_err_deg": 0.0}
    px_err = 0.0
    n = 0
    for img, tgt in loader:
        img, tgt = img.to(device), tgt.to(device)
        out = model(img)
        _, m = localization_loss(out, tgt)
        for k in agg:
            agg[k] += m[k] * img.size(0)
        pred = decode(out, W, H)
        true = decode(tgt, W, H)
        px_err += torch.hypot(pred[:, 0] - true[:, 0], pred[:, 1] - true[:, 1]).sum().item()
        n += img.size(0)
    for k in agg:
        agg[k] /= n
    agg["pos_err_px"] = px_err / n
    return agg


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--map", default=PATCHED_SPEC.map_path, help="base map image")
    ap.add_argument("--zoom", type=float, default=PATCHED_SPEC.zoom[0],
                    help="uniform zoom (base-px per radar-px); calibrate vs real radar")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--steps-per-epoch", type=int, default=400)
    ap.add_argument("--val-steps", type=int, default=40)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--no-pretrained", action="store_true")
    ap.add_argument("--linear-probe", action="store_true",
                    help="freeze backbone, train only the linear head (LP)")
    ap.add_argument("--out", default="checkpoints")
    ap.add_argument("--resume", default=None)
    args = ap.parse_args()

    device = _device(args.device)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"device={device}  out={out_dir}")

    aug = AugConfig()
    spec = dataclasses.replace(PATCHED_SPEC, map_path=args.map, zoom=(args.zoom, args.zoom))
    train_cfg = SynthConfig(spec=spec, length=args.steps_per_epoch * args.batch, seed=12345, aug=aug)
    # Val: fixed seed -> stable, comparable metric (still uses the same aug pipeline).
    val_cfg = SynthConfig(spec=spec, length=args.val_steps * args.batch, seed=777, aug=aug)
    train_loader = _loader(train_cfg, args.batch, args.workers, train=True)
    val_loader = _loader(val_cfg, args.batch, max(0, args.workers // 2), train=False)
    W, H = train_loader.dataset.W, train_loader.dataset.H

    model = build_model(pretrained=not args.no_pretrained, linear_probe=args.linear_probe).to(device)
    trainable = [p for p in model.parameters() if p.requires_grad]
    n_train = sum(p.numel() for p in trainable)
    n_total = sum(p.numel() for p in model.parameters())
    print(f"trainable params: {n_train:,} / {n_total:,}"
          f"{'  (linear probe)' if args.linear_probe else ''}")
    opt = torch.optim.AdamW(trainable, lr=args.lr, weight_decay=args.weight_decay)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    use_amp = device.type == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp)
    weights = LossWeights()

    start_epoch, best_val = 0, float("inf")
    if args.resume:
        ck = torch.load(args.resume, map_location=device)
        model.load_state_dict(ck["model"])
        opt.load_state_dict(ck["opt"])
        start_epoch = ck.get("epoch", 0) + 1
        best_val = ck.get("best_val", best_val)
        print(f"resumed from {args.resume} @ epoch {start_epoch}")

    for epoch in range(start_epoch, args.epochs):
        set_train_mode(model, args.linear_probe)
        # Reseed each epoch so train samples differ epoch-to-epoch.
        train_loader.dataset.cfg.seed = 12345 + epoch * 7919
        t0 = time.time()
        run = 0.0
        for step, (img, tgt) in enumerate(train_loader):
            img, tgt = img.to(device, non_blocking=True), tgt.to(device, non_blocking=True)
            opt.zero_grad(set_to_none=True)
            with torch.autocast(device_type=device.type, enabled=use_amp):
                out = model(img)
                loss, m = localization_loss(out, tgt, weights)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            run += m["loss"]
            if step % 50 == 0:
                print(f"  e{epoch} s{step:4d}/{len(train_loader)} "
                      f"loss={m['loss']:.4f} yaw_err={m['yaw_err_deg']:.2f}deg")
        sched.step()

        val = evaluate(model, val_loader, device, W, H)
        dt = time.time() - t0
        print(f"[epoch {epoch}] train_loss={run/len(train_loader):.4f} "
              f"val_loss={val['loss']:.4f} val_pos_err={val['pos_err_px']:.1f}px "
              f"val_yaw_err={val['yaw_err_deg']:.2f}deg ({dt:.0f}s)")

        ckpt = {"model": model.state_dict(), "opt": opt.state_dict(),
                "epoch": epoch, "best_val": best_val,
                "meta": {"W": W, "H": H, "map": args.map, "zoom": args.zoom}}
        torch.save(ckpt, out_dir / "last.pt")
        if val["loss"] < best_val:
            best_val = val["loss"]
            ckpt["best_val"] = best_val
            torch.save(ckpt, out_dir / "best.pt")
            print(f"  ** new best val_loss={best_val:.4f} -> best.pt")

    print("done.")


if __name__ == "__main__":
    main()
