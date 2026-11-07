from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class GoalStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class MilestoneStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"


class ActivityStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"


class ActivityOrigin(StrEnum):
    UI = "ui"
    ASSISTANT = "assistant"
    VOICE = "voice"


class CalendarItemKind(StrEnum):
    SESSION = "session"
    EVENT = "event"


class CalendarItemStatus(StrEnum):
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"


class AmigoServiceError(ValueError):
    """A user-correctable validation or reference-resolution problem."""


class AmbiguousReferenceError(AmigoServiceError):
    def __init__(self, kind: str, query: str, candidates: list[str]) -> None:
        self.kind = kind
        self.query = query
        self.candidates = candidates
        joined = ", ".join(candidates)
        super().__init__(f"More than one {kind} matches '{query}': {joined}. Please choose one.")


@dataclass(frozen=True)
class GoalProgress:
    goal_id: str
    milestone_total: int
    milestone_completed: int
    activity_count: int
    activity_minutes: int
    last_activity_at: str | None


@dataclass(frozen=True)
class MaterialRecord:
    id: str
    title: str
    relative_path: str
    media_type: str
    size_bytes: int
    sha256: str
    imported_at: str


@dataclass(frozen=True)
class CalendarEntryRecord:
    id: str
    kind: CalendarItemKind
    title: str
    description: str
    starts_at: str
    ends_at: str
    status: CalendarItemStatus
    goal_names: str = ""
    topic_names: str = ""
