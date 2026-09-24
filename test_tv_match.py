"""A show must match the series the folder names, not TMDB's most popular hit.

"The Seven Deadly Sins (2014) [tvdbid-284131]" matched the 2023 sequel "Four
Knights of the Apocalypse" because the title search took results[0], and every
episode was renamed after the wrong series."""
import io, json

import scan

SEQUEL = {"id": 218843, "name": "The Seven Deadly Sins: Four Knights of the Apocalypse",
          "first_air_date": "2023-10-08", "original_language": "ja", "genre_ids": [16]}
ORIGINAL = {"id": 62104, "name": "The Seven Deadly Sins",
            "first_air_date": "2014-10-05", "original_language": "ja", "genre_ids": [16]}


def fake_urlopen(url, timeout=10):
    if "/find/284131" in url:
        return io.BytesIO(json.dumps({"tv_results": [ORIGINAL]}).encode())
    if "/search/tv" in url:
        return io.BytesIO(json.dumps({"results": [SEQUEL, ORIGINAL]}).encode())
    raise AssertionError(url)


def main():
    scan.urllib.request.urlopen = fake_urlopen

    # the folder's tvdbid is exact and wins over the title search
    got = scan.tmdb_search_tv("The Seven Deadly Sins", "k", 2014, "284131")
    assert got["tmdb_id"] == 62104, got

    # no tvdbid: the folder year picks the right one out of the popularity order
    got = scan.tmdb_search_tv("The Seven Deadly Sins", "k", 2014)
    assert got["tmdb_id"] == 62104, got

    # no year to go on: fall back to the first result, as before
    got = scan.tmdb_search_tv("The Seven Deadly Sins", "k")
    assert got["tmdb_id"] == 218843, got

    print("test_tv_match OK")


if __name__ == "__main__":
    main()
