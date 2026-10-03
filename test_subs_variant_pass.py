"""subs_variant_pass.verdict: structural markers decide, vocabulary only corroborates, little text is unknown."""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp())
os.environ.setdefault("TMDB_API_KEY", "x")
import subs_variant_pass as sv  # noqa: E402

FILLER = "Ellos caminaron por la calle mientras el sol se ponía detrás de los edificios. " * 300   # ~3900 words


def test_vosotros_forms_make_it_castellano():
    t = FILLER + " Habéis llegado tarde. Sois unos genios. Mirad esto. Os he dicho que no."
    r = sv.verdict(t)
    assert r["verdict"] == "castellano" and r["struct"] >= 3, r


def test_no_structural_marker_in_enough_text_is_latino():
    r = sv.verdict(FILLER + " Ustedes llegaron temprano. Mi celular se perdió.")
    assert r["verdict"] == "latino" and r["lat_lex"] >= 2 and r["struct"] == 0, r


def test_short_text_is_unknown_even_without_markers():
    assert sv.verdict("Hola. ¿Cómo estás? Ustedes están bien.")["verdict"] == "unknown"


def test_one_or_two_markers_alone_do_not_decide():
    r = sv.verdict(FILLER + " Coge eso, por favor.")
    assert r["verdict"] == "unknown", r


def test_markers_plus_castellano_vocabulary_decide():
    r = sv.verdict(FILLER + " Coge el coche, tío. Vale, joder, qué guay.")
    assert r["verdict"] == "castellano", r


def test_vocabulary_alone_never_decides():                   # 'vale', 'coche', 'tío' exist in both variants
    r = sv.verdict(FILLER + " Vale, tío. Mi coche. Vale. Tío. Coche. Vale.")
    assert r["verdict"] == "latino" and r["struct"] == 0, r   # no structural marker: latino by the rule


def test_ass_dialogue_is_read():
    d = tempfile.mkdtemp(); p = os.path.join(d, "1.ass")
    open(p, "w").write("[Events]\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\i1}Habéis venido{\\i0}\\Nsois muchos\n")
    t = sv.plain_text(p)
    assert "Habéis venido" in t and "{" not in t, t


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(n, "OK")
