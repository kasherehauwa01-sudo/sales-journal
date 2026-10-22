# Backend SIGTERM forwarding

Final image verification of Sales Journal `0e33c2c` found that the default
`sh -c 'alembic upgrade head && uvicorn ...'` keeps the shell in front of
Uvicorn. All five isolated backend containers required a forced stop and
exited 137 after the Docker stop timeout. This is separate from CHURN_RISK
formulas and was not a production test.

The startup command now uses `&& exec uvicorn ...`. Alembic still runs first;
a failed migration still prevents application startup. After successful
migration Uvicorn replaces the shell, receives SIGTERM, and finishes the
FastAPI lifespan cleanup before Docker's timeout.

Regression test (local Docker and an explicitly prepared disposable PostgreSQL
fixture only):

```sh
BACKEND_SHUTDOWN_TEST=1 BACKEND_SHUTDOWN_TEST_IMAGE=churn-sigterm:fixed pytest -q backend/tests/test_backend_container_shutdown.py
```

The test restricts the image names and hardcodes the isolated database
`shutdown_test` on `churn-sigterm-test-pg` in `rollback-churn-test`. It does not
use the application's database settings or production credentials. It starts
an owned container, checks real HTTP health, sends Docker stop/SIGTERM, and
requires exit0 plus `Application shutdown complete` before the five-second
timeout. Containers are stopped and retained for audit, not deleted.

Observed: original target image failed with exit137; the fixed image passed.
The explicit opt-in is required; ordinary unit runs skip this container test.

This patch changes neither migrations nor feature flags, source evidence,
policies, formulas, assignments, or automation defaults. Production deployment
remains BLOCKED — NO VERIFIED RECOVERY. The original requested SHA is not
silently replaced: this separate branch requires review and a separately
approved deployment target. The real old production image/schema24 rollback
remains unverified in Codex.
