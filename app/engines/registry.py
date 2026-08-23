from __future__ import annotations

from app.config import FeatureFlags
from app.engines.base import Engine, EngineContext, EngineResult, EngineStatus


class UnknownEngineError(LookupError):
    pass


class EngineRegistry:
    def __init__(self) -> None:
        self._engines: dict[str, Engine] = {}

    def register(self, engine: Engine) -> None:
        if engine.name in self._engines:
            raise ValueError(f"engine already registered: {engine.name}")
        self._engines[engine.name] = engine

    def get(self, name: str) -> Engine:
        try:
            return self._engines[name]
        except KeyError as exc:
            raise UnknownEngineError(f"unknown engine: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._engines)

    def enabled_names(self, flags: FeatureFlags) -> tuple[str, ...]:
        return tuple(name for name in self._engines if flags.is_enabled(name))

    def run_one(self, name: str, context: EngineContext) -> EngineResult:
        engine = self.get(name)
        if not context.settings.engine_flags.is_enabled(name):
            return EngineResult(name, EngineStatus.SKIPPED)
        try:
            return engine.run(context)
        except Exception as exc:
            return EngineResult(name, EngineStatus.FAILED, error=str(exc))

    def run_enabled(self, context: EngineContext) -> list[EngineResult]:
        return [self.run_one(name, context) for name in self.enabled_names(context.settings.engine_flags)]
