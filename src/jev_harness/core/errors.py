"""Shared error types and process exit codes.

Exit codes are part of the public contract of the CLI (PLAN D9) and are shared
by every module so that CI wiring never has to guess:

* ``EXIT_OK``    - nothing at or above the configured ``--fail-on`` severity.
* ``EXIT_FAIL``  - the fail-on condition was met.
* ``EXIT_ERROR`` - the harness itself could not run (bad config, missing
  credential, budget exhausted). Never used to mean "the thing under test
  failed".
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_ERROR = 2


class HarnessError(Exception):
    """Base class for errors the CLI reports as exit code 2 without a traceback."""


class ConfigError(HarnessError):
    """A configuration file or environment value is missing, unknown, or invalid."""


class BudgetExceededError(HarnessError):
    """A run would have exceeded ``--max-cost`` or ``--max-runs``."""
