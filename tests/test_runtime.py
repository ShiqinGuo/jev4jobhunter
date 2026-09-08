"""Behavioral checks using isolated local data; no browser or network writes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "skills" / "job-hunter" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import store
from audit_gate import check_text


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="job-hunter-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        store.initialize(self.root)
        self.token = store.run_lock(self.root, "acquire")["token"]
        self.policy(authorization={**{kind: 'allow' for kind in store.KINDS},
                                   'evidence': 'Fixture user authorized these actions.'})

    def request(self, **changes):
        return {"kind": "reply", "platform": "boss", "targetKey": "boss:thread-1",
                "inboundId": "inbound-1", "content": "Thanks, I mainly work with Python.",
                "context": "Fixture: a recruiter asks about backend experience.",
                "authorizationEvidence": "Fixture user authorized this exact reply.", **changes}

    def policy(self, **changes):
        policy = store.read_json(self.root / "policy.json")
        policy.update(changes)
        store.write_json(self.root / "policy.json", policy)

    def test_setup_preserves_user_files_and_history(self):
        profile = self.root / "profile.md"
        profile.write_text("User corrections", encoding="utf-8")
        state_before = (self.root / "state.json").read_bytes()
        self.assertEqual(store.initialize(self.root)["created"], [])
        self.assertEqual(profile.read_text(encoding="utf-8"), "User corrections")
        self.assertEqual((self.root / "state.json").read_bytes(), state_before)

    def test_legacy_policy_gets_example_without_replacement(self):
        (self.root / "policy.json").unlink()
        (self.root / "policy.md").write_text("- autonomy: balanced", encoding="utf-8")
        store.initialize(self.root)
        self.assertFalse((self.root / "policy.json").exists())
        self.assertTrue((self.root / "policy.example.json").exists())
        with self.assertRaisesRegex(store.StoreError, "policy-missing"):
            store.begin(self.root, self.token, self.request())

    def test_preview_candidate_is_not_a_completed_application(self):
        store.update(self.root, self.token, "jobs", "boss:job-1", {"status": "shortlisted"})
        action = store.begin(self.root, self.token, self.request(kind="greet", targetKey="boss:job-1"))
        self.assertEqual(action["status"], "pending")

    def test_unknown_survives_run_restart_and_changed_wording(self):
        action = store.begin(self.root, self.token, self.request())
        store.resolve(self.root, self.token, action["id"], "unknown", "Connection lost after click")
        store.run_lock(self.root, "release", self.token)
        self.token = store.run_lock(self.root, "acquire")["token"]
        with self.assertRaisesRegex(store.StoreError, "duplicate-or-unresolved"):
            store.begin(self.root, self.token, self.request(content="Another wording of the reply"))
        store.resolve(self.root, self.token, action["id"], "succeeded", "Matching delivered bubble")
        with self.assertRaisesRegex(store.StoreError, "terminal-action"):
            store.resolve(self.root, self.token, action["id"], "failed", "Cannot undo a receipt")

    def test_kind_change_cannot_repeat_the_same_reply(self):
        store.begin(self.root, self.token, self.request())
        with self.assertRaisesRegex(store.StoreError, "duplicate-or-unresolved"):
            store.begin(self.root, self.token, self.request(kind="commitment"))

    def test_same_text_in_other_thread_or_new_inbound_is_allowed(self):
        store.begin(self.root, self.token, self.request())
        self.assertEqual(store.begin(self.root, self.token, self.request(targetKey="boss:thread-2"))["status"], "pending")
        self.assertEqual(store.begin(self.root, self.token, self.request(inboundId="inbound-2"))["status"], "pending")

    def test_failed_attempt_can_be_retried_and_quota_is_released(self):
        limits = {kind: 1 for kind in store.KINDS}
        self.policy(dailyLimits=limits)
        action = store.begin(self.root, self.token, self.request())
        with self.assertRaisesRegex(store.StoreError, "daily-limit"):
            store.begin(self.root, self.token, self.request(inboundId="inbound-2"))
        store.resolve(self.root, self.token, action["id"], "unknown", "No receipt yet")
        with self.assertRaisesRegex(store.StoreError, "daily-limit"):
            store.begin(self.root, self.token, self.request(inboundId="inbound-2"))
        store.resolve(self.root, self.token, action["id"], "failed", "Visible rejection: not sent")
        self.assertEqual(store.begin(self.root, self.token, self.request())["reservedToday"], 1)

    def test_platform_managed_limit_still_counts_and_deduplicates(self):
        self.policy(dailyLimits={kind: None for kind in store.KINDS})
        for index in range(12):
            request = self.request(kind="greet", targetKey=f"boss:job-{index}")
            action = store.begin(self.root, self.token, request)
            self.assertEqual(action["reservedToday"], index + 1)
        store.resolve(self.root, self.token, action["id"], "unknown", "No visible receipt")
        with self.assertRaisesRegex(store.StoreError, "duplicate-or-unresolved"):
            store.begin(self.root, self.token, request)
        store.update(self.root, self.token, "blocks", "boss", {"kinds": ["greet"], "reason": "Platform daily quota reached"})
        with self.assertRaisesRegex(store.StoreError, "platform-blocked"):
            store.begin(self.root, self.token, self.request(kind="greet", targetKey="boss:new-job"))

    def test_explicit_limit_can_bound_platform_managed_quota(self):
        self.policy(dailyLimits={kind: None for kind in store.KINDS})
        action = store.begin(self.root, self.token, self.request(), limit=1)
        store.resolve(self.root, self.token, action["id"], "unknown", "No visible receipt")
        with self.assertRaisesRegex(store.StoreError, "daily-limit"):
            store.begin(self.root, self.token, self.request(inboundId="next"), limit=1)
        with self.assertRaisesRegex(store.StoreError, "invalid-limit"):
            store.begin(self.root, self.token, self.request(inboundId="next"), limit=True)

    def test_missing_limit_is_not_implicit_platform_managed_quota(self):
        limits = {kind: None for kind in store.KINDS}
        del limits["reply"]
        self.policy(dailyLimits=limits)
        with self.assertRaisesRegex(store.StoreError, "invalid-daily-limit:reply"):
            store.begin(self.root, self.token, self.request())
        self.assertEqual(store.load_state(self.root)["actions"], {})

    def test_account_platform_and_handover_boundaries(self):
        store.update(self.root, self.token, "blocks", "boss", {"kinds": ["reply"], "reason": "verification"})
        with self.assertRaisesRegex(store.StoreError, "platform-blocked"):
            store.begin(self.root, self.token, self.request())
        self.assertEqual(store.begin(self.root, self.token, self.request(platform="careers", targetKey="careers:thread-1"))["status"], "pending")
        store.update(self.root, self.token, "blocks", "boss", {"active": False})
        store.update(self.root, self.token, "threads", "boss:thread-1", {"humanTakenOver": True})
        with self.assertRaisesRegex(store.StoreError, "thread-paused"):
            store.begin(self.root, self.token, self.request())

    def test_cross_platform_same_opportunity_is_not_reapplied(self):
        request = self.request(kind="application", targetKey="boss:job-1", answers={"name": "Fixture"})
        store.begin(self.root, self.token, request)
        store.update(self.root, self.token, "jobs", "careers:job-1", {"sameOpportunityAs": ["boss:job-1"]})
        with self.assertRaisesRegex(store.StoreError, "same-opportunity"):
            store.begin(self.root, self.token, {**request, "platform": "careers", "targetKey": "careers:job-1"})

    def test_explicit_one_shot_reply_preserves_human_handover(self):
        store.update(self.root, self.token, "threads", "boss:thread-1", {"humanTakenOver": True})
        grant = {k: self.request()[k] for k in ('kind', 'platform', 'targetKey', 'inboundId')}
        grant['evidence'] = 'Fixture user asked for this one reply.'
        store.begin(self.root, self.token, self.request(oneShotHandover=True, oneShotAuthorization=grant))
        self.assertTrue(store.load_state(self.root)["threads"]["boss:thread-1"]["humanTakenOver"])
        with self.assertRaisesRegex(store.StoreError, "thread-paused"):
            store.begin(self.root, self.token, self.request(inboundId="next-message"))

    def test_cli_reads_unicode_json_and_reports_confirmed_result(self):
        request_file = self.root / "回复材料.json"
        request_file.write_text(json.dumps(self.request(content="您好，我使用 Python 开发订单服务。"), ensure_ascii=False), encoding="utf-8-sig")
        command = [sys.executable, "-B", str(SCRIPT_DIR / "store.py"), "--data-dir", str(self.root)]
        result = subprocess.run(command + ["begin", "--token", self.token, "--file", str(request_file)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        action_id = json.loads(result.stdout)["id"]
        result = subprocess.run(command + ["resolve", "--token", self.token, "--id", action_id, "--status", "succeeded", "--evidence", "Fixture delivered receipt"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(store.report(self.root)["counts"]["reply"]["succeeded"], 1)

    def test_attachment_bound_to_exact_file_and_missing_file_fails(self):
        resume = self.root / "Resume 简历.txt"
        resume.write_text("Fixture resume", encoding="utf-8")
        request = self.request(kind="share_resume", attachments=[str(resume)])
        action = store.begin(self.root, self.token, request)
        stored = store.load_state(self.root)["actions"][action["id"]]
        self.assertEqual(stored["attachments"][0]["sha256"], hashlib.sha256(resume.read_bytes()).hexdigest())
        with self.assertRaisesRegex(store.StoreError, "attachment-must"):
            store.begin(self.root, self.token, self.request(kind="share_resume", inboundId="new", attachments=[str(self.root / "missing.pdf")]))

    def test_day_count_uses_policy_timezone_and_unknown_still_dedupes(self):
        self.policy(timezone="+08:00")
        with patch.object(store, "now", return_value=datetime(2026, 9, 7, 18, tzinfo=timezone.utc)):
            action = store.begin(self.root, self.token, self.request())
        self.assertEqual(store.load_state(self.root)["actions"][action["id"]]["date"], "2026-09-08")
        with patch.object(store, "now", return_value=datetime(2026, 9, 9, 18, tzinfo=timezone.utc)):
            with self.assertRaisesRegex(store.StoreError, "duplicate-or-unresolved"):
                store.begin(self.root, self.token, self.request())

    def test_active_hours_support_overnight(self):
        self.policy(timezone="+00:00", activeHours=["22:00", "06:00"])
        with patch.object(store, "now", return_value=datetime(2026, 9, 7, 23, tzinfo=timezone.utc)):
            store.begin(self.root, self.token, self.request())
        with patch.object(store, "now", return_value=datetime(2026, 9, 8, 6, tzinfo=timezone.utc)):
            with self.assertRaisesRegex(store.StoreError, "outside-active-hours"):
                store.begin(self.root, self.token, self.request(inboundId="new"))

    def test_old_lock_never_expires_and_wrong_owner_cannot_mutate(self):
        state = store.load_state(self.root)
        state["runLock"]["heartbeatAt"] = "2001-01-01T00:00:00+00:00"
        store.write_json(self.root / "state.json", state)
        with self.assertRaisesRegex(store.StoreError, "run-busy"):
            store.run_lock(self.root, "acquire")
        with self.assertRaisesRegex(store.StoreError, "token-mismatch"):
            store.run_lock(self.root, "release", "wrong")
        with self.assertRaisesRegex(store.StoreError, "token-mismatch"):
            store.begin(self.root, "wrong", self.request())
        with self.assertRaisesRegex(store.StoreError, "section-not-editable"):
            store.update(self.root, self.token, "actions", "fake", {"status": "succeeded"})

    def test_simultaneous_processes_have_only_one_run_owner(self):
        store.run_lock(self.root, "release", self.token)
        command = [sys.executable, "-B", str(SCRIPT_DIR / "store.py"), "--data-dir", str(self.root), "lock", "acquire"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: subprocess.run(command, capture_output=True, text=True, encoding="utf-8"), range(4)))
        self.assertEqual(sum(result.returncode == 0 for result in results), 1)
        for result in results:
            self.assertIn(result.returncode, (0, 1), result.stderr)
            self.assertIsInstance(json.loads(result.stdout), dict)

    def test_interrupted_atomic_save_leaves_original_intact(self):
        before = (self.root / "state.json").read_bytes()
        with patch.object(store.os, "replace", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                store.begin(self.root, self.token, self.request())
        self.assertEqual((self.root / "state.json").read_bytes(), before)
        self.assertEqual(list(self.root.glob(".state.json.*")), [])

    def test_audit_failure_does_not_undo_saved_receipt(self):
        action = store.begin(self.root, self.token, self.request())
        logs = self.root / "logs"
        logs.rmdir()
        logs.write_text("Simulated unwritable log directory", encoding="utf-8")
        result = store.resolve(self.root, self.token, action["id"], "succeeded", "Delivered bubble")
        self.assertIn("warning", result)
        self.assertEqual(store.load_state(self.root)["actions"][action["id"]]["status"], "succeeded")
        with self.assertRaisesRegex(store.StoreError, "duplicate-or-unresolved"):
            store.begin(self.root, self.token, self.request())

    def test_report_is_read_only(self):
        before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.root.iterdir() if p.is_file()}
        store.report(self.root)
        after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.root.iterdir() if p.is_file()}
        self.assertEqual(before, after)

    def test_legacy_migration_preserves_archive_history_and_current_counts(self):
        old = {"version": 1, "startedAt": "2026-01-01", "runLock": None,
               "dayKillSwitch": {"date": "2026-01-01", "reason": "verification"},
               "jobs": {"boss:skip": {"status": "skipped"}}, "threads": {},
               "daily": {"2026-09-07": {"greeted": ["legacy-contact"]}}, "cron": {"old": "retained"}}
        store.write_json(self.root / "state.json", old)
        store.write_json(self.root / "logs" / "archive-2026-08.json", {"boss:old": {"status": "closed"}})
        store.write_json(self.root / "logs" / "archive-2026-07.json", {"jobs": {}, "threads": {"boss:old-thread": {"stage": "closed", "humanTakenOver": True}}})
        original = (self.root / "state.json").read_bytes()
        result = store.migrate(self.root)
        self.assertEqual(Path(result["backup"]).read_bytes(), original)
        state = store.load_state(self.root)
        self.assertTrue(state["jobs"]["boss:old"]["legacyContacted"])
        self.assertTrue(state["threads"]["boss:old-thread"]["humanTakenOver"])
        self.assertFalse(state["jobs"]["boss:skip"].get("legacyContacted", False))
        self.assertEqual(store.used_today(state, "2026-09-07", "greet"), 1)
        self.assertIn("*", state["blocks"])
        self.assertFalse(store.migrate(self.root)["migrated"])

    def test_invalid_policy_and_missing_authorization_leave_no_action(self):
        with self.assertRaisesRegex(store.StoreError, "authorizationEvidence"):
            store.begin(self.root, self.token, self.request(authorizationEvidence=""))
        self.policy(timezone="+08:99")
        with self.assertRaisesRegex(store.StoreError, "timezone"):
            store.begin(self.root, self.token, self.request())
        self.assertEqual(store.load_state(self.root)["actions"], {})


class TextChecks(unittest.TestCase):
    def test_multilingual_short_replies_and_technical_names(self):
        for text in ("好", "Thanks!", "Bonjour, je travaille avec Python.", "我熟悉 C++、Python 和 PostgreSQL。"):
            self.assertTrue(check_text(text)["pass"], text)

    def test_placeholders_and_unapproved_links_are_caught(self):
        for text in ("Hello [NAME]", "您好，{{company}} 的团队", "See https://example.com/work"):
            self.assertFalse(check_text(text)["pass"], text)
        self.assertTrue(check_text("See https://example.com/work.", allowed_links=["https://example.com/work"])["pass"])


if __name__ == "__main__":
    unittest.main()
