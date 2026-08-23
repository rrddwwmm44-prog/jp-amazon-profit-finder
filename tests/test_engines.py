import tempfile
import unittest
from pathlib import Path

from app.config import FeatureFlags, Settings
from app.engines.base import EngineContext, EngineResult, EngineStatus
from app.engines.registry import EngineRegistry, UnknownEngineError
from app.storage.db import Database


class RecordingEngine:
    def __init__(self,name,events,fail=False): self.name=name; self.events=events; self.fail=fail
    def run(self,context):
        self.events.append(self.name)
        if self.fail: raise RuntimeError("engine boom")
        return EngineResult(self.name,EngineStatus.SUCCESS,processed_count=2,candidate_count=1)


class EngineTests(unittest.TestCase):
    def context(self,path,flags):
        return EngineContext(Settings(path,500,.15,85,"INFO",flags),Database(path),"mock")

    def test_registry_registers_and_gets_engine(self):
        registry=EngineRegistry(); engine=RecordingEngine("market_price",[]); registry.register(engine)
        self.assertIs(registry.get("market_price"),engine)
        self.assertEqual(registry.names(),("market_price",))

    def test_disabled_engine_is_not_run(self):
        with tempfile.TemporaryDirectory() as raw:
            events=[]; registry=EngineRegistry(); registry.register(RecordingEngine("market_price",events))
            result=registry.run_one("market_price",self.context(Path(raw)/"db.sqlite",FeatureFlags(market_price=False)))
            self.assertEqual(result.status,EngineStatus.SKIPPED); self.assertEqual(events,[])

    def test_enabled_engine_runs(self):
        with tempfile.TemporaryDirectory() as raw:
            events=[]; registry=EngineRegistry(); registry.register(RecordingEngine("market_price",events))
            result=registry.run_one("market_price",self.context(Path(raw)/"db.sqlite",FeatureFlags()))
            self.assertEqual(result.status,EngineStatus.SUCCESS); self.assertEqual(events,["market_price"])

    def test_failed_engine_does_not_stop_next_engine(self):
        with tempfile.TemporaryDirectory() as raw:
            events=[]; registry=EngineRegistry()
            registry.register(RecordingEngine("market_price",events,fail=True)); registry.register(RecordingEngine("seller_decline",events))
            flags=FeatureFlags(market_price=True,seller_decline=True)
            results=registry.run_enabled(self.context(Path(raw)/"db.sqlite",flags))
            self.assertEqual([r.status for r in results],[EngineStatus.FAILED,EngineStatus.SUCCESS])
            self.assertEqual(events,["market_price","seller_decline"])

    def test_unknown_engine_is_safe_error(self):
        registry=EngineRegistry()
        with self.assertRaises(UnknownEngineError): registry.get("missing")
