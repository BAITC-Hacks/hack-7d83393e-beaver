"""Data-level checks for DOCX export.

These checks exercise a protocol dictionary and therefore do not count as the
real-audio acceptance scenario from ACCEPTANCE.md.  The latter must be recorded
separately with the actual ASR and diarization models.
"""

from __future__ import annotations

import json
import sys
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

try:
    import docx  # noqa: F401
except ImportError:  # pragma: no cover - environment dependent
    HAS_DOCX = False
else:
    HAS_DOCX = True


def _load_example(name: str) -> dict:
    with (ROOT / "examples" / name).open(encoding="utf-8") as stream:
        return json.load(stream)


@unittest.skipUnless(HAS_DOCX, "python-docx не установлен")
class ExportDocxAcceptance(unittest.TestCase):
    def test_fixture_contains_protocol_sections_and_evidence(self) -> None:
        from services.export_docx import render

        payload = render(_load_example("01_mixed_deadline.json"))
        self.assertTrue(payload.startswith(b"PK"))
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        text = " ".join(root.itertext())
        self.assertIn("Синтетический пример, не ASR", text)
        self.assertIn("Поручения", text)
        self.assertIn("История", text)
        self.assertIn("u2", text)
        self.assertIn("00:07", text)
        self.assertIn("Данияр", text)

    def test_missing_fields_remain_unresolved_and_questions_are_exported(self) -> None:
        from services.export_docx import render

        payload = render(_load_example("02_ru_missing_fields.json"))
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            xml = archive.read("word/document.xml")
        text = " ".join(ElementTree.fromstring(xml).itertext())
        self.assertIn("ЧЕРНОВИК", text)
        self.assertIn("Кто отвечает", text)
        self.assertIn("К какому сроку", text)
        self.assertIn("—", text)

    def test_kazakh_letters_and_approved_marker(self) -> None:
        from services.export_docx import render

        protocol = _load_example("03_kk_no_new_task.json")
        protocol["approved"] = True
        protocol["meeting"]["title"] = "Ә Ғ Қ Ң Ө Ұ Ү Һ І"
        protocol["utterances"][0]["text"] = "Ә Ғ Қ Ң Ө Ұ Ү Һ І"
        payload = render(protocol)
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("УТВЕРЖДЁН", xml)
        for letter in "ӘҒҚҢӨҰҮҺІ":
            self.assertIn(letter, xml)


if __name__ == "__main__":
    unittest.main()
