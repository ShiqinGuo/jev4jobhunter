"""Deterministic checks of recorded policy and observed facts, not proof of consent."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import unicodedata


class PolicyError(ValueError):
    pass


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def normalized(value: str) -> str:
    return ''.join(unicodedata.normalize('NFKC', value).casefold().split())


def strings(value, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise PolicyError('invalid-' + field)
    return value


def validate_rules(policy: dict) -> None:
    platforms = policy.get('platforms', [])
    if not isinstance(platforms, list) or any(not isinstance(p, dict) or not p.get('id') for p in platforms):
        raise PolicyError('invalid-platforms')
    if len({p['id'] for p in platforms}) != len(platforms):
        raise PolicyError('duplicate-platform')
    for p in platforms:
        if not isinstance(p['id'], str) or not isinstance(p.get('accountLabel', ''), str):
            raise PolicyError('invalid-platform-identity')
    scope = policy.get('authorization', {}).get('scope', {})
    if not isinstance(scope, dict):
        raise PolicyError('invalid-authorization-scope')
    for field in ('platforms', 'targetKeys'):
        if field in scope:
            strings(scope[field], 'scope-' + field)
    targets, search = policy.get('targets', {}), policy.get('search', {})
    if not isinstance(targets, dict) or not isinstance(search, dict):
        raise PolicyError('invalid-targets-or-search')
    if type(search.get('excludeHeadhunterPosted', False)) is not bool:
        raise PolicyError('invalid-headhunter-rule')
    for field, rows in [('excludedCompanies', targets.get('excludedCompanies', [])),
                        ('excludedOpportunityGroups', search.get('excludedOpportunityGroups', []))]:
        if not isinstance(rows, list):
            raise PolicyError('invalid-' + field)
        for row in rows:
            key = 'name' if field == 'excludedCompanies' else 'key'
            if not isinstance(row, dict) or not isinstance(row.get(key), str) or not row[key].strip():
                raise PolicyError('invalid-' + field)
            strings(row.get('aliases', []), field + '-aliases')
    if search.get('batchBeforeReply') is not None and (
            type(search['batchBeforeReply']) is not int or search['batchBeforeReply'] < 1):
        raise PolicyError('invalid-batch-size')
    resume = policy.get('resume')
    if resume is not None:
        if not isinstance(resume, dict):
            raise PolicyError('invalid-resume')
        if resume.get('sha256') and (not isinstance(resume['sha256'], str) or
                len(resume['sha256']) != 64 or any(c not in '0123456789abcdef' for c in resume['sha256'].lower())):
            raise PolicyError('invalid-resume-hash')


def one_shot(request: dict) -> bool:
    grant = request.get('oneShotAuthorization')
    if grant is None:
        return False
    if not isinstance(grant, dict) or not isinstance(grant.get('evidence'), str) or not grant['evidence'].strip():
        raise PolicyError('invalid-one-shot-authorization')
    for field in ('kind', 'platform', 'targetKey'):
        if grant.get(field) != request.get(field):
            raise PolicyError('one-shot-scope-mismatch:' + field)
    if request['kind'] in ('reply', 'share_resume', 'commitment') and grant.get('inboundId') != request.get('inboundId'):
        raise PolicyError('one-shot-scope-mismatch:inboundId')
    return True


def check_authorization(policy: dict, request: dict) -> None:
    explicit = one_shot(request)
    auth = policy['authorization']
    if not explicit and auth[request['kind']] != 'allow':
        raise PolicyError('authorization-required:record-an-existing-specific-user-grant')
    scope = auth.get('scope', {})
    if not explicit:
        platforms = scope.get('platforms', [p['id'] for p in policy.get('platforms', [])])
        if (platforms or 'platforms' in scope) and request['platform'] not in platforms:
            raise PolicyError('outside-authorized-platforms')
        if 'targetKeys' in scope and request['targetKey'] not in scope['targetKeys']:
            raise PolicyError('outside-authorized-targets')
    expected = next((p.get('accountLabel') for p in policy.get('platforms', [])
                     if p['id'] == request['platform']), None) or scope.get('accountLabel')
    if expected and request.get('accountLabel') != expected:
        raise PolicyError('account-not-verified')
    if request.get('oneShotHandover') and not explicit:
        raise PolicyError('handover-needs-specific-user-grant')


def check_target(policy: dict, state: dict, request: dict, *, require_complete: bool = True) -> None:
    """Known exclusions cannot be removed by supplying fresher, contradictory facts."""
    key = request['targetKey']
    thread = state['threads'].get(key, {})
    facts = request.get('targetFacts', {})
    if not isinstance(facts, dict):
        raise PolicyError('invalid-target-facts')
    job_keys = thread.get('jobKeys', [])
    if facts.get('jobKey'):
        job_keys = [*job_keys, facts['jobKey']]
    sources = [state['jobs'].get(key, {}), thread, facts]
    sources.extend(state['jobs'].get(k, {}) for k in job_keys)
    companies = set()
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get('companyAliases', []), list):
            raise PolicyError('invalid-recorded-target-facts')
        for company in [source.get('company'), source.get('actualEmployer'), *source.get('companyAliases', [])]:
            if company:
                companies.add(normalized(company))
    exclusions = policy.get('targets', {}).get('excludedCompanies', [])
    for row in exclusions:
        if companies.intersection(normalized(x) for x in [row['name'], *row.get('aliases', [])]):
            raise PolicyError('excluded-company:' + row['name'])
    screened = request['kind'] in ('greet', 'application', 'share_resume')
    if not screened:
        return
    search = policy.get('search', {})
    if require_complete and exclusions and not companies:
        raise PolicyError('company-unverified')
    if search.get('excludeHeadhunterPosted'):
        if facts.get('publisherType') not in ('direct', 'headhunter', 'unknown', None):
            raise PolicyError('invalid-publisher-type')
        publishers = {s.get('publisherType', 'unknown') for s in sources}
        if 'headhunter' in publishers:
            raise PolicyError('excluded-headhunter-posted')
        if require_complete and (facts.get('publisherType') != 'direct' or not facts.get('evidence')):
            raise PolicyError('publisher-unverified:read-page-before-submitting')
    groups = {normalized(s['opportunityGroup']) for s in sources if s.get('opportunityGroup')}
    for group in search.get('excludedOpportunityGroups', []):
        if groups.intersection(normalized(x) for x in [group['key'], *group.get('aliases', [])]):
            raise PolicyError('excluded-opportunity-group:' + group['key'])


def check_materials(policy: dict, request: dict, files: list[dict]) -> None:
    resume = policy.get('resume') or {}
    expected = resume.get('sha256')
    grant_files = []
    if one_shot(request):
        grant_files = request['oneShotAuthorization'].get('attachments', [])
        if not isinstance(grant_files, list):
            raise PolicyError('invalid-one-shot-attachments')
    for file in files:
        explicit_file = any(isinstance(g, dict) and g.get('sha256') == file['sha256'] and
                            g.get('path') == file['path'] for g in grant_files)
        if expected and file['sha256'] != expected.lower() and not explicit_file:
            raise PolicyError('attachment-outside-authorized-version')
    if request['kind'] == 'share_resume' and not files:
        answers = request.get('answers', {})
        if not expected or answers.get('resumeSha256') != expected or not answers.get('platformResumeEvidence'):
            raise PolicyError('resume-attachment-required:or-identify-authorized-platform-copy')


def validate_interview(value: dict) -> None:
    if value.get('stage') != 'interview' and not value.get('interviewConfirmed'):
        return
    invite = value.get('interviewInvite', {})
    if not isinstance(invite, dict) or not invite.get('inboundId') or not invite.get('evidence'):
        raise PolicyError('interview-invite-evidence-required')
    if value.get('interviewConfirmed'):
        meeting = value.get('interview', {})
        if not isinstance(meeting, dict) or not meeting.get('confirmationEvidence') or not meeting.get('mode'):
            raise PolicyError('interview-confirmation-evidence-required')
        try:
            parsed = datetime.fromisoformat(meeting['startAt'])
            if parsed.tzinfo is None:
                raise ValueError()
        except (KeyError, TypeError, ValueError) as error:
            raise PolicyError('interview-time-and-timezone-required') from error
