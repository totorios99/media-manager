"""Keep Antonio's Jellyfin playlists in a fixed order as requested films land.

    playlists.py            # sync every playlist in PLAYLISTS
    playlists.py --dry      # print what each would hold, change nothing

Each playlist is a list of Movies/ folder names. A film not yet in Jellyfin is skipped and
picked up on a later run. The playlist is rewritten only when its contents or order differ,
by removing every entry and re-adding in order: Jellyfin 13 answers 400 to /Move/{index}.
"""
import json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "http://localhost:8097"
USER = "78afafb5f8fb40c687197ac606bfd9b8"          # antonio
PLAYLISTS = {
    "f4bb283f56a813f64addd15b05baab01": [            # Iceberg mind-bending (6-tier iceberg order)
        "Fight Club (1999)", "The Matrix (1999)", "Interstellar (2014)", "Inception (2010)", "Shutter Island (2010)",
        "The Truman Show (1998)", "Eternal Sunshine of the Spotless Mind (2004)",
        "Tenet (2020)", "Requiem for a Dream (2000)", "2001 A Space Odyssey (1968)", "Ex Machina (2015)",
        "Donnie Darko (2001)", "Oldboy (2003)", "The Prestige (2006)", "Mystic River (2003)",
        "Black Swan (2010)", "Arrival (2016)", "Eyes Wide Shut (1999)", "Looper (2012)", "Mulholland Drive (2001)",
        "Coherence (2014)", "Vanilla Sky (2001)", "Predestination (2014)",
        "Persona (1966)", "Eraserhead (1977)", "Inland Empire (2006)", "I'm Thinking of Ending Things (2020)",
        "The Usual Suspects (1995)", "Incendies (2010)", "Possession (1981)", "Titane (2021)",
        "A Tale of Two Sisters (2003)", "A Clockwork Orange (1971)", "The Seventh Seal (1957)", "Psycho (1960)",
        "The Game (1997)", "Twin Peaks Fire Walk with Me (1992)",
        "Lost Highway (1997)", "Primer (2004)", "Videodrome (1983)", "Cure (1997)", "Barton Fink (1991)",
        "Paprika (2006)", "Perfect Blue (1998)"],
    "9272e9d14397437d95af8cd7700533f5": [            # Anime que conecta (light to heavy)
        "Howl's Moving Castle (2004)", "Princess Mononoke (1997)", "The Boy and the Beast (2015)", "Your Name. (2016)",
        "The Tunnel to Summer, the Exit of Goodbyes (2022)", "The Garden of Words (2013)",
        "5 Centimeters per Second (2007)", "A Silent Voice The Movie (2016)", "The Wind Rises (2013)",
        "Grave of the Fireflies (1988)"],
}


def call(method, path):
    tok = open(os.path.join(HERE, ".jellyfin-token")).read().strip()
    req = urllib.request.Request(URL + path, method=method, headers={"Authorization": f'MediaBrowser Token="{tok}"'})
    with urllib.request.urlopen(req) as r:
        body = r.read()
    return json.loads(body) if body else None


def norm(s):
    # Radarr folder names drop ":" and may keep or drop a trailing "." ("Your Name. (2016)")
    return "".join(c for c in s.lower() if c.isalnum())


def sync(pid, order, dry):
    # without CollapseBoxSetItems=false a film in a TMDB collection comes back as its box set (The Matrix)
    movies = call("GET", f"/Users/{USER}/Items?IncludeItemTypes=Movie&Recursive=true&CollapseBoxSetItems=false"
                         "&Fields=Path")["Items"]
    by = {norm(m["Path"].split("/")[-2]): m["Id"] for m in movies if m.get("Path")}
    want = [by[norm(f)] for f in order if norm(f) in by]
    missing = [f for f in order if norm(f) not in by]
    have = call("GET", f"/Playlists/{pid}/Items?UserId={USER}")["Items"]
    if [i["Id"] for i in have] == want:
        print(pid, "in order,", len(want), "items; waiting for", missing or "nothing")
        return
    print(pid, f"{len(have)} -> {len(want)} items; waiting for", missing or "nothing")
    if dry:
        return
    if have:
        call("DELETE", f"/Playlists/{pid}/Items?entryIds={','.join(i['PlaylistItemId'] for i in have)}")
    call("POST", f"/Playlists/{pid}/Items?ids={','.join(want)}&userId={USER}")
    got = [i["Id"] for i in call("GET", f"/Playlists/{pid}/Items?UserId={USER}")["Items"]]
    assert got == want, f"{pid}: order after rewrite differs"


if __name__ == "__main__":
    assert norm("Your Name. (2016)") == norm("Your Name (2016)")
    for pid, order in PLAYLISTS.items():
        sync(pid, order, "--dry" in sys.argv)
