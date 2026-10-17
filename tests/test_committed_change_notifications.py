"""提交信号只含表名；回滚、嵌套事务和工作线程边界。"""

import asyncio

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from test_storage import database

from app.storage.database import Database
from app.storage.models import MCPServerRecord, SystemSettingRecord


@pytest.mark.asyncio
async def test_flush_and_rollback_never_publish(database):
    async with database.changes.watch() as queue:
        with database.new_session() as session:
            session.add(
                SystemSettingRecord(
                    section="fixture", value_json={"secret": "not-in-events"}
                )
            )
            session.flush()
            await asyncio.sleep(0)
            assert queue.empty()
            session.rollback()
        await asyncio.sleep(0)
        assert queue.empty()


@pytest.mark.asyncio
async def test_nested_commit_waits_for_outer_commit(database):
    async with database.changes.watch() as queue:
        with database.new_session() as session:
            session.connection().exec_driver_sql("BEGIN")
            with session.begin_nested():
                session.add(
                    SystemSettingRecord(
                        section="fixture", value_json={"secret": "not-in-events"}
                    )
                )
            await asyncio.sleep(0)
            assert queue.empty()
            session.commit()
        assert await asyncio.wait_for(queue.get(), 1) == {"system_settings"}


@pytest.mark.asyncio
async def test_nested_rollback_preserves_only_outer_changes(database):
    async with database.changes.watch() as queue:
        with database.new_session() as session:
            session.add(SystemSettingRecord(section="outer", value_json={}))
            nested = session.begin_nested()
            session.add(
                MCPServerRecord(name="rolled-back", type="stdio", config_json={})
            )
            session.flush()
            nested.rollback()
            session.commit()
        assert await asyncio.wait_for(queue.get(), 1) == {"system_settings"}


@pytest.mark.asyncio
async def test_outer_rollback_discards_nested_commit(database):
    async with database.changes.watch() as queue:
        with database.new_session() as session:
            session.connection().exec_driver_sql("BEGIN")
            with session.begin_nested():
                session.add(SystemSettingRecord(section="fixture", value_json={}))
            session.rollback()
        await asyncio.sleep(0)
        assert queue.empty()
        with database.new_session() as session:
            assert session.get(SystemSettingRecord, "fixture") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("commit", [True, False])
async def test_bulk_delete_respects_commit_boundary(database, commit):
    with database.session_scope() as session:
        session.add(SystemSettingRecord(section="bulk", value_json={}))
    async with database.changes.watch() as queue:
        with database.new_session() as session:
            session.execute(
                delete(SystemSettingRecord).where(SystemSettingRecord.section == "bulk")
            )
            if commit:
                session.commit()
            else:
                session.rollback()
        await asyncio.sleep(0)
        if commit:
            assert queue.get_nowait() == {"system_settings"}
        else:
            assert queue.empty()


@pytest.mark.asyncio
async def test_failed_commit_does_not_publish(database):
    with database.session_scope() as session:
        session.add(SystemSettingRecord(section="duplicate", value_json={}))
    async with database.changes.watch() as queue:
        with database.new_session() as session:
            session.add(SystemSettingRecord(section="duplicate", value_json={}))
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
        await asyncio.sleep(0)
        assert queue.empty()


@pytest.mark.asyncio
async def test_threaded_commits_coalesce_and_handles_share_hub(database):
    other = Database(database.path)
    try:
        assert other.changes is database.changes
        async with database.changes.watch() as queue:

            def write():
                for index in range(5):
                    with other.session_scope() as session:
                        session.add(
                            SystemSettingRecord(
                                section=f"fixture-{index}",
                                value_json={"password": "never-published"},
                            )
                        )
                with other.session_scope() as session:
                    session.add(
                        MCPServerRecord(name="fixture", type="stdio", config_json={})
                    )

            await asyncio.to_thread(write)
            await asyncio.sleep(0)
            assert queue.qsize() == 1
            assert queue.get_nowait() == {"system_settings", "mcp_servers"}
        with other.session_scope() as session:
            session.add(SystemSettingRecord(section="after-close", value_json={}))
        await asyncio.sleep(0)
        assert queue.empty()
    finally:
        other.dispose()
