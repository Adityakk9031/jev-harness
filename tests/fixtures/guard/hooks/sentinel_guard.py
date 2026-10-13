"""Writes a file named by $JEV_SENTINEL when it runs; proves --dry-run executes nothing."""

import os
import sys

with open(os.environ["JEV_SENTINEL"], "a") as handle:
    handle.write("ran\n")
sys.exit(0)
