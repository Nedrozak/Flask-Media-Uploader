# Contributing

Thank you for improving Media Uploader.

## Development setup

1. Create a Python 3.11+ virtual environment.
2. Upgrade pip, then install the project and development group with `python -m pip install -c constraints.lock -e . --group dev`.
3. Start Redis locally.
4. Copy `.env.example` to `.env` and set a random `FLASK_SECRET_KEY`.
5. Run the app with `media-uploader`.

## Quality checks

Run these before opening a pull request:

```bash
ruff format --check .
ruff check .
mypy media_uploader tests tools
pytest --cov=media_uploader --cov-report=term-missing
pip-audit
```

JavaScript files should also pass `node --check media_uploader/static/scripts/index.js` and `node --check media_uploader/static/scripts/summary.js`.

For browser E2E coverage, keep Redis running, then install the E2E group and Chromium and run:

```bash
python -m pip install -c constraints.lock -e . --group e2e
python -m playwright install chromium
pytest e2e --browser chromium
```

The E2E fixture launches and stops the application automatically on a free localhost port; do not start a second `media-uploader` process for the test. Set `E2E_REDIS_URL` if Redis is not available at `redis://127.0.0.1:6379/15`.

## Pull requests

Keep changes focused, include tests for behavior changes, avoid committing generated archives or local environment files, and document any new environment variables in `.env.example` and `README.md`.

## Dependency lock

Keep dependency declarations in `pyproject.toml`. `constraints.lock` is generated runtime output, not a second source of truth. Regenerate it only as part of an intentional dependency update and run unit, integration, E2E, audit, and Docker checks before committing the new resolution.
