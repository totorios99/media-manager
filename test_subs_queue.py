"""subs_queue.paced: one file at a time, waits for that file's [subs] line, gives up on a file after two tries."""
import json, os, sys, tempfile, types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.modules["app"] = types.SimpleNamespace(_hook_credentials=lambda: ("u", "p"))   # paced() imports it lazily
import subs_queue as sq  # noqa: E402


def setup(paths):
    d = tempfile.mkdtemp()
    sq.HERE = d
    sq.TRIED = os.path.join(d, "tried.json")
    open(os.path.join(d, "server.log"), "w").write("")
    sq.unfiltered = lambda: [(i, p) for i, p in enumerate(paths)]
    sq.time.sleep = lambda s: None
    return d


def test_one_at_a_time_and_waits_for_each_result():
    paths = ["/m/A (2000)/A (2000).en.srt", "/m/B (2001)/B (2001).en.srt"]
    d = setup(paths)
    order = []

    def post(path, auth):
        order.append(("post", os.path.basename(path)))
        with open(os.path.join(d, "server.log"), "a") as f:           # the service answers with its [subs] line
            f.write(f"[subs] {os.path.basename(path)}: ok ads=0 none final=+0.00s/1.0000 written=False\n")
        return 202
    sq.post = post
    sq.paced(lambda: True)
    assert order == [("post", "A (2000).en.srt"), ("post", "B (2001).en.srt")], order
    print("test_one_at_a_time_and_waits_for_each_result OK")


def test_stops_when_the_mount_goes():
    d = setup(["/m/A (2000)/A (2000).en.srt", "/m/B (2001)/B (2001).en.srt"])
    posted, calls = [], iter([True, False])
    sq.post = lambda path, auth: (posted.append(path), open(os.path.join(d, "server.log"), "a").write(
        f"[subs] {os.path.basename(path)}: ok\n"))[0] or 202
    sq.paced(lambda: next(calls))
    assert len(posted) == 1, posted
    print("test_stops_when_the_mount_goes OK")


def test_a_file_tried_twice_is_skipped():
    d = setup(["/m/A (2000)/A (2000).en.srt"])
    json.dump({"/m/A (2000)/A (2000).en.srt": 2}, open(sq.TRIED, "w"))
    posted = []
    sq.post = lambda path, auth: posted.append(path) or 202
    sq.paced(lambda: True)
    assert posted == []
    print("test_a_file_tried_twice_is_skipped OK")


if __name__ == "__main__":
    test_one_at_a_time_and_waits_for_each_result(); test_stops_when_the_mount_goes(); test_a_file_tried_twice_is_skipped()
