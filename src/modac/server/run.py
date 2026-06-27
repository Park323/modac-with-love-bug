"""CLI entrypoint: ``modac-server`` (or ``python -m modac.server.run``)."""

from __future__ import annotations

import argparse
import importlib

import uvicorn

from modac.policy.base import Policy
from modac.policy.random_policy import RandomPolicy


def load_policy(spec: str) -> Policy:
    """Resolve a policy from a spec string.

    'random'              -> built-in RandomPolicy
    'pkg.module:ClassName' -> imported and instantiated with no args
    """
    if spec == "random":
        return RandomPolicy()
    if ":" not in spec:
        raise SystemExit(
            f"Unknown policy '{spec}'. Use 'random' or 'pkg.module:ClassName'."
        )
    mod_name, cls_name = spec.split(":", 1)
    cls = getattr(importlib.import_module(mod_name), cls_name)
    return cls()


def main() -> None:
    p = argparse.ArgumentParser(description="Run the MODAC policy server.")
    p.add_argument("--policy", default="random", help="'random' or 'pkg.module:ClassName'")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()

    from modac.server.app import create_app

    app = create_app(load_policy(args.policy))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
