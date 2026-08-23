from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.config import Settings
from app.storage.db import Database


class EngineStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class EngineContext:
    settings: Settings
    db: Database
    mode: str


@dataclass(frozen=True)
class EngineResult:
    engine_name: str
    status: EngineStatus
    processed_count: int = 0
    candidate_count: int = 0
    error: str | None = None
    job_id: int | None = None


class Engine(Protocol):
    name: str

    def run(self, context: EngineContext) -> EngineResult: ...
