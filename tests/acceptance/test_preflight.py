"""Offline checks for the preflight report itself."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


class PreflightAcceptance(unittest.TestCase):
    def test_report_is_observational_and_marks_missing_weights(self) -> None:
        from scripts.preflight import run_preflight

        with tempfile.TemporaryDirectory() as directory:
            report = run_preflight(
                project_root=Path(directory),
                asr_model=Path(directory) / "missing-asr",
                diarization_model=Path(directory) / "missing-diarization",
                llm_model="model-that-is-not-installed",
            )
        self.assertTrue(report["offline"])
        self.assertFalse(report["ready"])
        self.assertIn("asr_weights", report["unavailable"])
        self.assertIn("diarization_weights", report["unavailable"])


if __name__ == "__main__":
    unittest.main()
