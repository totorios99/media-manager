"""Which existing audio track should carry the default flag. Never adds or drops a track.

Rule (Antonio, 2026-10-02):
  1. the language: the original, or the Latino dub for animation when there is one;
  2. TrueHD/Atmos is allowed as default only when it is the only track of that language;
  3. otherwise the best codec with the widest client support (no transcoding):
     E-AC-3 > AC-3 > AAC > Opus/MP3 > FLAC > DTS-HD > DTS, then more channels.
Commentary and descriptive tracks are never a candidate. A default that already ties for
best is left alone (two identical 'Español (FLAC)' tracks cannot be told apart by tags).

This is NOT scan.PREMIUM_AUDIO_ORDER: that list decides which track a remux KEEPS
(DTS-HD first, to protect quality) and a different order here must not change what is kept.
"""
import re

import scan

HEAVY = re.compile(r"truehd|atmos", re.I)
SIDE = re.compile(r"commentar|comentari|descript|audio desc|karaoke", re.I)
COMPAT = ["e-ac-3", "eac3", "ac-3", "ac3", "aac", "opus", "mp3", "flac", "dts-hd", "dts"]


def variant(t):
    p = t["properties"]
    return scan._spanish_variant(p.get("language"), p.get("track_name"), "audio", p.get("language_ietf"))


def heavy(t):
    return bool(HEAVY.search(f"{t['codec']} {t['properties'].get('track_name') or ''}"))


def side(t):
    p = t["properties"]
    return bool(p.get("flag_commentary") or p.get("flag_visual_impaired")
                or SIDE.search(p.get("track_name") or ""))


def compat_key(t):
    c = (t["codec"] or "").lower()
    rank = next((i for i, n in enumerate(COMPAT) if n in c), len(COMPAT))
    return (heavy(t), rank, -(t["properties"].get("audio_channels") or 0))


def candidates(audio, orig, animation):
    main = [t for t in audio if not side(t)]
    cands = [t for t in main if variant(t) in ("spa-mx", "spa")] if animation else []
    if not cands:
        want = scan.LANG_ISO1_TO_3.get(orig or "", "eng")
        cands = [t for t in main if variant(t) == want
                 or (orig == "es" and variant(t) in ("spa", "spa-mx", "spa-es"))]
    return cands


def pick(audio, orig, animation, current=None):
    """The track that should be default, or None when no candidate exists (leave the file alone)."""
    cands = candidates(audio, orig, animation)
    if not cands:
        return None
    best = min(cands, key=compat_key)
    if current is not None and current in cands and compat_key(current) == compat_key(best):
        return current
    return best
