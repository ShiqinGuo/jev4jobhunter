#!/usr/bin/env python3
"""Local run lock, atomic state and outbox. Never operates a browser or sends data."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid
from zoneinfo import ZoneInfo

from audit_gate import check_text
from policy_rules import (PolicyError, fingerprint, validate_rules, check_authorization,
                          check_target, check_materials, validate_interview)

KINDS = ("greet", "application", "reply", "share_resume", "commitment")
HELD = {"pending", "unknown", "succeeded"}
DEFAULT_POLICY = {
    "version": 2,
    "platforms": [],
    "targets": {"keywords": [], "locations": [], "workModes": [], "salary": None,
                "mustHave": [], "preferences": [], "exclude": [], "excludedCompanies": []},
    "authorization": {"greet": "draft", "application": "draft", "reply": "draft",
                      "share_resume": "ask", "commitment": "ask", "evidence": ""},
    "dailyLimits": {"greet": 10, "application": 10, "reply": 20,
                    "share_resume": 5, "commitment": 5},
    "timezone": "local",
    "activeHours": None,
    "weekdays": [1, 2, 3, 4, 5, 6, 7],
    "recheckAfterDays": 7,
    "allowedLinks": [],
    "schedule": {"enabled": False, "windows": []},
    "search": {"excludeHeadhunterPosted": False},
}


StoreError = PolicyError


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return now().isoformat(timespec="seconds")


def read_json(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict):
        raise StoreError(f"expected-object:{path.name}")
    return result


def write_json(path: Path, value: dict) -> None:
    """Same-directory replace: readers see either the old or complete new file."""
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def transaction(root: Path):
    """OS lock serializes short updates; the persistent run token spans UI calls."""
    root.mkdir(parents=True, exist_ok=True)
    # Never delete this inode: other processes may already have it open.
    with (root / ".store.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise StoreError("store-busy:retry-after-current-update") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def new_state() -> dict:
    return {"version": 2, "startedAt": stamp(), "runLock": None,
            "jobs": {}, "threads": {}, "actions": {}, "blocks": {},
            "scheduler": {}, "legacy": {}}


def load_state(root: Path) -> dict:
    state = read_json(root / "state.json")
    if state.get("version") != 2:
        raise StoreError("state-version:run-migrate-for-version-1")
    for field in ("jobs", "threads", "actions", "blocks", "scheduler", "legacy"):
        if not isinstance(state.get(field), dict):
            raise StoreError(f"invalid-state:{field}")
    return state


def require_token(state: dict, token: str | None) -> None:
    lock = state.get("runLock")
    if not token or not lock or lock.get("token") != token:
        raise StoreError("run-token-mismatch")


def active_account_context(state: dict, platform: str) -> dict:
    registry = state.get('accountContexts', {}).get(platform, {})
    return registry.get('contexts', {}).get(registry.get('activeContextId'), {})


def applicable_blocks(state: dict, platform: str):
    """Yield restrictions whose recorded scope applies to the selected context."""
    context_id = active_account_context(state, platform).get('id')
    entries = [(scope, state['blocks'].get(scope, {})) for scope in ('*', platform)]
    entries.extend((platform, block) for block in state.get('contextBlocks', {}).get(platform, {}).values())
    for scope, block in entries:
        if not block or not block.get('active', True):
            continue
        if block.get('applicability') == 'all-contexts' and block.get('scopeEvidence'):
            yield scope, block
        elif block.get('accountContextId'):
            if block['accountContextId'] == context_id:
                yield scope, block
        elif block.get('appliesToContextIds') is not None:
            if context_id in block['appliesToContextIds']:
                yield scope, block
        else:
            # Unscoped legacy entries need an explicit audited attribution.
            yield scope, block


def check_account_context(state: dict, request: dict) -> None:
    context = active_account_context(state, request['platform'])
    if context and (request.get('accountContextId') != context['id'] or
                    request.get('accountLabel') != context['accountLabel']):
        raise StoreError('active-account-context-mismatch')


def local_now(policy: dict) -> datetime:
    value = policy.get("timezone", "local")
    if value == "local":
        return now().astimezone()
    if isinstance(value, str) and re.fullmatch(r"[+-]\d{2}:\d{2}", value):
        hours, minutes = map(int, value[1:].split(":"))
        if hours > 23 or minutes > 59:
            raise StoreError("invalid-timezone-offset")
        offset = timedelta(hours=hours, minutes=minutes)
        return now().astimezone(timezone(offset if value[0] == "+" else -offset))
    try:
        return now().astimezone(ZoneInfo(value))
    except (KeyError, TypeError, ValueError) as error:
        raise StoreError("timezone-unavailable:use-local-or-an-explicit-offset") from error


def validate_policy(policy: dict) -> None:
    if policy.get("version") != 2:
        raise StoreError("policy-version-must-be-2")
    authorization = policy.get("authorization")
    if not isinstance(authorization, dict) or any(
        authorization.get(kind) not in ("draft", "ask", "allow") for kind in KINDS
    ):
        raise StoreError("invalid-authorization-mode")
    if "allow" in [authorization[kind] for kind in KINDS]:
        required_string(authorization, "evidence")
    limits = policy.get("dailyLimits", {})
    if not isinstance(limits, dict):
        raise StoreError("invalid-daily-limits")
    for kind in KINDS:
        if kind not in limits or (limits[kind] is not None and (
            type(limits[kind]) is not int or limits[kind] < 0
        )):
            raise StoreError(f"invalid-daily-limit:{kind}")
    weekdays = policy.get("weekdays", list(range(1, 8)))
    if not isinstance(weekdays, list) or any(type(d) is not int or d not in range(1, 8) for d in weekdays):
        raise StoreError("invalid-weekdays")
    hours = policy.get("activeHours")
    if hours is not None:
        if not isinstance(hours, list) or len(hours) != 2 or any(
            not isinstance(t, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t) for t in hours
        ) or hours[0] == hours[1]:
            raise StoreError("invalid-active-hours")
    links = policy.get("allowedLinks", [])
    if not isinstance(links, list) or any(not isinstance(link, str) for link in links):
        raise StoreError("invalid-allowed-links")
    local_now(policy)
    validate_rules(policy)


def load_policy(root: Path) -> dict:
    path = root / "policy.json"
    if not path.exists():
        raise StoreError("policy-missing:review-policy.example.json-or-migrate-policy.md")
    policy = read_json(path)
    validate_policy(policy)
    return policy


def initialize(root: Path) -> dict:
    with transaction(root):
        created = []
        for folder in ("logs", "drafts"):
            (root / folder).mkdir(exist_ok=True)
        path = root / "state.json"
        if not path.exists():
            write_json(path, new_state())
            created.append(path.name)
        # Never overwrite policy, profile, opener or old state during repeated setup.
        policy_name = "policy.example.json" if (root / "policy.md").exists() else "policy.json"
        if not (root / "policy.json").exists() and not (root / policy_name).exists():
            write_json(root / policy_name, DEFAULT_POLICY)
            created.append(policy_name)
        return {"created": created, "dataDir": str(root)}


def profile_fingerprint(root: Path) -> str | None:
    profile = root / 'profile.md'
    return hashlib.sha256(profile.read_bytes()).hexdigest() if profile.is_file() else None


def effective_config(root: Path) -> dict:
    policy = load_policy(root)
    return {'policy': policy, 'policyFingerprint': fingerprint(policy),
            'profileFingerprint': profile_fingerprint(root),
            'factsSource': 'profile.md', 'executionSource': 'policy.json'}


def set_policy(root: Path, token: str, policy: dict, expected: str, evidence: str) -> dict:
    if not evidence.strip():
        raise StoreError('policy-change-evidence-required')
    validate_policy(policy)
    with transaction(root):
        state = load_state(root)
        require_token(state, token)
        old = load_policy(root)
        if fingerprint(old) != expected:
            raise StoreError('policy-changed:reread-before-updating')
        # Keep a recovery copy before changing the effective configuration.
        backup = root / 'logs' / ('policy-' + expected[:16] + '.json')
        backup.parent.mkdir(exist_ok=True)
        if not backup.exists():
            write_json(backup, old)
        policy = {**policy, 'changeSource': {'at': stamp(), 'evidence': evidence}}
        write_json(root / 'policy.json', policy)
        return {'updated': True, 'policyFingerprint': fingerprint(policy), 'backup': backup.name}


def migrate(root: Path) -> dict:
    with transaction(root):
        path = root / "state.json"
        old = read_json(path)
        if old.get("version") == 2:
            load_state(root)
            return {"migrated": False, "reason": "already-version-2"}
        if old.get("version") != 1:
            raise StoreError("unsupported-state-version")
        if old.get("runLock"):
            raise StoreError("legacy-run-lock-present:verify-owner-ended-before-migration")
        for field in ("jobs", "threads", "daily", "cron"):
            if not isinstance(old.get(field, {}), dict):
                raise StoreError(f"invalid-legacy-state:{field}")
        # Keep a byte-for-byte backup; an existing backup is never replaced.
        backup = root / f"state.v1.{uuid.uuid4().hex}.bak.json"
        with backup.open("xb") as stream:
            stream.write(path.read_bytes())
        state = new_state()
        state.update(startedAt=old.get("startedAt", stamp()),
                     jobs=old.get("jobs", {}), threads=old.get("threads", {}))
        state["legacy"] = {"daily": old.get("daily", {}), "cron": old.get("cron", {}),
                           "backup": backup.name}
        # Migrate old archives too, or previously contacted jobs could be retried.
        for archive in sorted((root / "logs").glob("archive-*.json")):
            archived = read_json(archive)
            jobs = archived.get("jobs", archived)
            if not isinstance(jobs, dict):
                raise StoreError(f"invalid-legacy-archive:{archive.name}")
            threads = archived.get("threads", {})
            if not isinstance(threads, dict):
                raise StoreError(f"invalid-legacy-threads:{archive.name}")
            for key, thread in threads.items():
                if not isinstance(thread, dict):
                    raise StoreError(f"invalid-legacy-thread:{key}")
                state["threads"].setdefault(key, thread)
            for key, job in jobs.items():
                if ":" in key and isinstance(job, dict):
                    destination = "threads" if "stage" in job and "history" in job else "jobs"
                    state[destination].setdefault(key, job)
        for job in state["jobs"].values():
            if not isinstance(job, dict):
                raise StoreError("invalid-legacy-job")
            if job.get("status") not in ("skipped", "candidate", "shortlisted"):
                job["legacyContacted"] = True
        if old.get("dayKillSwitch"):
            state["blocks"]["*"] = {"kinds": list(KINDS),
                                     "reason": "legacy-block:review-before-clearing",
                                     "source": old["dayKillSwitch"]}
        write_json(path, state)
        return {"migrated": True, "backup": str(backup),
                "policy": "policy.md retained; review before writing policy.json"}


def run_lock(root: Path, operation: str, token: str | None = None) -> dict:
    with transaction(root):
        state = load_state(root)
        if operation == "acquire":
            if state["runLock"]:
                raise StoreError("run-busy:existing-lock-has-no-automatic-expiry")
            state["runLock"] = {"token": uuid.uuid4().hex, "at": stamp(), "heartbeatAt": stamp()}
            result = dict(state["runLock"])
        else:
            require_token(state, token)
            if operation == "renew":
                state["runLock"]["heartbeatAt"] = stamp()
            else:
                state["runLock"] = None
            result = {"operation": operation, "ok": True}
        write_json(root / "state.json", state)
        return result


def update(root: Path, token: str, section: str, key: str, value: dict) -> dict:
    with transaction(root):
        state = load_state(root)
        require_token(state, token)
        if section not in ("jobs", "threads", "blocks", "scheduler"):
            raise StoreError("section-not-editable")
        current = state[section].get(key, {})
        if not isinstance(current, dict):
            raise StoreError("invalid-existing-entry")
        # Shallow merge preserves unrelated fields; nested values replace as a unit.
        merged = {**current, **value}
        if section == 'threads' and any(k in value for k in ('stage', 'interviewConfirmed', 'interview', 'interviewInvite')):
            validate_interview(merged)
        if section == 'scheduler' and key.startswith('run:'):
            raise StoreError('use-run-ledger-for-run-records')
        state[section][key] = merged
        state["runLock"]["heartbeatAt"] = stamp()
        write_json(root / "state.json", state)
        return {"section": section, "key": key, "updated": True}


def required_string(value: dict, key: str) -> str:
    text = value.get(key)
    if not isinstance(text, str) or not text.strip():
        raise StoreError(f"missing-or-invalid:{key}")
    return text


def used_today(state: dict, day: str, kind: str) -> int:
    count = sum(a.get("kind") == kind and a.get("date") == day and a.get("status") in HELD
                for a in state["actions"].values())
    legacy = state["legacy"].get("daily", {}).get(day, {})
    old_key = {"greet": "greeted", "reply": "replied"}.get(kind)
    if old_key:
        count += len(legacy.get(old_key, []))
    return count


def validate_content_mode(request: dict) -> bool:
    """A platform-owned BOSS opener is an action, not an invented message draft."""
    mode = request.get('contentMode', 'text')
    if mode not in ('text', 'platform-default'):
        raise StoreError('invalid-content-mode')
    if mode != 'platform-default':
        return False
    if request.get('platform') != 'boss' or request.get('kind') != 'greet':
        raise StoreError('platform-default-only-supports-boss-greet')
    if request.get('content') not in (None, ''):
        raise StoreError('platform-default-content-must-be-unobserved')
    if request.get('observedContent') is not None:
        raise StoreError('platform-default-cannot-predeclare-observed-content')
    if request.get('attachments') or request.get('answers'):
        raise StoreError('platform-default-greet-cannot-include-other-materials')
    return True


def begin(root: Path, token: str, request: dict, limit: int | None = None) -> dict:
    with transaction(root):
        state = load_state(root)
        require_token(state, token)
        policy = load_policy(root)
        profile_hash = profile_fingerprint(root)
        browsing = request.get('browsingContext', {})
        if not isinstance(browsing, dict):
            raise StoreError('invalid-browsing-context')
        if 'profileFingerprint' in browsing and browsing['profileFingerprint'] != profile_hash:
            raise StoreError('profile-changed-before-begin')
        kind = required_string(request, "kind")
        if kind not in KINDS:
            raise StoreError("invalid-action-kind")
        platform = required_string(request, "platform")
        check_account_context(state, request)
        target = required_string(request, "targetKey")
        required_string(request, "authorizationEvidence")
        required_string(request, "context")
        if not target.startswith(platform + ":"):
            raise StoreError("target-must-be-platform-namespaced")
        check_authorization(policy, request)
        check_target(policy, state, request)
        inbound = request.get("inboundId", "")
        if kind in ("reply", "share_resume", "commitment"):
            inbound = required_string(request, "inboundId")
        family = "message" if kind in ("reply", "commitment") else kind
        identity = json.dumps([family, platform, target, inbound if kind not in ("greet", "application") else ""],
                              ensure_ascii=False, separators=(",", ":"))
        dedupe = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if any(a.get("dedupeKey") == dedupe and a.get("status") in HELD for a in state["actions"].values()):
            raise StoreError("duplicate-or-unresolved-action")
        job = state["jobs"].get(target, {})
        if kind in ("greet", "application") and job.get("legacyContacted"):
            raise StoreError("legacy-contact-requires-reconciliation")
        if kind in ("greet", "application"):
            for alias in job.get("sameOpportunityAs", []):
                if any(a.get("targetKey") == alias and a.get("kind") == kind and a.get("status") in HELD
                       for a in state["actions"].values()):
                    raise StoreError("same-opportunity-already-contacted")
        thread = state["threads"].get(target, {})
        if thread.get("needsReview") or (thread.get("humanTakenOver") and request.get("oneShotHandover") is not True):
            raise StoreError("thread-paused")
        for scope, block in applicable_blocks(state, platform):
            if block and block.get("active", True) and kind in block.get("kinds", KINDS):
                raise StoreError(f"platform-blocked:{scope}")
        moment = local_now(policy)
        hours = policy.get("activeHours")
        if moment.isoweekday() not in policy.get("weekdays", range(1, 8)):
            raise StoreError("outside-active-weekdays")
        if hours:
            current = moment.strftime("%H:%M")
            allowed = hours[0] <= current < hours[1] if hours[0] < hours[1] else (
                current >= hours[0] or current < hours[1])
            if not allowed:
                raise StoreError("outside-active-hours")
        cap = policy["dailyLimits"][kind]
        if limit is not None:
            if type(limit) is not int or limit < 0:
                raise StoreError("invalid-limit")
            cap = limit if cap is None else min(cap, limit)
        day = moment.date().isoformat()
        if cap is not None and used_today(state, day, kind) >= cap:
            raise StoreError("daily-limit-reached")
        platform_default = validate_content_mode(request)
        content = '' if platform_default else request.get("content", "")
        if not isinstance(content, str):
            raise StoreError("invalid-content")
        if not platform_default and kind in ("greet", "reply", "commitment") and not content.strip():
            raise StoreError("message-content-required")
        audit = None
        if content:
            max_len = request.get("maxLength", 800)
            if type(max_len) is not int:
                raise StoreError("invalid-max-length")
            audit = check_text(content, max_len=max_len, allowed_links=policy.get("allowedLinks", []))
            if not audit["pass"]:
                raise StoreError("text-check-failed:" + ",".join(audit["hits"]))
        attachments = request.get("attachments", [])
        if not isinstance(attachments, list):
            raise StoreError("invalid-attachments")
        stored_files = []
        for entry in attachments:
            path = Path(entry).expanduser()
            if not path.is_absolute() or not path.is_file():
                raise StoreError("attachment-must-be-existing-absolute-file")
            stored_files.append({"path": str(path.resolve()),
                                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        answers = request.get("answers", {})
        if not isinstance(answers, dict):
            raise StoreError("invalid-answers")
        check_materials(policy, request, stored_files)
        if kind == "application" and not (content or stored_files or answers):
            raise StoreError("application-materials-required")
        action_id = uuid.uuid4().hex
        action = {**request, "id": action_id, "dedupeKey": dedupe, "date": day,
                  "at": stamp(), "updatedAt": stamp(), "status": "pending",
                  "attachments": stored_files, "audit": audit, "events": [],
                  "policyFingerprint": fingerprint(policy), "profileFingerprint": profile_hash}
        if platform_default:
            action.update(content=None, observedContent=None, contentMode='platform-default')
        action["events"].append({"at": action["at"], "status": "pending"})
        state["actions"][action_id] = action
        state["runLock"]["heartbeatAt"] = stamp()
        write_json(root / "state.json", state)
        return {"id": action_id, "status": "pending", "reservedToday": used_today(state, day, kind)}


def resolve(root: Path, token: str, action_id: str, status: str, evidence: str) -> dict:
    with transaction(root):
        state = load_state(root)
        require_token(state, token)
        if status not in ("succeeded", "failed", "unknown") or not evidence.strip():
            raise StoreError("status-and-evidence-required")
        if action_id not in state["actions"]:
            raise StoreError("unknown-action-id")
        action = state["actions"][action_id]
        if action["status"] in ("succeeded", "failed"):
            if action["status"] == status:
                return {"id": action_id, "status": status, "unchanged": True}
            raise StoreError("terminal-action-cannot-change")
        action.update(status=status, evidence=evidence, updatedAt=stamp())
        action["events"].append({"at": action["updatedAt"], "status": status, "evidence": evidence})
        state["runLock"]["heartbeatAt"] = stamp()
        write_json(root / "state.json", state)
        # State is authoritative. Audit failure must never look like a failed send.
        result = {"id": action_id, "status": status}
        try:
            logs = root / "logs"
            logs.mkdir(exist_ok=True)
            with (logs / f"audit-{action['date']}.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(action, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            result["warning"] = "audit-log-unavailable:state-saved-do-not-resend"
        return result


def check_action(root: Path, token: str, action_id: str) -> dict:
    """Recheck immediately before the browser submission; never re-send unknown actions."""
    state = load_state(root)
    require_token(state, token)
    action = state['actions'][action_id]
    check_account_context(state, action)
    if action['status'] != 'pending':
        raise StoreError('only-pending-actions-can-submit')
    policy = load_policy(root)
    if action.get('policyFingerprint') != fingerprint(policy):
        raise StoreError('policy-changed-before-submit')
    if 'profileFingerprint' not in action:
        raise StoreError('profile-fingerprint-missing:reconcile-existing-action-only')
    if action['profileFingerprint'] != profile_fingerprint(root):
        raise StoreError('profile-changed-before-submit')
    moment = local_now(policy)
    if action['date'] != moment.date().isoformat():
        raise StoreError('prepared-action-date-changed')
    if moment.isoweekday() not in policy.get('weekdays', range(1, 8)):
        raise StoreError('outside-active-weekdays')
    hours = policy.get('activeHours')
    if hours:
        current = moment.strftime('%H:%M')
        allowed = hours[0] <= current < hours[1] if hours[0] < hours[1] else current >= hours[0] or current < hours[1]
        if not allowed:
            raise StoreError('outside-active-hours')
    check_authorization(policy, action)
    validate_content_mode(action)
    check_target(policy, state, action)
    thread = state['threads'].get(action['targetKey'], {})
    if thread.get('needsReview') or (thread.get('humanTakenOver') and not action.get('oneShotHandover')):
        raise StoreError('thread-paused')
    for scope, block in applicable_blocks(state, action['platform']):
        if block.get('active', True) and action['kind'] in block.get('kinds', KINDS) and block:
            raise StoreError('platform-blocked:' + scope)
    for file in action['attachments']:
        if hashlib.sha256(Path(file['path']).read_bytes()).hexdigest() != file['sha256']:
            raise StoreError('attachment-changed-before-submit')
    check_materials(policy, action, action['attachments'])
    return {'ready': True, 'id': action_id, 'kind': action['kind'], 'policyFingerprint': fingerprint(policy)}


def report(root: Path, date: str | None = None) -> dict:
    state = load_state(root)
    if date:
        datetime.strptime(date, '%Y-%m-%d')
    actions = {k: a for k, a in state['actions'].items() if date is None or a.get('date') == date}
    return {"version": 2, "readAt": stamp(), "runLock": state["runLock"],
            "date": date,
            "counts": {kind: {status: sum(a["kind"] == kind and a["status"] == status for a in actions.values())
                              for status in ("pending", "unknown", "succeeded", "failed")} for kind in KINDS},
            "unresolved": [a["id"] for a in actions.values() if a["status"] in ("pending", "unknown")],
            "allUnresolved": [a['id'] for a in state['actions'].values() if a['status'] in ('pending', 'unknown')],
            "blocks": state["blocks"], "legacyHistoryPresent": bool(state["legacy"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("JOB_HUNTER_HOME") or "~/.job-hunter"))
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "migrate", "show", "check-policy", "effective-config"):
        sub.add_parser(name)
    report_parser = sub.add_parser('report')
    report_parser.add_argument('--date')
    policy_parser = sub.add_parser('set-policy')
    policy_parser.add_argument('--token', required=True)
    policy_parser.add_argument('--file', type=Path, required=True)
    policy_parser.add_argument('--expected-fingerprint', required=True)
    policy_parser.add_argument('--evidence', required=True)
    lock_parser = sub.add_parser("lock")
    lock_parser.add_argument("operation", choices=["acquire", "renew", "release"])
    lock_parser.add_argument("--token")
    update_parser = sub.add_parser("update")
    update_parser.add_argument("--token", required=True)
    update_parser.add_argument("--section", choices=["jobs", "threads", "blocks", "scheduler"], required=True)
    update_parser.add_argument("--key", required=True)
    update_parser.add_argument("--file", type=Path, required=True)
    begin_parser = sub.add_parser("begin")
    begin_parser.add_argument("--token", required=True)
    begin_parser.add_argument("--file", type=Path, required=True)
    begin_parser.add_argument("--limit", type=int)
    resolve_parser = sub.add_parser("resolve")
    resolve_parser.add_argument("--token", required=True)
    resolve_parser.add_argument("--id", required=True)
    resolve_parser.add_argument("--status", choices=["succeeded", "failed", "unknown"], required=True)
    resolve_parser.add_argument("--evidence", required=True)
    check_parser = sub.add_parser('check-action')
    check_parser.add_argument('--token', required=True)
    check_parser.add_argument('--id', required=True)
    args = parser.parse_args()
    root = args.data_dir.expanduser().resolve()
    try:
        if args.command == "init":
            result = initialize(root)
        elif args.command == "migrate":
            result = migrate(root)
        elif args.command == "lock":
            result = run_lock(root, args.operation, args.token)
        elif args.command == "update":
            result = update(root, args.token, args.section, args.key, read_json(args.file))
        elif args.command == "begin":
            result = begin(root, args.token, read_json(args.file), args.limit)
        elif args.command == "resolve":
            result = resolve(root, args.token, args.id, args.status, args.evidence)
        elif args.command == 'check-action':
            result = check_action(root, args.token, args.id)
        elif args.command == "report":
            result = report(root, args.date)
        elif args.command == 'effective-config':
            result = effective_config(root)
        elif args.command == 'set-policy':
            result = set_policy(root, args.token, read_json(args.file), args.expected_fingerprint, args.evidence)
        elif args.command == "show":
            result = load_state(root)
        else:
            load_policy(root)
            result = {"valid": True}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
