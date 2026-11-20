"""compress.py --run: pending samples go before the next film, one at a time, never beside an encode."""
import os, sqlite3, sys, types

os.environ["HANDBRAKE_CLI"] = "flatpak run fr.handbrake.ghb"
import compress  # noqa: E402

calls = []


def _enqueue(conn, kind, oid, jk, q, start=None):
    calls.append((oid, jk, q, start))
    conn.execute("INSERT INTO jobs (movie_id, kind, status, cmd) VALUES (?,?,?,?)",
                 (oid, jk, "queued", f"x '/m/.O.sample.rf{q}.t{start}.mkv'"))
    return {"job_id": len(calls)}


sys.modules["app"] = types.SimpleNamespace(_enqueue=_enqueue, _mark_auto_finalize=lambda *a: None)


def main():
    c = sqlite3.connect(":memory:")
    c.executescript("""
      CREATE TABLE movies (id INTEGER PRIMARY KEY, folder TEXT, file TEXT, height INT, bitrate INT, status TEXT);
      CREATE TABLE jobs (id INTEGER PRIMARY KEY, movie_id INT, kind TEXT, status TEXT, cmd TEXT);
      INSERT INTO movies VALUES (474, 'O (2026)', 'O.mkv', 2160, 1, 'clean');
      INSERT INTO jobs (movie_id, kind, status) VALUES (9, 'encode', 'running');
    """)
    compress.SAMPLES = [("O (2026)", 100, 20), ("O (2026)", 100, 22), ("O (2026)", 200, 20)]
    assert compress.queue_next(c) is None and not calls, "a sample must wait for the running encode"
    c.execute("UPDATE jobs SET status='verified'")
    q = compress.queue_next(c)
    assert q["sample"] == "rf20 t100" and calls == [(474, "sample", 20, 100)], (q, calls)
    assert compress.queue_next(c) is None, "one sample at a time"
    c.execute("UPDATE jobs SET status='failed' WHERE kind='sample'")          # a failed pair is not retried
    assert compress.queue_next(c)["sample"] == "rf22 t100"
    print("test_compress OK")


if __name__ == "__main__":
    main()
