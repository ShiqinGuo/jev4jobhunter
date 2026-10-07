"""Mobile contracts without device, capture or storage side effects."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json

class MobileError(RuntimeError):
    """Boundary/invariant failure; callers preserve its exception chain."""

class DeviceUnavailable(MobileError):
    pass

class SubmissionUncertain(MobileError):
    """Permission was issued; only evidence may resolve the original action."""

class IdentityChanged(MobileError):
    pass

class NavigationError(MobileError):
    pass

class TargetUnavailable(NavigationError):
    """A bounded lookup could not prove one intended recipient."""

class PlatformBlocked(MobileError):
    pass

class ProtocolError(MobileError):
    def __init__(self, stage: str, reason: str):
        self.stage = stage
        super().__init__(reason)

class LocateStatus(StrEnum):
    FOUND = 'found'
    NOT_VISIBLE = 'not-visible'
    DETAIL_MISSING = 'detail-missing'
    AMBIGUOUS = 'ambiguous'

@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    app_version: str

    @property
    def key(self) -> str:
        return f'{self.width}x{self.height}@{self.app_version}'

@dataclass(frozen=True)
class Runtime:
    serial: str
    layout: Layout

@dataclass(frozen=True)
class Binding:
    serial: str
    account_id: str
    account_label: str

@dataclass(frozen=True)
class Marker:
    context_id: str
    action_id: str
    account_id: str
    account_label: str
    operation: str
    target_key: str | None
    at: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> Marker:
        for field in ('context_id', 'action_id', 'account_id', 'account_label', 'operation'):
            if not isinstance(value.get(field), str) or not value[field]:
                raise MobileError('invalid-marker:' + field)
        if not isinstance(value.get('at'), (int, float)):
            raise MobileError('invalid-marker:at')
        return cls(**{name: value[name] for name in cls.__dataclass_fields__})

@dataclass(frozen=True)
class Evidence:
    action_id: str
    account_id: str
    account_label: str
    target_key: str
    source_id: str
    outcome: str
    observed_content: str | None = None
    details: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass(frozen=True)
class Observation:
    evidence: Evidence | None = None
    diagnostics: tuple[str, ...] = ()

@dataclass(frozen=True)
class LocatedJob:
    status: LocateStatus
    job: dict | None = None

def digest(value) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def jd_digest(job: dict) -> str:
    fields = ('key', 'title', 'company', 'city', 'salary', 'experience', 'degree', 'text', 'publisherType')
    return digest({name: job.get(name, '') for name in fields})

def judgment_context(profile_hash: str | None, policy: dict, question_version: int) -> str:
    return digest({'profile': profile_hash, 'questionVersion': question_version,
                   'keywords': policy.get('targets', {}).get('keywords', []),
                   'roleFocus': policy.get('search', {}).get('roleFocus', '')})
