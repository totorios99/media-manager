"""library_maint.run: a failing step is reported once at the end, the next step still runs."""
import json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import library_maint as lm  # noqa: E402


def test_failures_are_collected_and_the_run_goes_on():
    d = tempfile.mkdtemp()
    for name, status in (("a.py", "FAILED"), ("b.py", "ok")):
        open(os.path.join(d, name), "w").write(
            f"import json\nopen({os.path.join(d, name + '.jsonl')!r}, 'a').write(json.dumps("
            f"{{'path': '/x/{name}.mkv', 'status': {status!r}, 'error': 'boom'}}) + '\\n')\n")
    sent = []
    real = (lm.HERE, lm.STEPS, lm.OUT, lm.overnight.alert, lm.jellyfin, lm.os.path.ismount)
    lm.HERE, lm.OUT = d, os.path.join(d, "out.log")
    lm.STEPS = [("a", [os.path.join(d, "a.py")], "a.py.jsonl"), ("b", [os.path.join(d, "b.py")], "b.py.jsonl")]
    lm.overnight.alert = lambda st, key, title, body, prio=4: sent.append(body)
    lm.jellyfin = lambda p, method="POST": None
    lm.os.path.ismount = lambda p: True
    try:
        rc = lm.run()
    finally:
        lm.HERE, lm.STEPS, lm.OUT, lm.overnight.alert, lm.jellyfin, lm.os.path.ismount = real
    assert rc == 1 and len(sent) == 1 and "a.py.mkv" in sent[0] and "boom" in sent[0], sent
    assert os.path.exists(os.path.join(d, "b.py.jsonl")), "the step after the failure still ran"
    print("test_failures_are_collected_and_the_run_goes_on OK")


if __name__ == "__main__":
    test_failures_are_collected_and_the_run_goes_on()
