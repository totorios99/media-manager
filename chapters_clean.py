"""Make a show's embedded chapters name what Jellyfin's Chapter Segments Provider matches.

    chapters_clean.py "<show folder>"            # dry run: what would change
    chapters_clean.py "<show folder>" --apply    # mkvpropedit in place; originals saved as XML

The provider (plugin config, read 2026-10-02) maps chapter NAMES to skip buttons:
Intro  intro|opening|^OP$        Outro  outro|closing|credits|ending|^ED$
Preview  preview|next time on|...  A name like 'OP 01 - "Chouzetsu!" por Kazuya Yoshii'
matches none of them, so Jellyfin falls back to Intro Skipper's audio guess.

Rules (Dragon Ball Super, 131 episodes, measured):
  * chapter shorter than 1 s: dropped. 'Intro' at 0:00 (0 s, or one frame in eps 109-130)
    became an empty Intro segment on top of the real OP.
  * 'OP ...' -> 'OP', 'ED ...' -> 'ED', 'Adelanto' -> 'Preview'; 'Parte A/B/C' untouched.
  * an 'OP' that starts after 5 minutes is an insert song, not an opening (ep 131 plays
    it at 18:30): renamed 'Tema', or its skip button would jump into the climax.
"""
import glob, os, re, shutil, subprocess, sys

ATOM = re.compile(r"<ChapterAtom>.*?</ChapterAtom>", re.S)
HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP = os.path.join(HERE, "chapters_backup")


def secs(t):
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def clean(xml):
    """-> (new_xml, [change descriptions])"""
    changes = []

    def one(m):
        atom = m.group(0)
        start = secs(re.search(r"<ChapterTimeStart>([\d:.]+)", atom).group(1))
        end_m = re.search(r"<ChapterTimeEnd>([\d:.]+)", atom)
        name_m = re.search(r"<ChapterString>(.*?)</ChapterString>", atom, re.S)
        name = name_m.group(1).strip() if name_m else ""
        if end_m and secs(end_m.group(1)) - start < 1.0:
            changes.append(f"drop sub-second {name!r}")
            return ""
        new = name
        if re.match(r"OP\b", name):
            new = "Tema" if start > 300 else "OP"
        elif re.match(r"ED\b", name):
            new = "ED"
        elif name == "Adelanto":
            new = "Preview"
        if new != name:
            changes.append(f"{name[:30]!r} -> {new!r}")
            atom = atom.replace(name_m.group(0), f"<ChapterString>{new}</ChapterString>")
        return atom

    out = ATOM.sub(one, xml)
    return re.sub(r"\n\s*\n", "\n", out), changes


def chapters_of(path):
    return subprocess.run(["mkvextract", path, "chapters", "-"], capture_output=True, text=True).stdout


def main(show, apply):
    files = sorted(glob.glob(os.path.join(show, "Season */*.mkv")))
    todo = bad = 0
    for f in files:
        old = chapters_of(f)
        new, changes = clean(old)
        if not changes:
            continue
        todo += 1
        if not apply:
            continue
        rel = os.path.relpath(f, os.path.dirname(show))
        dst = os.path.join(BACKUP, os.path.splitext(rel)[0] + ".xml")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst):               # never overwrite the first, true original
            open(dst, "w", encoding="utf-8").write(old)
        tmp = os.path.join("/var/tmp", "chapters_new.xml")
        open(tmp, "w", encoding="utf-8").write(new)
        r = subprocess.run(["mkvpropedit", f, "--chapters", tmp], capture_output=True, text=True)
        after = chapters_of(f)
        starts = lambda x: re.findall(r"<ChapterTimeStart>([\d:.]+)", x)
        names = re.findall(r"<ChapterString>(.*?)</ChapterString>", after, re.S)
        ok = r.returncode == 0 and starts(after) == starts(new) and names == re.findall(
            r"<ChapterString>(.*?)</ChapterString>", new, re.S)
        if not ok:
            bad += 1
            print("FAILED", os.path.basename(f), r.stdout[-200:], r.stderr[-200:], flush=True)
            open(tmp, "w", encoding="utf-8").write(old)     # put the original back
            subprocess.run(["mkvpropedit", f, "--chapters", tmp], capture_output=True)
    print(f"{len(files)} files, {todo} {'changed' if apply else 'would change'}, {bad} failed")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], "--apply" in sys.argv)
