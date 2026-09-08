"""Read-only local diagnostics. No login, browser mutation, or personal content output."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import store


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            digest.update(path.relative_to(root).as_posix().encode('utf-8'))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def diagnose(data_dir: Path | None = None, compare_skill: Path | None = None, codex: str | None = None) -> dict:
    skill = Path(__file__).resolve().parents[1]
    checks = [{'name': 'python', 'status': 'ok' if sys.version_info >= (3, 10) else 'error',
               'version': '.'.join(map(str, sys.version_info[:3]))},
              {'name': 'skill', 'status': 'ok', 'fingerprint': tree_digest(skill)}]
    if compare_skill is not None:
        other = compare_skill.expanduser()
        checks.append({'name': 'installed-copy', 'status': 'missing' if not other.is_dir() else
                       ('ok' if tree_digest(other) == tree_digest(skill) else 'different')})
    if data_dir is not None:
        root = data_dir.expanduser()
        try:
            policy = store.load_policy(root)
            state = store.load_state(root)
            unresolved = [a for a in state['actions'].values() if a['status'] in ('unknown', 'pending')]
            checks.append({'name': 'data', 'status': 'ok', 'policyFingerprint': store.fingerprint(policy),
                           'runBusy': bool(state.get('runLock')), 'unresolvedCount': len(unresolved),
                           'pendingCount': sum(a['status'] == 'pending' for a in unresolved),
                           'blockedPlatformCount': sum(b.get('active', True) for b in state['blocks'].values()),
                           'profileExists': (root / 'profile.md').is_file()})
        except (OSError, ValueError, TypeError, KeyError) as error:
            # Error strings can contain user paths and company names; do not export them.
            checks.append({'name': 'data', 'status': 'error', 'errorType': type(error).__name__,
                           'next': 'Run store.py check-policy locally; initialize a new directory only if intended.'})
    if codex:
        try:
            result = subprocess.run([codex, 'plugin', '--help'], capture_output=True, timeout=10,
                                    encoding='utf-8', errors='replace', check=False)
            import re
            can_add = result.returncode == 0 and bool(re.search(r'^\s+add\s', result.stdout, re.M))
            checks.append({'name': 'codex-plugin-add', 'status': 'available' if can_add else 'unavailable',
                           'next': 'Use the documented plugin installation UI or standalone Skill path if unavailable.'})
        except (OSError, subprocess.TimeoutExpired):
            checks.append({'name': 'codex-plugin-add', 'status': 'unavailable'})
    checks.append({'name': 'browser-account', 'status': 'unverified',
                   'next': 'Run recon in the selected browser; local diagnostics do not prove login or submission.'})
    return {'readOnly': True, 'checks': checks}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', type=Path)
    p.add_argument('--compare-skill', type=Path)
    p.add_argument('--codex', help='Optional native executable to query with plugin --help; no installation occurs.')
    a = p.parse_args()
    result = diagnose(a.data_dir, a.compare_skill, a.codex)
    print(json.dumps(result, ensure_ascii=False))
    return int(any(c['status'] == 'error' for c in result['checks']))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
