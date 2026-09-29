import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import claude_usage_statusline as cu  # noqa: E402

NOW = 1_800_000_000

SAMPLE = {
    "model": {"display_name": "Opus"},
    "rate_limits": {
        "five_hour": {"used_percentage": 23.5, "resets_at": NOW + 3600},
        "seven_day": {"used_percentage": 41.2, "resets_at": NOW + 86400},
    },
}


class ExtractTest(unittest.TestCase):
    def test_remaining_is_100_minus_used(self):
        usage = cu.extract_usage(SAMPLE, now=NOW)
        self.assertEqual(usage["five_hour"]["remaining_percentage"], 76.5)
        self.assertEqual(usage["seven_day"]["remaining_percentage"], 58.8)
        self.assertEqual(usage["updated_at"], NOW)

    def test_no_rate_limits(self):
        # API 従量課金や、セッション最初の応答前は rate_limits が来ない
        usage = cu.extract_usage({"model": {}}, now=NOW)
        self.assertNotIn("five_hour", usage)
        self.assertNotIn("seven_day", usage)

    def test_only_one_window(self):
        usage = cu.extract_usage(
            {"rate_limits": {"seven_day": {"used_percentage": 10}}}, now=NOW
        )
        self.assertIn("seven_day", usage)
        self.assertNotIn("five_hour", usage)

    def test_clamped_to_0_100(self):
        usage = cu.extract_usage(
            {"rate_limits": {"five_hour": {"used_percentage": 120}}}, now=NOW
        )
        self.assertEqual(usage["five_hour"]["remaining_percentage"], 0.0)


class SaveLoadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "sub" / "claude_usage.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_round_trip(self):
        cu.save_usage(cu.extract_usage(SAMPLE, now=NOW), self.path)
        loaded = cu.load_usage(self.path, now=NOW)
        self.assertEqual(loaded["five_hour"]["remaining_percentage"], 76.5)
        self.assertEqual(loaded["seven_day"]["remaining_percentage"], 58.8)

    def test_partial_update_keeps_other_window(self):
        cu.save_usage(cu.extract_usage(SAMPLE, now=NOW), self.path)
        cu.save_usage(
            cu.extract_usage(
                {"rate_limits": {"five_hour": {"used_percentage": 50, "resets_at": NOW + 10}}},
                now=NOW + 5,
            ),
            self.path,
        )
        loaded = cu.load_usage(self.path, now=NOW + 5)
        self.assertEqual(loaded["five_hour"]["remaining_percentage"], 50.0)
        self.assertEqual(loaded["seven_day"]["remaining_percentage"], 58.8)

    def test_missing_or_broken_file(self):
        self.assertIsNone(cu.load_usage(self.path))
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{壊れた", encoding="utf-8")
        self.assertIsNone(cu.load_usage(self.path))

    def test_past_reset_time_counts_as_full(self):
        cu.save_usage(cu.extract_usage(SAMPLE, now=NOW), self.path)
        loaded = cu.load_usage(self.path, now=NOW + 7200)  # 5時間枠のリセット後
        self.assertEqual(loaded["five_hour"]["remaining_percentage"], 100.0)
        self.assertTrue(loaded["five_hour"]["reset"])
        self.assertEqual(loaded["seven_day"]["remaining_percentage"], 58.8)


class FormatTest(unittest.TestCase):
    def test_dashboard_labels(self):
        usage = cu.extract_usage(SAMPLE, now=NOW)
        self.assertEqual(cu.format_remaining(usage, "seven_day"), "Claude週間枠 残り 59%")
        self.assertEqual(cu.format_remaining(usage, "five_hour"), "Claude 5時間枠 残り 76%")
        self.assertEqual(cu.format_remaining(None, "seven_day"), "Claude週間枠 残り 未取得")


class CommandTest(unittest.TestCase):
    def run_script(self, stdin, path):
        env = dict(os.environ, CLAUDE_USAGE_FILE=str(path))
        return subprocess.run(
            [sys.executable, str(ROOT / "tools" / "claude_usage_statusline.py")],
            input=stdin, capture_output=True, text=True, encoding="utf-8", env=env,
        )

    def test_writes_file_and_prints(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "claude_usage.json"
            result = self.run_script(json.dumps(SAMPLE), path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("5時間枠 残り76%", result.stdout)
            self.assertIn("週間枠 残り59%", result.stdout)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["seven_day"]["remaining_percentage"], 58.8)

    def test_bad_input_does_not_crash_or_write(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "claude_usage.json"
            result = self.run_script("not json", path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("未取得", result.stdout)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
