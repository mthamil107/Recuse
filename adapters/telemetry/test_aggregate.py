#!/usr/bin/env python3
"""Unit tests for aggregate.py. Standard library only (unittest); no deps.

Run:
    python3 -m unittest test_aggregate -v
    python3 test_aggregate.py
"""

import os
import tempfile
import unittest

import aggregate


def _write(tmpdir, name, lines):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


class AggregateTest(unittest.TestCase):
    def test_bundled_sample(self):
        here = os.path.dirname(os.path.abspath(__file__))
        sample = os.path.join(here, "sample-telemetry.json")
        result = aggregate.aggregate([sample])

        # 6 telemetry events, 1 non-telemetry line skipped.
        self.assertEqual(result["events"], 6)
        self.assertEqual(result["skipped"], 1)

        # Emissions.
        self.assertEqual(result["emitted"][("ssh", "deny")], 2)
        self.assertEqual(result["emitted"][("postgres", "deny")], 1)
        self.assertEqual(result["emitted"][("kubernetes", "deny")], 1)
        self.assertEqual(result["emitted"][("kubernetes", "warn")], 1)

        # Withdrawal counted separately, not as an emission.
        self.assertEqual(result["withdrawn"][("postgres", "deny")], 1)
        self.assertNotIn(("postgres", "deny"), {})  # sanity

    def test_ignores_non_telemetry_and_garbage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "mixed.json", [
                '{"schema":"recuse.telemetry/v1","protocol":"ssh","directive":"deny","outcome":"emitted","count":1}',
                '{"schema":"something.else/v1","protocol":"ssh"}',   # wrong schema
                'not json at all',                                    # garbage
                '',                                                   # blank
                '{"no":"schema"}',                                    # missing schema
            ])
            result = aggregate.aggregate([path])
        self.assertEqual(result["events"], 1)
        self.assertEqual(result["emitted"][("ssh", "deny")], 1)
        # 3 non-blank, non-telemetry lines skipped (blank line is not counted).
        self.assertEqual(result["skipped"], 3)

    def test_unknown_tokens_are_folded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "weird.json", [
                # Unknown protocol/directive/outcome must be coerced, never
                # emitted verbatim into the report.
                '{"schema":"recuse.telemetry/v1","protocol":"telnet","directive":"nuke","outcome":"???","count":1}',
            ])
            result = aggregate.aggregate([path])
        # protocol -> "other", directive -> "other", outcome -> "emitted".
        self.assertEqual(result["emitted"][("other", "other")], 1)

    def test_count_defaults_and_sanitizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "counts.json", [
                '{"schema":"recuse.telemetry/v1","protocol":"ssh","directive":"deny","outcome":"emitted"}',        # no count -> 1
                '{"schema":"recuse.telemetry/v1","protocol":"ssh","directive":"deny","outcome":"emitted","count":5}',
                '{"schema":"recuse.telemetry/v1","protocol":"ssh","directive":"deny","outcome":"emitted","count":-3}',  # negative -> 1
                '{"schema":"recuse.telemetry/v1","protocol":"ssh","directive":"deny","outcome":"emitted","count":"x"}', # non-int -> 1
            ])
            result = aggregate.aggregate([path])
        # 1 + 5 + 1 + 1 = 8
        self.assertEqual(result["emitted"][("ssh", "deny")], 8)

    def test_multiple_files_summed(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = _write(tmp, "a.json", [
                '{"schema":"recuse.telemetry/v1","protocol":"ssh","directive":"deny","outcome":"emitted","count":1}',
            ])
            b = _write(tmp, "b.json", [
                '{"schema":"recuse.telemetry/v1","protocol":"ssh","directive":"deny","outcome":"emitted","count":1}',
            ])
            result = aggregate.aggregate([a, b])
        self.assertEqual(result["files"], 2)
        self.assertEqual(result["emitted"][("ssh", "deny")], 2)

    def test_report_renders(self):
        result = aggregate.aggregate([])
        text = aggregate.format_report(result)
        self.assertIn("Recuse telemetry summary", text)
        self.assertIn("(none)", text)


if __name__ == "__main__":
    unittest.main()
