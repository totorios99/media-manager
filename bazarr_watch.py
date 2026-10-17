"""Which subtitles we removed are still missing, so Bazarr can be asked for them (Antonio, 2026-10-11: "Supervisa los
episodios sin subtítulos para que bazarr los entregue correctamente").

    bazarr_watch.py            # prints the pending list; updates bazarr_watch.json (first_seen, delivered_at)

Sources: ocr_review.jsonl (a PGS/VobSub dropped after a failed OCR gate, or a VobSub with no OCR), dbz_rm_subs.jsonl
(a DBZ tie with no dub subtitle left) and thin_forced.jsonl (a language whose only track was signs, now flagged forced). A language counts as delivered when the file has a text track in it again
(embedded, castellano excluded for Spanish) or a sidecar that the subtitle filter accepted.
"""
import datetime, json, os, re

import f6_series as f6
import library_audit as la

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "bazarr_watch.json")
SIDE = {"spa": re.compile(r"\.(es|spa|es-MX|es-419)(\.(forced|sdh|hi))?\.srt$", re.I),
        "eng": re.compile(r"\.(en|eng)(\.(forced|sdh|hi))?\.srt$", re.I)}


def wanted():
    out = {}
    for name in ("ocr_review.jsonl", "dbz_rm_subs.jsonl", "thin_forced.jsonl"):
        p = os.path.join(HERE, name)
        for l in open(p, encoding="utf-8") if os.path.exists(p) else []:
            r = json.loads(l)
            if name == "thin_forced.jsonl":
                for lang in r.get("missing") or []:
                    if lang in SIDE:
                        out[(r["path"], lang)] = "only a signs/forced track (thin_forced)"
            elif name == "ocr_review.jsonl" and r.get("lang") in SIDE:
                out[(r["path"], r["lang"])] = r.get("why", "")
            elif r.get("tie") and r.get("status") == "ok" and "Bazarr" in (r.get("detail") or ""):
                out[(r["path"], "spa")] = "DBZ tie without a dub subtitle"
            elif r.get("status") == "ok" and r.get("acts") and not {"es-419", "keep"} & set(r["acts"].values()):
                out[(r["path"], "spa")] = "DBZ: only castellano/songs subtitles, all dropped"
    return out


def has_text(path, lang):
    tracks = la.probe(path)["tracks"]
    if any(t["type"] == "subtitles" and t["properties"].get("language") == lang and "PGS" not in t["codec"]
           and "VobSub" not in t["codec"] and not t["properties"].get("forced_track")
           and t["properties"].get("language_ietf") != "es-ES" for t in tracks):
        return True
    folder, stem = os.path.dirname(path), os.path.splitext(os.path.basename(path))[0]
    log_text = open(os.path.join(HERE, "server.log"), encoding="utf-8", errors="replace").read()
    return any(SIDE[lang].search(s["ext_path"]) for s in f6.filtered_sidecars(folder, stem, log_text))


def main():
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    now = datetime.datetime.now().isoformat(timespec="minutes")
    pending = []
    for (path, lang), why in sorted(wanted().items()):
        key = f"{path}|{lang}"
        s = state.setdefault(key, {"first_seen": now, "why": why, "asked": None, "delivered_at": None})
        if s["delivered_at"] or not os.path.exists(path):
            continue
        if has_text(path, lang):
            s["delivered_at"] = now
        else:
            pending.append((path, lang, s["asked"]))
    json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=1)
    for path, lang, asked in pending:
        print(lang, "| asked", asked or "never", "|", path)
    print(f"{len(pending)} pending, {sum(1 for s in state.values() if s['delivered_at'])} delivered")


if __name__ == "__main__":
    main()
