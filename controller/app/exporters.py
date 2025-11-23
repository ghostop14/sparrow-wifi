from __future__ import annotations

from typing import Any, Dict

from .config import get_settings
from .events import event_bus


class ElasticExporter:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint.rstrip('/')

    def handle_scan_completed(self, payload: Dict[str, Any]) -> None:
        # Placeholder: write to stdout for now.  Real implementation would POST to Elastic.
        print(f"[ElasticExporter] would send payload to {self.endpoint}: keys={list(payload.keys())}")


def setup_exporters() -> None:
    settings = get_settings()
    if settings.elastic_url:
        exporter = ElasticExporter(settings.elastic_url)
        event_bus.subscribe('scan.completed', exporter.handle_scan_completed)
