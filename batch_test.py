#!/usr/bin/env python3
"""Run bounded like-test rounds on an Android device."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import uiautomator2 as u2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run configurable like-test rounds on an Android device."
    )
    parser.add_argument(
        "--serial",
        help="ADB serial. Omit when exactly one Android device is connected.",
    )
    parser.add_argument(
        "--adb-dir",
        type=Path,
        default=Path("F:/模拟器/LDPlayer14"),
        help="Directory containing adb.exe.",
    )
    parser.add_argument(
        "--package-prefix",
        default="com.tencent.mobileqq",
        help="Foreground package prefix passed to the worker.",
    )
    parser.add_argument(
        "--template",
        type=Path,
        action="append",
        required=True,
        help="Unliked icon template. Repeat to match multiple templates.",
    )
    parser.add_argument(
        "--liked-template",
        type=Path,
        action="append",
        help="Optional liked-state icon. Repeat to verify multiple templates.",
    )
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--per-round", type=int, default=10)
    parser.add_argument(
        "--min-total-clicks",
        type=int,
        default=1450,
        help="Keep running extra rounds until this total is reached (default: 1450).",
    )
    parser.add_argument("--max-scrolls-per-round", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Reduce UI waits, skip step screenshots, and use fewer template scales.",
    )
    parser.add_argument(
        "--turbo",
        action="store_true",
        help="Template-only detection with minimal waits and live progress.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts/batch_test"),
    )
    args = parser.parse_args()

    if args.rounds < 1:
        parser.error("--rounds must be at least 1")
    if args.per_round < 1:
        parser.error("--per-round must be at least 1")
    if args.min_total_clicks < 1:
        parser.error("--min-total-clicks must be at least 1")
    if not 1 <= args.max_scrolls_per_round <= 50:
        parser.error("--max-scrolls-per-round must be between 1 and 50")
    for template in args.template:
        if not template.is_file():
            parser.error(f"template does not exist: {template}")
    for template in args.liked_template or []:
        if not template.is_file():
            parser.error(f"liked template does not exist: {template}")
    return args


def configure_adb_path(adb_dir: Path) -> None:
    adb = adb_dir / ("adb.exe" if os.name == "nt" else "adb")
    if not adb.is_file():
        raise FileNotFoundError(f"adb not found: {adb}")
    os.environ["PATH"] = f"{adb_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def find_round_result(round_dir: Path) -> Path:
    matches = sorted(
        round_dir.glob("*/result.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not matches:
        raise FileNotFoundError(f"No result.json under {round_dir}")
    return matches[0]


def build_worker_command(
    args: argparse.Namespace,
    round_dir: Path,
    *,
    frozen: bool | None = None,
) -> list[str]:
    """Build a worker command that also works inside a PyInstaller bundle."""
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if is_frozen:
        command = [sys.executable, "--run-like"]
    else:
        core_script = Path(__file__).with_name("qq_like_test.py")
        command = [sys.executable, str(core_script)]

    if args.serial:
        command.extend(["--serial", args.serial])
    if args.package_prefix:
        command.extend(["--package-prefix", args.package_prefix])
    for template in args.template:
        command.extend(["--template", str(template.resolve())])
    for template in args.liked_template or []:
        command.extend(["--liked-template", str(template.resolve())])
    command.extend(
        [
            "--max-actions",
            str(args.per_round),
            "--max-scrolls",
            str(args.max_scrolls_per_round),
            "--i-confirm-test-account",
            "--artifacts-dir",
            str(round_dir.resolve()),
        ]
    )
    if args.dry_run:
        command.append("--dry-run")
    if args.fast:
        command.append("--fast")
    if args.turbo:
        command.append("--turbo")
    return command


def run_worker(
    command: list[str],
    round_dir: Path,
    total_clicks: int,
    target_clicks: int,
) -> int:
    """Stream worker output and expose cumulative click progress to the GUI."""
    process = subprocess.Popen(
        command,
        cwd=Path(__file__).resolve().parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    output: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        output.append(line)
        if "Clicked action " in line:
            match = re.search(r"Clicked action (\d+)", line)
            if match:
                current = total_clicks + int(match.group(1))
                print(
                    f"total={current}/{target_clicks}",
                    flush=True,
                )
    return_code = process.wait()
    (round_dir / "console.log").write_text(
        "".join(output), encoding="utf-8", errors="replace"
    )
    return return_code


def main() -> int:
    args = parse_args()
    configure_adb_path(args.adb_dir)

    root = args.artifacts_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
    root.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(root / "batch.log", encoding="utf-8"),
        ],
    )
    log = logging.getLogger("qq-like-batch")

    device = u2.connect(args.serial)
    current = device.app_current()
    package = current.get("package", "")
    log.info("Foreground package: %s", package)

    summary = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "serial": args.serial,
        "package": package,
        "dry_run": args.dry_run,
        "fast": args.fast,
        "turbo": args.turbo,
        "rounds_requested": args.rounds,
        "per_round": args.per_round,
        "min_total_clicks": args.min_total_clicks,
        "rounds": [],
        "total_clicks": 0,
        "total_verified": 0,
        "target_reached": False,
        "stopped_reason": "",
    }
    stop_reason = "target_reached"
    no_progress_rounds = 0
    round_number = 0

    try:
        while True:
            round_number += 1
            round_dir = root / f"round_{round_number:02d}"
            round_dir.mkdir(parents=True, exist_ok=True)
            command = build_worker_command(args, round_dir)

            log.info(
                "Starting round %d (target total clicks: %d)",
                round_number,
                args.min_total_clicks,
            )
            return_code = run_worker(
                command,
                round_dir,
                summary["total_clicks"],
                args.min_total_clicks,
            )
            if return_code != 0:
                raise RuntimeError(
                    f"Round {round_number} exited with code {return_code}"
                )

            result_path = find_round_result(round_dir)
            round_result = json.loads(result_path.read_text(encoding="utf-8"))
            clicks = int(round_result.get("clicks", 0))
            verified = int(round_result.get("likes_verified", 0))
            summary["rounds"].append(
                {
                    "round": round_number,
                    "clicks": clicks,
                    "verified": verified,
                    "scrolls": int(round_result.get("scrolls", 0)),
                    "stopped_reason": round_result.get("stopped_reason", ""),
                    "result": str(result_path),
                }
            )
            summary["total_clicks"] += clicks
            summary["total_verified"] += verified
            log.info(
                "Round %d finished: clicks=%d, verified=%d, total=%d/%d",
                round_number,
                clicks,
                verified,
                summary["total_clicks"],
                args.min_total_clicks,
            )
            print(
                f"total={summary['total_clicks']}/{args.min_total_clicks}",
                flush=True,
            )

            if args.dry_run:
                if round_number >= args.rounds:
                    stop_reason = "dry_run_completed"
                    break
            else:
                target_reached = summary["total_clicks"] >= args.min_total_clicks
                if target_reached and round_number >= args.rounds:
                    summary["target_reached"] = True
                    stop_reason = "target_reached"
                    break

                if clicks == 0:
                    no_progress_rounds += 1
                    if no_progress_rounds >= 2:
                        stop_reason = "no_progress_before_target"
                        log.error(
                            "Stopping after two rounds without clicks before reaching "
                            "the target"
                        )
                        break
                else:
                    no_progress_rounds = 0
            time.sleep(0.1 if args.fast else 1.0)
    except KeyboardInterrupt:
        stop_reason = "interrupted"
        log.warning("Stopped by user")
    except Exception:
        stop_reason = "error"
        log.exception("Batch test failed")
        summary["stopped_reason"] = stop_reason
        summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
        (root / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 1

    target_reached = (
        args.dry_run or summary["total_clicks"] >= args.min_total_clicks
    )
    summary["target_reached"] = target_reached
    summary["stopped_reason"] = stop_reason
    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    (root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(
        "Batch finished: clicks=%d, verified=%d, artifacts=%s",
        summary["total_clicks"],
        summary["total_verified"],
        root.resolve(),
    )
    if not target_reached:
        log.error(
            "Target not reached: %d/%d clicks",
            summary["total_clicks"],
            args.min_total_clicks,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
