"""chapters_clean: names the skip-button provider can match, nothing else touched."""
import os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chapters_clean as cc  # noqa: E402


def atom(start, end, name):
    return (f"<ChapterAtom><ChapterUID>1</ChapterUID><ChapterTimeStart>{start}</ChapterTimeStart>"
            f"<ChapterTimeEnd>{end}</ChapterTimeEnd><ChapterDisplay><ChapterString>{name}</ChapterString>"
            f"</ChapterDisplay></ChapterAtom>\n")


def names(x):
    return re.findall(r"<ChapterString>(.*?)</ChapterString>", x)


def test_normal_episode():
    xml = "<Chapters><EditionEntry>\n" + atom("00:00:00.000", "00:00:00.000", "Intro") + \
        atom("00:00:00.000", "00:01:25.085", 'OP 02 - "Genkai Toppa" por Kiyoshi Hikawa') + \
        atom("00:01:25.085", "00:12:19.000", "Parte A") + \
        atom("00:21:20.000", "00:22:20.000", 'ED 10 - "70cm" por ROTTENGRAFFTY') + \
        atom("00:22:20.000", "00:22:50.000", "Adelanto") + "</EditionEntry></Chapters>"
    new, ch = cc.clean(xml)
    assert names(new) == ["OP", "Parte A", "ED", "Preview"], names(new)
    assert len(ch) == 4
    print("test_normal_episode OK")


def test_one_frame_intro_is_dropped():
    xml = "<Chapters>" + atom("00:00:00.000", "00:00:00.033", "Intro") + \
        atom("00:00:00.033", "00:01:25.000", "OP 02 - x por y") + "</Chapters>"
    assert names(cc.clean(xml)[0]) == ["OP"]
    print("test_one_frame_intro_is_dropped OK")


def test_mid_episode_op_is_not_an_intro():
    xml = "<Chapters>" + atom("00:18:30.000", "00:20:02.000", 'OP 02 - "Genkai" por X') + "</Chapters>"
    assert names(cc.clean(xml)[0]) == ["Tema"]
    print("test_mid_episode_op_is_not_an_intro OK")


def test_nothing_to_do():
    xml = "<Chapters>" + atom("00:00:00.000", "00:10:00.000", "Parte A") + "</Chapters>"
    assert cc.clean(xml) == (xml, [])
    print("test_nothing_to_do OK")


if __name__ == "__main__":
    test_normal_episode(); test_one_frame_intro_is_dropped(); test_mid_episode_op_is_not_an_intro(); test_nothing_to_do()
