"""Bounded, allowlisted host telemetry. Never executes commands or authorizes work."""
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import stat
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, field_validator

MAX_BYTES = 131072
MAX_AGE = timedelta(hours=36)


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @field_validator('*', mode='after', check_fields=False)
    @classmethod
    def aware_dates(cls, value):
        if isinstance(value, datetime) and value.utcoffset() is None:
            raise ValueError('Timezone required')
        return value


class Budget(Record):
    limit: StrictInt | None = Field(default=None, ge=0)
    used: StrictInt | None = Field(default=None, ge=0)
    approved: StrictBool | None = None
    ledger_used: StrictInt | None = Field(default=None, ge=0)
    business_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    policy_version: str | None = Field(default=None, max_length=64)


class ManagerBudget(Budget):
    manager_id: Literal[3, 6]


class Receipt(Record):
    release_id: str = Field(max_length=36, pattern=r'^[a-zA-Z0-9-]+$')
    accepted: StrictInt = Field(ge=0)
    blocked: StrictInt = Field(ge=0)
    tasks: StrictInt = Field(ge=0)


class Receiver(Record):
    status: Literal['available', 'unavailable', 'unauthorized', 'unknown'] = 'unknown'
    policy_approved: StrictBool | None = None
    churn_enabled: StrictBool | None = None
    priority: Literal['LOW', 'NORMAL', 'HIGH', 'URGENT'] | None = None
    due_days: StrictInt | None = Field(default=None, ge=0, le=365)
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    approved_generators: list[Annotated[str, StringConstraints(pattern=r'^inactivity/[a-z0-9/-]{1,64}$')]] | None = Field(default=None, max_length=10)
    accepted_signals: StrictInt | None = Field(default=None, ge=0)
    blocked_signals: StrictInt | None = Field(default=None, ge=0)
    tasks_created: StrictInt | None = Field(default=None, ge=0)
    lifetime: Budget | None = None
    daily: Budget | None = None
    manager_budgets: list[ManagerBudget] = Field(default_factory=list, max_length=2)
    receipts: list[Receipt] = Field(default_factory=list, max_length=30)


class HostRun(Record):
    id: str = Field(max_length=64, pattern=r'^[a-zA-Z0-9-]+$')
    observed_at: datetime
    result: Literal['success', 'failed', 'blocked', 'unknown']
    codes: list[str] = Field(default_factory=list, max_length=20)


class Systemd(Record):
    timer_enabled: StrictBool | None = None
    timer_active: StrictBool | None = None
    service_state: Literal['active', 'inactive', 'failed', 'activating', 'deactivating', 'unknown'] = 'unknown'
    service_result: Literal['success', 'failed', 'unknown'] = 'unknown'
    last_attempt: datetime | None = None
    last_success: datetime | None = None
    next_run: datetime | None = None
    schedule: Literal['09:00 Europe/Moscow'] | None = None


class Telemetry(Record):
    schema_version: Literal[1]
    collected_at: datetime
    systemd: Systemd = Field(default_factory=Systemd)
    integrity: Literal['match', 'mismatch', 'unknown'] = 'unknown'
    receiver: Receiver = Field(default_factory=Receiver)
    history: list[HostRun] = Field(default_factory=list, max_length=20)
    codes: list[str] = Field(default_factory=list, max_length=20)


def read_private_json(path: Path, *, max_bytes=MAX_BYTES, owners=None):
    """Reject symlinks, writable/untrusted files, large input and FIFO/devices."""
    owners = {0} if owners is None else owners
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in owners or info.st_mode & 0o022:
            raise ValueError('Untrusted telemetry permissions')
        if info.st_size > max_bytes:
            raise ValueError('Telemetry too large')
        with os.fdopen(descriptor, 'rb', closefd=False) as source:
            data = source.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError('Telemetry too large')
        return json.loads(data)
    finally:
        os.close(descriptor)


def load_telemetry(path: Path, now: datetime):
    try:
        telemetry = Telemetry.model_validate(read_private_json(path))
    except FileNotFoundError:
        return None, 'RUNTIME_STATUS_MISSING'
    except (OSError, ValueError, TypeError):
        return None, 'RUNTIME_STATUS_INVALID'
    if telemetry.collected_at > now + timedelta(minutes=5) or now - telemetry.collected_at > MAX_AGE:
        return None, 'RUNTIME_STATUS_STALE'
    return telemetry, None
