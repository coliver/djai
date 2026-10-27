import sqlite3

import pytest
import set_autodj_queue
from conftest import write_json


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "mixxxdb.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
        create table Playlists (id integer primary key, name text, hidden integer);
        create table PlaylistTracks (id integer primary key autoincrement, playlist_id integer, track_id integer,
                                     position integer, pl_datetime_added text);
        insert into Playlists values (1, 'Mine', 0), (2, 'Auto DJ', 1);
        insert into PlaylistTracks (playlist_id, track_id, position) values (2, 900, 1), (2, 901, 2), (1, 5, 1);
    """)
    db.commit()
    db.close()
    return path


@pytest.fixture
def plan(tmp_path):
    path = tmp_path / "plan.json"
    write_json(path, [{"id": 10}, {"id": 11}, {"id": 12}])
    return path


def queue(path):
    db = sqlite3.connect(path)
    rows = db.execute("select track_id, position from PlaylistTracks where playlist_id = 2 order by position").fetchall()
    db.close()
    return rows


def test_set_goes_on_top_and_the_old_queue_moves_down(db_path, plan, capsys):
    set_autodj_queue.main(str(db_path), str(plan), "1")
    assert queue(db_path) == [(11, 1), (12, 2), (900, 3), (901, 4)]
    out = capsys.readouterr().out
    assert "2 tracks were queued, now below the set" in out
    assert "queued 2 tracks: [11, 12]" in out


def test_other_playlists_are_untouched(db_path, plan):
    set_autodj_queue.main(str(db_path), str(plan), "0")
    db = sqlite3.connect(db_path)
    assert db.execute("select track_id, position from PlaylistTracks where playlist_id = 1").fetchall() == [(5, 1)]
    stamp = db.execute("select pl_datetime_added from PlaylistTracks where track_id = 10").fetchone()[0]
    assert stamp.endswith(".000Z") and "T" in stamp


def test_nothing_is_changed_when_an_insert_fails(db_path, plan):
    db = sqlite3.connect(db_path)
    db.execute("create unique index one on PlaylistTracks (playlist_id, track_id)")
    db.commit()
    db.close()
    set_autodj_queue.main(str(db_path), str(plan), "0")  # unique per playlist, fine
    with pytest.raises(sqlite3.IntegrityError):
        set_autodj_queue.main(str(db_path), str(plan), "0")  # same tracks again: rolled back
    assert [t for t, _ in queue(db_path)] == [10, 11, 12, 900, 901]


def test_runs_as_a_script(db_path, plan, run_script):
    run_script(set_autodj_queue, str(db_path), str(plan), "2")
    assert queue(db_path)[0] == (12, 1)
