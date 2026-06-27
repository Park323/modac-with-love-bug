from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class DependencyStatus:
    package: str
    import_name: str
    available_before: bool
    attempted_install: bool
    available_after: bool
    command: list[str]
    returncode: Optional[int]
    error: str


def is_import_available(import_name: str) -> bool:
    return importlib.util.find_spec(import_name) is not None


def install_package(
    package: str,
    import_name: Optional[str] = None,
    *,
    user: bool = False,
    upgrade: bool = False,
    dry_run: bool = False,
) -> DependencyStatus:
    """
    Explicit optional dependency installer.

    This function intentionally installs only when called by a CLI flag or by a
    script that the user explicitly runs. Library imports should not trigger pip.
    """
    import_name = import_name or package.replace("-", "_")
    before = is_import_available(import_name)

    cmd = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        cmd.append("--upgrade")
    if user:
        cmd.append("--user")
    cmd.append(package)

    if before:
        return DependencyStatus(package, import_name, before, False, True, cmd, None, "")

    if dry_run:
        return DependencyStatus(package, import_name, before, False, before, cmd, None, "dry_run")

    try:
        proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
        after = is_import_available(import_name)
        err = ""
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "pip install failed").strip()[-2000:]
        return DependencyStatus(package, import_name, before, True, after, cmd, proc.returncode, err)
    except Exception as exc:  # pragma: no cover
        return DependencyStatus(package, import_name, before, True, False, cmd, None, str(exc))


def ensure_easyocr(*, user: bool = False, upgrade: bool = False, dry_run: bool = False) -> DependencyStatus:
    return install_package("easyocr", "easyocr", user=user, upgrade=upgrade, dry_run=dry_run)


def ensure_paddleocr(*, user: bool = False, upgrade: bool = False, dry_run: bool = False) -> DependencyStatus:
    return install_package("paddleocr", "paddleocr", user=user, upgrade=upgrade, dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Install optional dependencies for CrossFire QA MVP.")
    parser.add_argument("--easyocr", action="store_true", help="install easyocr fallback OCR package")
    parser.add_argument("--paddleocr", action="store_true", help="install paddleocr OCR package")
    parser.add_argument("--user", action="store_true", help="pip install --user")
    parser.add_argument("--upgrade", action="store_true", help="pip install --upgrade")
    parser.add_argument("--dry-run", action="store_true", help="print the command without installing")
    args = parser.parse_args()

    results: list[DependencyStatus] = []
    if args.easyocr:
        results.append(ensure_easyocr(user=args.user, upgrade=args.upgrade, dry_run=args.dry_run))
    if args.paddleocr:
        results.append(ensure_paddleocr(user=args.user, upgrade=args.upgrade, dry_run=args.dry_run))

    if not results:
        parser.error("Nothing to install. Use --easyocr or --paddleocr.")

    print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2))

    failed = [r for r in results if not r.available_after and not args.dry_run]
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
