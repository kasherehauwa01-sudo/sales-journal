"""Retired module: import compatibility for reviewed updater lock preflight only.
No scans, aggregation, fitting or worker entry points remain.
"""

from app.services.source_control import (
    SOURCE_LOCK,
    WORKER_LOCK,
    CATALOG_LOCK,
    advisory,
    verify,
    ResourceBusy,
)

SeasonalityBusy = ResourceBusy
