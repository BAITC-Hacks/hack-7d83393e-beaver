import unittest

from fastapi.testclient import TestClient

from app import app


class FixtureHttpFlow(unittest.TestCase):
    def test_fixture_audio_to_docx(self):
        client = TestClient(app)
        meeting = client.post(
            "/api/meetings",
            json={
                "title": "Синтетическая проверка",
                "started_at": "2026-09-23T10:00:00+06:00",
                "timezone": "Asia/Almaty",
                "mode": "FIXTURE",
                "participants": [{"id": "p1", "name": "Данияр"}, {"id": "p2", "name": "Айдана"}],
            },
        )
        self.assertEqual(meeting.status_code, 200)
        meeting_id = meeting.json()["id"]
        uploaded = client.post(
            f"/api/meetings/{meeting_id}/audio",
            files={"file": ("synthetic.wav", b"fixture bytes", "audio/wav")},
        )
        self.assertEqual(uploaded.status_code, 200)
        job = client.get(f"/api/jobs/{uploaded.json()['job_id']}").json()
        self.assertEqual(job["status"], "done")
        protocol = client.get(f"/api/meetings/{meeting_id}/protocol").json()
        self.assertEqual(protocol["mode"], "FIXTURE")
        self.assertTrue(protocol["utterances"])
        mapping = [
            {"speaker_id": "SPEAKER_00", "participant_id": "p1", "confirmed": True},
            {"speaker_id": "SPEAKER_01", "participant_id": "p2", "confirmed": True},
        ]
        self.assertEqual(client.put(f"/api/meetings/{meeting_id}/speaker-map", json={"speaker_map": mapping}).status_code, 200)
        extracted = client.post(f"/api/meetings/{meeting_id}/extract")
        self.assertEqual(extracted.status_code, 200)
        self.assertTrue(extracted.json()["tasks"])
        self.assertEqual(client.post(f"/api/meetings/{meeting_id}/approve").status_code, 200)
        docx = client.get(f"/api/meetings/{meeting_id}/export.docx")
        self.assertEqual(docx.status_code, 200)
        self.assertGreater(len(docx.content), 1000)


if __name__ == "__main__":
    unittest.main()
