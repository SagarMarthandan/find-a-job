from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

from find_a_job import api, cache


SOURCE_TEXT = (
    "Ada Example built Atlas Ledger in Python. Ada Example built Metrics Viewer with Python and SQLite; "
    "the full catalog records the export workflow, measured outcomes, and "
    "https://example.invalid/catalog/design."
)
UPLOAD_TEXT = "Ada Example built Atlas Ledger in Python.\n"
PROJECT_EVIDENCE = {
    "Atlas Ledger": "Ada Example built Atlas Ledger in Python.",
    "Metrics Viewer": "Ada Example built Metrics Viewer with Python and SQLite;",
}


def _profile(source_id: str, project: str = "Atlas Ledger") -> dict[str, object]:
    return {
        "name": "Ada Example",
        "skills": ["Python"],
        "experience": [],
        "projects": [project],
        "education": [],
        "evidence": [
            {"id": "evidence-1", "text": PROJECT_EVIDENCE[project], "source": source_id}
        ],
    }


def _python_environment() -> tuple[Path, dict[str, str]]:
    project_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    source_root = str(project_root / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_root, environment.get("PYTHONPATH", "")) if part
    )
    return project_root, environment


def _quoted_sql_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _matches_context_payload(payload: object, intake_id: str) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("intake_id") == intake_id
        and "sources" in payload
    )


def _context_payload(value: object, intake_id: str) -> dict[str, object] | None:
    if not isinstance(value, str):
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not _matches_context_payload(payload, intake_id):
        return None
    return payload


def _context_payload_cell(row: tuple[object, ...], intake_id: str) -> tuple[int, dict[str, object]] | None:
    for index, value in enumerate(row[1:]):
        payload = _context_payload(value, intake_id)
        if payload is not None:
            return index, payload
    return None


def _corrupt_saved_context(database: Path, intake_id: str) -> None:
    with sqlite3.connect(database) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for (table,) in tables:
            quoted_table = _quoted_sql_identifier(table)
            try:
                rows = connection.execute(f"SELECT rowid, * FROM {quoted_table}").fetchall()
            except sqlite3.OperationalError:
                continue
            columns = [
                row[1]
                for row in connection.execute(f"PRAGMA table_info({quoted_table})").fetchall()
            ]
            for row in rows:
                cell = _context_payload_cell(row, intake_id)
                if cell is None:
                    continue
                index, payload = cell
                payload["sources"] = None
                connection.execute(
                    f"UPDATE {quoted_table} SET {_quoted_sql_identifier(columns[index])}=? WHERE rowid=?",
                    (json.dumps(payload), row[0]),
                )
                return
    raise AssertionError("No serialized context payload was found to corrupt")


class CacheTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.data_dir = Path(self.temporary_directory.name)
        self.api_paths = patch.multiple(
            api,
            DATA_DIR=self.data_dir,
            TOKEN_FILE=self.data_dir / "session-token",
            DB_FILE=self.data_dir / "tasks.sqlite3",
        )
        self.api_paths.start()
        self.addCleanup(self.api_paths.stop)
        api._initialize()

    def _approve_sources(
        self, task_id: str, sources: list[dict[str, object]], profile: dict[str, object]
    ) -> dict[str, object]:
        result = api._validate_intake_result(profile, sources)
        with api._db() as connection:
            connection.execute(
                "UPDATE tasks SET status='completed', result_json=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), task_id),
            )
        return api.approve_intake(task_id, {}, None)["result"]

    def _create_approved_intake(
        self, text: str = SOURCE_TEXT, project: str = "Atlas Ledger"
    ) -> tuple[str, dict[str, object], dict[str, object]]:
        source = {
            "id": uuid.uuid4().hex,
            "kind": "catalog",
            "label": "synthetic-catalog.md",
            "format": "md",
            "status": "ready",
            "text": text,
        }
        task = api._create_intake("Synthetic intake", [source])
        approved = self._approve_sources(
            task["id"], [source], _profile(source["id"], project)
        )
        return task["id"], source, approved

    def _upload_intake(self, title: str) -> dict[str, object]:
        return asyncio.run(
            api.create_intake(
                resume_files=[
                    UploadFile(file=BytesIO(UPLOAD_TEXT.encode()), filename="synthetic.md")
                ],
                catalog_files=[],
                github_urls="[]",
                notes="",
                title=title,
                _=None,
            )
        )

    def _create_research_and_resume(self, intake_id: str) -> tuple[str, str]:
        research = api._create_task(
            "browser",
            "Synthetic job description",
            {"url": "https://example.invalid/job", "purpose": "research"},
        )
        with api._db() as connection:
            connection.execute(
                "UPDATE tasks SET status='completed', result_json=? WHERE id=?",
                (
                    json.dumps(
                        {
                            "url": "https://example.invalid/job",
                            "text": "Synthetic role requiring Python.",
                        }
                    ),
                    research["id"],
                ),
            )
        resume = api._create_task(
            "resume",
            "Synthetic resume",
            {"profile_task_id": intake_id, "job_task_id": research["id"]},
        )
        return research["id"], resume["id"]


class ExtractionCacheTests(CacheTestCase):
    def test_content_suffix_and_fresh_process_cache_behavior(self) -> None:
        calls: list[tuple[bytes, str]] = []

        def extract(data: bytes, suffix: str) -> str:
            calls.append((data, suffix))
            return data.decode("utf-8")

        original = b"synthetic source content"
        first = cache.extract_cached(self.data_dir, original, ".md", extract)
        self.assertFalse(first["cache_hit"])
        self.assertEqual(first["text"], original.decode())
        self.assertEqual(first["content_sha256"], hashlib.sha256(original).hexdigest())

        project_root, environment = _python_environment()
        script = """
import json, sys
from pathlib import Path
from find_a_job.cache import extract_cached

def unexpected_extraction(data, suffix):
    raise RuntimeError("persistent cache did not hit")

print(json.dumps(extract_cached(Path(sys.argv[1]), sys.argv[2].encode(), ".md", unexpected_extraction)))
"""
        process = subprocess.run(
            [sys.executable, "-c", script, str(self.data_dir), original.decode()],
            cwd=project_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        across_process = json.loads(process.stdout)
        self.assertTrue(across_process["cache_hit"])
        self.assertEqual(across_process["text"], first["text"])
        self.assertEqual(across_process["content_sha256"], first["content_sha256"])

        changed = cache.extract_cached(self.data_dir, original + b" changed", ".md", extract)
        different_suffix = cache.extract_cached(self.data_dir, original, ".txt", extract)
        same_suffix_casefolded = cache.extract_cached(self.data_dir, original, ".MD", extract)
        self.assertFalse(changed["cache_hit"])
        self.assertFalse(different_suffix["cache_hit"])
        self.assertTrue(same_suffix_casefolded["cache_hit"])
        self.assertEqual(len(calls), 3)

    def test_parser_fingerprint_change_invalidates_cached_text(self) -> None:
        data = b"same synthetic bytes"
        with patch.object(cache, "version", return_value="parser-set-one"):
            first = cache.extract_cached(self.data_dir, data, ".md", lambda *_: "source text")
        with patch.object(cache, "version", return_value="parser-set-two"):
            second = cache.extract_cached(self.data_dir, data, ".md", lambda *_: "source text")

        self.assertFalse(first["cache_hit"])
        self.assertFalse(second["cache_hit"])
        self.assertNotEqual(first["extractor_version"], second["extractor_version"])

    def test_failed_and_empty_extractions_are_not_cached(self) -> None:
        attempts = 0

        def fail_once(data: bytes, suffix: str) -> str:
            nonlocal attempts
            attempts += 1
            raise ValueError("synthetic extraction failure")

        with self.assertRaisesRegex(ValueError, "synthetic extraction failure"):
            cache.extract_cached(self.data_dir, b"failed source", ".md", fail_once)

        recovered = cache.extract_cached(
            self.data_dir, b"failed source", ".md", lambda *_: "recovered text"
        )
        self.assertFalse(recovered["cache_hit"])
        self.assertEqual(recovered["text"], "recovered text")

        empty = cache.extract_cached(self.data_dir, b"empty source", ".md", lambda *_: "")
        nonempty = cache.extract_cached(
            self.data_dir, b"empty source", ".md", lambda *_: "later text"
        )
        self.assertEqual(empty["text"], "")
        self.assertFalse(nonempty["cache_hit"])
        self.assertEqual(nonempty["text"], "later text")
        self.assertEqual(attempts, 1)

    def test_workspaces_do_not_share_extraction_results(self) -> None:
        second_workspace = Path(self.temporary_directory.name) / "candidate-two"
        first = cache.extract_cached(
            self.data_dir, b"same bytes", ".md", lambda *_: "candidate one text"
        )
        second = cache.extract_cached(
            second_workspace, b"same bytes", ".md", lambda *_: "candidate two text"
        )

        self.assertFalse(first["cache_hit"])
        self.assertFalse(second["cache_hit"])
        self.assertEqual(first["text"], "candidate one text")
        self.assertEqual(second["text"], "candidate two text")
        self.assertNotEqual(first["text"], second["text"])


class ContextCacheTests(CacheTestCase):
    def test_approved_context_caches_exact_profile_and_full_source(self) -> None:
        intake_id, source, approved_profile = self._create_approved_intake()

        first = cache.context_cached(self.data_dir, intake_id)
        second = cache.context_cached(self.data_dir, intake_id)
        refreshed = cache.context_cached(self.data_dir, intake_id, refresh=True)

        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(second["cache"]["hit"])
        self.assertFalse(refreshed["cache"]["hit"])
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(first["revision"], refreshed["revision"])
        self.assertEqual(first["profile"], approved_profile)
        self.assertEqual(second["profile"], approved_profile)
        self.assertEqual(len(first["sources"]), 1)
        saved_source = first["sources"][0]
        self.assertEqual(saved_source["id"], source["id"])
        self.assertEqual(saved_source["text"], SOURCE_TEXT)
        self.assertIn("measured outcomes", saved_source["text"])
        self.assertIn("https://example.invalid/catalog/design", saved_source["text"])
        self.assertEqual(
            saved_source["content_sha256"],
            hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual(first["intake_id"], intake_id)
        self.assertIsInstance(first["cache"]["created_at"], str)

    def test_review_withdrawal_rejects_cached_context_and_reapproval_revises_it(self) -> None:
        intake_id, source, _ = self._create_approved_intake()
        approved_context = cache.context_cached(self.data_dir, intake_id)

        api.review_intake(
            intake_id,
            {"result": _profile(source["id"], "Metrics Viewer")},
            None,
        )
        with self.assertRaises(ValueError):
            cache.context_cached(self.data_dir, intake_id)

        reapproved = api.approve_intake(intake_id, {}, None)["result"]
        corrected_context = cache.context_cached(self.data_dir, intake_id)
        self.assertFalse(corrected_context["cache"]["hit"])
        self.assertEqual(corrected_context["profile"], reapproved)
        self.assertEqual(corrected_context["profile"]["projects"], ["Metrics Viewer"])
        self.assertNotEqual(corrected_context["revision"], approved_context["revision"])
        self.assertEqual(corrected_context["sources"][0]["id"], source["id"])

    def test_intake_contexts_are_isolated_by_intake_and_source_identity(self) -> None:
        first_id, first_source, _ = self._create_approved_intake()
        second_text = SOURCE_TEXT + " Independent source snapshot."
        second_id, second_source, _ = self._create_approved_intake(
            text=second_text, project="Metrics Viewer"
        )

        first = cache.context_cached(self.data_dir, first_id)
        second = cache.context_cached(self.data_dir, second_id)
        first_again = cache.context_cached(self.data_dir, first_id)

        self.assertNotEqual(first_id, second_id)
        self.assertNotEqual(first_source["id"], second_source["id"])
        self.assertNotEqual(first["revision"], second["revision"])
        self.assertEqual(first["sources"][0]["id"], first_source["id"])
        self.assertEqual(second["sources"][0]["id"], second_source["id"])
        self.assertEqual(first_again["sources"][0]["text"], SOURCE_TEXT)
        self.assertEqual(second["sources"][0]["text"], second_text)
        self.assertTrue(first_again["cache"]["hit"])

    def test_corrupt_context_payload_is_rebuilt_from_approved_source_of_truth(self) -> None:
        intake_id, _, approved_profile = self._create_approved_intake()
        cache.context_cached(self.data_dir, intake_id)
        _corrupt_saved_context(self.data_dir / "cache" / "cache.sqlite3", intake_id)

        rebuilt = cache.context_cached(self.data_dir, intake_id)

        self.assertFalse(rebuilt["cache"]["hit"])
        self.assertEqual(rebuilt["profile"], approved_profile)
        self.assertEqual(rebuilt["sources"][0]["text"], SOURCE_TEXT)

    def test_offline_cli_reads_persisted_context_in_a_fresh_process(self) -> None:
        intake_id, _, _ = self._create_approved_intake()
        stored = cache.context_cached(self.data_dir, intake_id)
        project_root, environment = _python_environment()
        environment["FIND_A_JOB_DATA_DIR"] = str(self.data_dir)

        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "find_a_job.cli",
                "context",
                "--intake-id",
                intake_id,
            ],
            cwd=project_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        output = json.loads(process.stdout)

        self.assertEqual(output["schema_version"], 1)
        self.assertEqual(output["intake_id"], intake_id)
        self.assertEqual(output["revision"], stored["revision"])
        self.assertTrue(output["cache"]["hit"])
        self.assertEqual(output["sources"][0]["text"], SOURCE_TEXT)


class IntakeAndConsumerIsolationTests(CacheTestCase):
    def test_identical_uploads_keep_fresh_intakes_sources_and_approval_state(self) -> None:
        first = self._upload_intake("Synthetic first intake")
        first_source = first["input"]["sources"][0]
        first_approved = self._approve_sources(
            first["id"],
            [first_source],
            {
                "name": "Ada Example",
                "skills": ["Python"],
                "experience": [],
                "projects": ["Atlas Ledger"],
                "education": [],
                "evidence": [
                    {
                        "id": "upload-evidence",
                        "text": "Ada Example built Atlas Ledger in Python.",
                        "source": first_source["id"],
                    }
                ],
            },
        )
        second = self._upload_intake("Synthetic second intake")
        second_source = second["input"]["sources"][0]
        self.assertEqual(first_source["text"], second_source["text"])
        self.assertFalse(first_source["extraction_cache"]["hit"])
        self.assertTrue(second_source["extraction_cache"]["hit"])
        self.assertEqual(first_source["content_sha256"], second_source["content_sha256"])
        self.assertEqual(
            first_source["extraction_cache"]["extractor_version"],
            second_source["extraction_cache"]["extractor_version"],
        )
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first_source["id"], second_source["id"])
        self.assertEqual(first_approved["review_status"], "approved")
        self.assertIsNone(second["result"])
        uploaded_files = [path for path in (self.data_dir / "uploads").rglob("*") if path.is_file()]
        self.assertEqual(len(uploaded_files), 2)
        self.assertTrue(all(path.read_bytes() == UPLOAD_TEXT.encode() for path in uploaded_files))

    def test_resume_claim_rejects_profile_that_lost_approval(self) -> None:
        intake_id, source, _ = self._create_approved_intake()
        _, resume_id = self._create_research_and_resume(intake_id)
        api.review_intake(
            intake_id,
            {"result": _profile(source["id"], "Metrics Viewer")},
            None,
        )

        try:
            claimed = api.claim(api.ClaimRequest(worker_id="synthetic-host"), None)
        except HTTPException as error:
            self.assertEqual(error.status_code, 409)
        else:
            self.assertIsNone(claimed["task"])

        with api._db() as connection:
            row = connection.execute(
                "SELECT status,error FROM tasks WHERE id=?", (resume_id,)
            ).fetchone()
        self.assertNotEqual(row["status"], "running")
        if row["status"] == "failed":
            self.assertTrue(row["error"])

    def test_claimed_resume_cannot_complete_after_profile_revision_changes(self) -> None:
        intake_id, source, approved_profile = self._create_approved_intake()
        _, resume_id = self._create_research_and_resume(intake_id)
        claimed = api.claim(api.ClaimRequest(worker_id="synthetic-host"), None)["task"]
        self.assertEqual(claimed["id"], resume_id)
        self.assertEqual(claimed["input"]["profile"], approved_profile)
        claimed_revision = claimed["input"]["profile_context"]["revision"]
        self.assertEqual(claimed_revision, cache.context_cached(self.data_dir, intake_id)["revision"])
        self.assertEqual(claimed["input"]["profile_context"]["sources"][0]["id"], source["id"])
        self.assertEqual(claimed["input"]["profile_context"]["sources"][0]["text"], SOURCE_TEXT)
        self.assertEqual(claimed["input"]["job_description"]["text"], "Synthetic role requiring Python.")

        api.review_intake(
            intake_id,
            {"result": _profile(source["id"], "Metrics Viewer")},
            None,
        )
        api.approve_intake(intake_id, {}, None)
        with self.assertRaises(HTTPException) as rejected:
            api.complete(
                resume_id,
                api.CompleteRequest(
                    worker_id="synthetic-host",
                    result={
                        "name": "Ada Example",
                        "contact": [],
                        "sections": [
                            {
                                "heading": "Projects",
                                "items": [
                                    {"text": "Atlas Ledger", "evidence_ids": ["evidence-1"]}
                                ],
                            }
                        ],
                    },
                ),
                None,
            )

        self.assertEqual(rejected.exception.status_code, 409)
        self.assertTrue(any(word in str(rejected.exception.detail).lower() for word in ("revision", "changed", "review", "profile")))
        with api._db() as connection:
            row = connection.execute(
                "SELECT status FROM tasks WHERE id=?", (resume_id,)
            ).fetchone()
        self.assertNotEqual(row["status"], "completed")
        self.assertNotEqual(claimed_revision, cache.context_cached(self.data_dir, intake_id)["revision"])


if __name__ == "__main__":
    unittest.main()
