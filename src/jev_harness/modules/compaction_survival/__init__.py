"""compaction: which of my agent's instructions survive context compaction?"""

from __future__ import annotations

from jev_harness.modules.compaction_survival.real import (
    CompactionNotConfirmedError,
    HooksNotRunningError,
    RealRunners,
)
from jev_harness.modules.compaction_survival.service import (
    CompactionOptions,
    execute_real,
    execute_simulated,
    plan_lines,
)

__all__ = [
    "CompactionNotConfirmedError",
    "CompactionOptions",
    "HooksNotRunningError",
    "RealRunners",
    "execute_real",
    "execute_simulated",
    "plan_lines",
]
