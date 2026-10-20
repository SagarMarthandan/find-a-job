from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from find_a_job import api
from find_a_job.browser import BrowserSession, UnsafeUrlError


class TaskStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        self.paths = patch.multiple(
            api,
            DATA_DIR=data_dir,
            TOKEN_FILE=data_dir / "session-token",
            DB_FILE=data_dir / "tasks.sqlite3",
        )
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.addCleanup(self.temp_dir.cleanup)
        api._initialize()

    def test_claim_waits_when_queue_empty_or_github_still_pending(self) -> None:
        request = api.ClaimRequest(worker_id="waiting-host")
        self.assertEqual(api.claim(request, None), {"task": None})
        stamp = api.now()
        with api._db() as connection:
            connection.execute(
                "INSERT INTO tasks(id,kind,status,title,input_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                ("github-pending", "browser", "queued", "GitHub", "{}", stamp, stamp),
            )
            connection.execute(
                "INSERT INTO tasks(id,kind,status,title,input_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                ("intake-pending", "profile", "waiting_for_harness", "Intake",
                 '{"intake":true,"sources":[{"github_task_id":"github-pending"}]}', stamp, stamp),
            )
        self.assertEqual(api.claim(request, None), {"task": None})
        with api._db() as connection:
            row = connection.execute("SELECT status,worker_id FROM tasks WHERE id='intake-pending'").fetchone()
        self.assertEqual((row["status"], row["worker_id"]), ("waiting_for_harness", None))

    def add_running_profile(self, task_id: str = "profile-task", worker_id: str = "owner") -> None:
        stamp = api.now()
        with api._db() as connection:
            connection.execute(
                "INSERT INTO tasks(id,kind,status,title,input_json,worker_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (task_id, "profile", "running", "Synthetic profile", '{"text":"Ada Quinn uses Python"}', worker_id, stamp, stamp),
            )

    def test_cancelled_task_rejects_late_worker_completion(self) -> None:
        self.add_running_profile()
        cancelled = api.cancel_task("profile-task", None)
        self.assertEqual(cancelled["status"], "cancelled")

        with self.assertRaises(HTTPException) as raised:
            api.complete(
                "profile-task",
                api.CompleteRequest(
                    worker_id="owner",
                    result={
                        "name": "Ada Quinn",
                        "skills": ["Python"],
                        "experience": [],
                        "projects": [],
                        "education": [],
                        "evidence": [{"id": "e1", "text": "Ada Quinn uses Python", "source": "synthetic CV"}],
                    },
                ),
                None,
            )

        self.assertEqual(raised.exception.status_code, 409)
        with api._db() as connection:
            status = connection.execute("SELECT status FROM tasks WHERE id='profile-task'").fetchone()[0]
        self.assertEqual(status, "cancelled")

    def test_completion_rejects_wrong_claim_owner_without_state_change(self) -> None:
        self.add_running_profile()
        with self.assertRaises(HTTPException) as raised:
            api.complete(
                "profile-task",
                api.CompleteRequest(worker_id="other-worker", result={}),
                None,
            )

        self.assertEqual(raised.exception.status_code, 409)
        with api._db() as connection:
            row = connection.execute("SELECT status,worker_id FROM tasks WHERE id='profile-task'").fetchone()
        self.assertEqual((row["status"], row["worker_id"]), ("running", "owner"))

    def test_profile_facts_must_be_supported_by_source_quotes(self) -> None:
        evidence = [{"id": "e1", "text": "Ada Quinn uses Python", "source": "synthetic CV"}]
        valid = {
            "name": "Ada Quinn",
            "skills": ["Python"],
            "experience": [],
            "projects": [],
            "education": [],
            "evidence": evidence,
        }
        result = api._profile_output(valid, ["Ada Quinn uses Python"])
        self.assertFalse(result["evidence"][0]["confirmed"])

        invalid = {**valid, "skills": ["Python", "Invented technology"]}
        with self.assertRaises(HTTPException) as raised:
            api._profile_output(invalid, ["Ada Quinn uses Python"])
        self.assertEqual(raised.exception.status_code, 422)

    def test_generic_profile_creation_requires_source_aware_intake(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            api._create_task("profile", "Synthetic profile", {"text": "Ada Quinn"})
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("POST /api/intakes", raised.exception.detail)

    def test_intake_evidence_must_match_its_attributed_source(self) -> None:
        sources = [
            {"id": "resume-1", "kind": "resume", "label": "cv.md", "status": "ready", "text": "Ada Quinn uses Python."},
            {"id": "catalog-1", "kind": "catalog", "label": "work.md", "status": "ready", "text": "Ada built a data tool."},
        ]
        profile = {
            "name": "Ada Quinn",
            "skills": [],
            "experience": [],
            "projects": [],
            "education": [],
            "evidence": [{"id": "e1", "text": "Ada built a data tool.", "source": "resume-1"}],
        }
        with self.assertRaises(HTTPException) as raised:
            api._validate_intake_result(profile, sources)
        self.assertEqual(raised.exception.status_code, 422)

    def test_intake_rejects_invented_claim_and_requires_source_error_ack(self) -> None:
        ready = {"id": "resume-1", "kind": "resume", "label": "cv.md", "status": "ready", "text": "Ada Quinn uses Python."}
        failed = {"id": "catalog-1", "kind": "catalog", "label": "broken.bin", "status": "failed", "text": "", "error": "Unsupported format"}
        sources = [ready, failed]
        profile = {
            "name": "Ada Quinn",
            "skills": ["Invented framework"],
            "experience": [],
            "projects": [],
            "education": [],
            "evidence": [{"id": "e1", "text": "Ada Quinn uses Python.", "source": "resume-1"}],
        }
        with self.assertRaises(HTTPException):
            api._validate_intake_result(profile, sources)

        profile["skills"] = ["Python"]
        task = api._create_intake("Synthetic intake", sources)
        result = api._validate_intake_result(profile, sources)
        with api._db() as connection:
            connection.execute(
                "UPDATE tasks SET status='completed',result_json=? WHERE id=?",
                (api.json.dumps(result), task["id"]),
            )
        with self.assertRaises(HTTPException) as raised:
            api.approve_intake(task["id"], {}, None)
        self.assertEqual(raised.exception.status_code, 422)
        approved = api.approve_intake(task["id"], {"acknowledge_source_errors": True}, None)
        self.assertEqual(approved["result"]["acknowledged_source_errors"], ["catalog-1"])

    def test_intake_approval_waits_for_pending_github_source(self) -> None:
        sources = [
            {"id": "resume-1", "kind": "resume", "label": "cv.md", "status": "ready", "text": "Ada Quinn uses Python."},
            {"id": "github-1", "kind": "github", "label": "https://github.com/octocat/Hello-World", "format": "html", "status": "pending", "text": "", "url": "https://github.com/octocat/Hello-World"},
        ]
        profile = {
            "name": "Ada Quinn",
            "skills": ["Python"],
            "experience": [],
            "projects": [],
            "education": [],
            "evidence": [{"id": "e1", "text": "Ada Quinn uses Python.", "source": "resume-1"}],
        }
        task = api._create_intake("Pending GitHub", sources)
        result = api._validate_intake_result(profile, sources)
        with api._db() as connection:
            connection.execute(
                "UPDATE tasks SET status='completed',result_json=? WHERE id=?",
                (api.json.dumps(result), task["id"]),
            )
        with self.assertRaises(HTTPException) as raised:
            api.approve_intake(task["id"], {}, None)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("Wait for all GitHub sources", raised.exception.detail)

    def test_correction_reopens_approved_intake_and_resume_gate(self) -> None:
        source = {"id": "resume-1", "kind": "resume", "label": "cv.md", "status": "ready", "text": "Ada Quinn uses Python."}
        profile = {
            "name": "Ada Quinn",
            "skills": ["Python"],
            "experience": [],
            "projects": [],
            "education": [],
            "evidence": [{"id": "e1", "text": "Ada Quinn uses Python.", "source": "resume-1"}],
        }
        task = api._create_intake("Synthetic intake", [source])
        result = api._validate_intake_result(profile, [source])
        with api._db() as connection:
            connection.execute(
                "UPDATE tasks SET status='completed',result_json=? WHERE id=?",
                (api.json.dumps(result), task["id"]),
            )
            connection.execute(
                "INSERT INTO tasks(id,kind,status,title,input_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                ("job-task", "browser", "completed", "Synthetic JD", '{"purpose":"research","url":"https://example.com"}', api.now(), api.now()),
            )
        api.approve_intake(task["id"], {}, None)
        edited = api.review_intake(task["id"], {"result": profile}, None)
        self.assertEqual(edited["result"]["review_status"], "pending")
        self.assertIsNone(edited["result"]["reviewed_at"])
        with self.assertRaises(HTTPException) as raised:
            api._resume_input({"profile_task_id": task["id"], "job_task_id": "job-task"})
        self.assertEqual(raised.exception.status_code, 422)

    def test_private_browser_url_is_rejected_before_navigation(self) -> None:
        session = BrowserSession(Path(self.temp_dir.name))
        try:
            with self.assertRaises(UnsafeUrlError):
                session._assert_public_url("http://127.0.0.1/")
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
