"""Integration fixtures: a dedicated `<db>_test` database, migrated with Alembic.

Skipped automatically when PostgreSQL at DATABASE_URL (or TEST_DATABASE_URL)
is not reachable within a couple of seconds — unless REQUIRE_DB=1 (CI), which
turns the skip into a failure so a missing database can't pass silently.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentops.config import Settings
from agentops.infrastructure.db.session import create_engine, create_session_factory

BACKEND_DIR = Path(__file__).resolve().parents[2]
CONNECT_TIMEOUT_S = 2.0


def _skip_or_fail(reason: str) -> None:
    if os.environ.get("REQUIRE_DB") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


def _test_database_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    try:
        url = make_url(Settings().database_url)
    except ValidationError as exc:
        _skip_or_fail(f"DATABASE_URL is not usable for integration tests: {exc.errors()[0]['msg']}")
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


async def _ensure_database(url: str) -> None:
    target = make_url(url)
    dsn = target.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    conn = await asyncpg.connect(dsn, timeout=CONNECT_TIMEOUT_S)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", target.database)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{target.database}"')
    finally:
        await conn.close()


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _test_database_url()
    try:
        asyncio.run(_ensure_database(url))
    except (OSError, TimeoutError, asyncpg.PostgresError) as exc:
        _skip_or_fail(f"PostgreSQL not reachable for integration tests ({type(exc).__name__}: {exc})")
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
async def session_factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(database_url)
    async with engine.begin() as conn:
        # CASCADE: message_retrievals (and later tables) reference messages.
        await conn.execute(text("TRUNCATE conversations, messages, documents CASCADE"))
    yield create_session_factory(engine)
    await engine.dispose()
