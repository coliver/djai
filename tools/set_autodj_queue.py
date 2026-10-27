"""Put a set plan's tracks at the top of Mixxx's Auto DJ queue. Mixxx must be closed.

    set_autodj_queue.py <mixxxdb.sqlite> sets/plan.json <first index>

DJAI uses Auto DJ only as a track loader: enabling it while one deck plays
loads the next queued track into the other deck, and disabling it straight
away leaves the decks and mixer alone. Auto DJ removes each set track from
the queue as it plays, so whatever was queued before ends up back on top.
"""
import datetime
import json
import sqlite3
import sys


def main(db_path, plan_path, first_index):
    tracks = json.load(open(plan_path, encoding="utf-8"))[int(first_index):]
    db = sqlite3.connect(db_path)
    (playlist_id,) = db.execute("select id from Playlists where hidden = 1").fetchone()
    previous = db.execute("select count(*) from PlaylistTracks where playlist_id = ?", (playlist_id,)).fetchone()[0]
    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with db:
        db.execute("update PlaylistTracks set position = position + ? where playlist_id = ?", (len(tracks), playlist_id))
        for position, t in enumerate(tracks, start=1):
            db.execute("insert into PlaylistTracks (playlist_id, track_id, position, pl_datetime_added) values (?, ?, ?, ?)",
                       (playlist_id, t["id"], position, now))
    print(f"Auto DJ playlist {playlist_id}: {previous} tracks were queued, now below the set")
    print(f"queued {len(tracks)} tracks: {[t['id'] for t in tracks]}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
