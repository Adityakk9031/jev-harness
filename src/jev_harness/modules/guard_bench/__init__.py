"""guard_bench: do my hooks and permission rules block disguised dangerous commands?"""

from __future__ import annotations

from jev_harness.modules.guard_bench.service import GuardOptions, execute, plan

__all__ = ["GuardOptions", "execute", "plan"]
