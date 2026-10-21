from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app  # noqa: E402
import batch_test  # noqa: E402


class LikeFlowSmokeTests(unittest.TestCase):
    def test_adb_directory_resolves_executable(self) -> None:
        adb_dir = Path(r"F:\模拟器\LDPlayer14")
        if adb_dir.is_dir():
            executable = app.adb_executable(str(adb_dir))
            self.assertIsNotNone(executable)
            self.assertEqual(executable.name.lower(), "adb.exe")

    def test_batch_accepts_optional_liked_template(self) -> None:
        original = sys.argv
        try:
            sys.argv = [
                "batch_test.py",
                "--template",
                str(ROOT / "templates" / "unliked_heart.png"),
                "--rounds",
                "2",
                "--per-round",
                "3",
                "--min-total-clicks",
                "10",
            ]
            args = batch_test.parse_args()
            self.assertIsNone(args.liked_template)
        finally:
            sys.argv = original

    def test_frozen_worker_uses_executable_worker_mode(self) -> None:
        original = sys.argv
        try:
            sys.argv = [
                "batch_test.py",
                "--template",
                str(ROOT / "templates" / "unliked_heart.png"),
            ]
            args = batch_test.parse_args()
            command = batch_test.build_worker_command(
                args,
                ROOT / "artifacts" / "test-round",
                frozen=True,
            )
            self.assertEqual(command[1], "--run-like")
            self.assertNotIn("qq_like_test.py", command)
        finally:
            sys.argv = original


if __name__ == "__main__":
    unittest.main()
