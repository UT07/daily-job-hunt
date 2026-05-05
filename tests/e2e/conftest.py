"""Pytest config for tests/e2e.

`test_full_system.py` is a standalone smoke-test script that hits the live
production API and calls `sys.exit(...)` at module top level (see its
docstring: "Run with: python tests/e2e/test_full_system.py"). Pytest auto-
discovers it by name but importing it during collection crashes the run with
INTERNALERROR. Skip it from collection — it's still runnable as a script.
"""

collect_ignore = ["test_full_system.py"]
