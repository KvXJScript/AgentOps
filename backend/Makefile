.PHONY: install lint format typecheck test test-integration dev migrate check

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy
	uv run mypy tests

test:
	uv run pytest -m "not integration"

test-integration:
	uv run pytest -m integration

dev:
	uv run uvicorn agentops.main:app_factory --factory --reload --port 8000 --no-access-log

migrate:
	uv run alembic upgrade head

check: lint typecheck test
