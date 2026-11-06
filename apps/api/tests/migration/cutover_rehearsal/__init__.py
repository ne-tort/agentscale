"""Package for the end-to-end cutover rehearsal (task ``MIG-REHEARSAL-01``).

Run it with a migrated PostgreSQL:

.. code-block:: powershell

    cd apps/api
    $env:PYTHONPATH="<repo>\\apps\\api\\src"
    $env:PRODAVAN_TEST_DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:55432/mig_fresh"
    python -m pytest tests/migration/cutover_rehearsal -q --no-header -p no:cacheprovider

Without ``PRODAVAN_TEST_DATABASE_URL`` every module skips with a printed reason
(:mod:`harness`), never silently: a rehearsal that reported "passed" while running
nothing would be worse than no rehearsal at all.
"""

from __future__ import annotations
