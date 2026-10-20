"""app._bazarr_blacklist: the right endpoint and form for movies/episodes; silent without key or ids."""
import http.server, os, sys, threading, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402

seen = []


class Fake(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        seen.append((self.path, self.headers.get("X-API-KEY"), urllib.parse.parse_qs(body)))
        self.send_response(500)                    # Bazarr's delete of the recycled file fails, as on 10-18
        self.end_headers()

    def log_message(self, *a):
        pass


def test_bazarr_blacklist():
    srv = http.server.HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ.pop("BAZARR_API_KEY", None)
    meta = {"provider": "subx", "subtitle_id": "abc", "language": "es", "radarr_id": "351"}
    assert app._bazarr_blacklist(meta, "/hdd1/Movies/X/X.es-MX.srt") == ""          # no key: nothing sent
    os.environ.update(BAZARR_URL=f"http://127.0.0.1:{srv.server_port}", BAZARR_API_KEY="k")
    note = app._bazarr_blacklist(meta, "/hdd1/Movies/X/X.es-MX.srt")
    assert "HTTP 500" in note and seen[-1][0] == "/api/movies/blacklist?radarrid=351" and seen[-1][1] == "k", seen
    assert seen[-1][2] == {"provider": ["subx"], "subs_id": ["abc"], "language": ["es"],
                           "subtitles_path": ["/hdd1/Movies/X/X.es-MX.srt"]}, seen[-1]
    ep = {"provider": "subdl", "subtitle_id": "z", "language": "en", "series_id": "7", "episode_id": "99"}
    app._bazarr_blacklist(ep, "/hdd1/Shows/S/e.en.srt")
    assert seen[-1][0] == "/api/episodes/blacklist?seriesid=7&episodeid=99", seen[-1]
    assert app._bazarr_blacklist({"provider": "subx", "language": "es", "radarr_id": "1"}, "p") == ""   # no subs id
    # what Bazarr really sends for a movie: empty series_id, radarrId in episode_id, language with a suffix
    app._bazarr_blacklist({"provider": "subx", "subtitle_id": "q", "language": "es:hi", "series_id": "",
                           "episode_id": "351"}, "/hdd1/Movies/Y/Y.es-MX.srt")
    assert seen[-1][0] == "/api/movies/blacklist?radarrid=351" and seen[-1][2]["language"] == ["es"], seen[-1]
    srv.shutdown()
    print("test_bazarr_blacklist OK")


if __name__ == "__main__":
    test_bazarr_blacklist()
