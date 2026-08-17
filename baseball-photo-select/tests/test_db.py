"""State machine and schema behaviour (spec 02 §2, §3)."""
from __future__ import annotations

import pytest

from bps import db as dbmod
from bps.db import TransitionError


def add_photo(database, name="DSC00001.JPG", new_name="20260720_133005_000_DSC00001.jpg"):
    return database.insert_photo(
        orig_name=name,
        new_name=new_name,
        file_number=1,
        shot_time="2026-07-20 13:30:05.000",
        received_at=1_700_000_000.0,
        arw_name=name.replace(".JPG", ".ARW"),
    )


def test_schema_and_version(database):
    assert database.get_meta("schema_version") == dbmod.SCHEMA_VERSION
    tables = {
        row[0]
        for row in database.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"photos", "groups", "name_map", "meta"} <= tables


def test_init_schema_is_idempotent(database):
    add_photo(database)
    database.init_schema()
    assert database.counts_by_state()[dbmod.RECEIVED] == 1


def test_insert_registers_name_map(database):
    add_photo(database)
    assert database.arw_name_for("20260720_133005_000_DSC00001.jpg") == "DSC00001.ARW"


def test_new_name_is_unique(database):
    add_photo(database)
    with pytest.raises(Exception):
        add_photo(database, name="OTHER.JPG")


def test_transition_moves_state(database):
    pid = add_photo(database)
    database.transition(pid, dbmod.RECEIVED, dbmod.VERIFIED)
    assert database.get_photo(pid)["state"] == dbmod.VERIFIED


def test_transition_updates_extra_columns(database):
    pid = add_photo(database)
    gid = database.create_group("2026-07-20 13:30:05.000", "2026-07-20 13:30:05.000", 1.0)
    database.transition(pid, dbmod.RECEIVED, dbmod.VERIFIED)
    database.transition(pid, dbmod.VERIFIED, dbmod.GROUPED, group_id=gid)
    assert database.get_photo(pid)["group_id"] == gid


def test_group_id_must_reference_real_group(database):
    """Foreign keys are on: a photo cannot point at a group that does not exist."""
    pid = add_photo(database)
    database.transition(pid, dbmod.RECEIVED, dbmod.VERIFIED)
    with pytest.raises(Exception):
        database.transition(pid, dbmod.VERIFIED, dbmod.GROUPED, group_id=999)


def test_transition_serialises_json_columns(database):
    pid = add_photo(database)
    database.transition(
        pid, dbmod.RECEIVED, dbmod.VERIFIED, af_json={"x": 1, "center_suspect": False}
    )
    assert '"center_suspect": false' in database.get_photo(pid)["af_json"]


def test_transition_rejects_wrong_source_state(database):
    """The double-processing guard: two workers cannot both advance a photo."""
    pid = add_photo(database)
    database.transition(pid, dbmod.RECEIVED, dbmod.VERIFIED)
    with pytest.raises(TransitionError, match="expected state"):
        database.transition(pid, dbmod.RECEIVED, dbmod.VERIFIED)


def test_transition_on_missing_photo(database):
    with pytest.raises(TransitionError, match="missing"):
        database.transition(999, dbmod.RECEIVED, dbmod.VERIFIED)


def test_transition_rejects_unknown_state(database):
    pid = add_photo(database)
    with pytest.raises(ValueError, match="unknown target state"):
        database.transition(pid, dbmod.RECEIVED, "NOPE")


def test_transition_rejects_unknown_column(database):
    pid = add_photo(database)
    with pytest.raises(ValueError, match="not updatable"):
        database.transition(pid, dbmod.RECEIVED, dbmod.VERIFIED, state_hack=1)


def test_counts_cover_all_states(database):
    counts = database.counts_by_state()
    assert set(counts) == set(dbmod.ALL_STATES)
    assert all(v == 0 for v in counts.values())


def test_photos_in_states_and_errors(database):
    pid = add_photo(database)
    database.transition(pid, dbmod.RECEIVED, dbmod.FAILED, error="boom")
    assert [r["id"] for r in database.photos_in_states(dbmod.RESUMABLE_STATES)] == [pid]
    assert database.recent_errors()[0]["error"] == "boom"


def test_groups_lifecycle(database):
    gid = database.create_group("2026-07-20 13:30:05.000", "2026-07-20 13:30:06.000", 1.0)
    assert database.count_open_groups() == 1
    with database.conn:
        database.conn.execute("UPDATE groups SET finalized_at = 2.0 WHERE id = ?", (gid,))
    assert database.count_open_groups() == 0


def test_file_numbers_excludes_unknown(database):
    add_photo(database)
    database.insert_photo(
        orig_name="IMG.JPG",
        new_name="20260720_133006_000_IMG.jpg",
        file_number=-1,
        shot_time="2026-07-20 13:30:06.000",
        received_at=1.0,
    )
    assert database.file_numbers() == {1}


def test_session_start_recorded(database):
    started = database.start_session(now=123.5)
    assert started == 123.5
    assert float(database.get_meta("session_started_at")) == 123.5
