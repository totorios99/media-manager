# Pipeline gaps

Cases the automation (library_maint: F6 series/movies, ocr_pass, subs_check) did not handle. For each: the cause, the
hand fix, and the change folded into the pipeline, or why it stays manual. Rule from Antonio, 2026-10-11.

| Date | Case | Cause (evidence) | Hand fix | Pipeline | Status |
|---|---|---|---|---|---|
| 10-11 | Avengers Endgame, Matrix Resurrections: F6 "stream hashes differ" | mkvmerge writes CRLF inside SRT cues; video/audio identical on a 120 s clip remuxed with the real plan | none needed | `stream_md5` normalized pass drops `\r` (2a5c158) | in pipeline; next F6 movies pass |
| 10-11 | Death Note, 7 eps: F6 refusal | mkvmerge lengthens zero-duration karaoke lines | none needed | normalized pass compares text + starts, not end times (1fd4702) | in pipeline |
| 10-11 | DBZ 46 / R&M 8: two bare-`spa` text subs, F6 blocked | no variant evidence in tags; text shows castellano fansub + Latino dubtitles (+ songs, Latino ASS fansub) | `dbz_rm_subs.py` (markers, dub rule, verified remux) | `spa_unblock.py` runs first in library_maint: castellano by text verdict → es-ES, latino → es-419, then F6 plans the file; still ambiguous → spa_unblock_review.jsonl. The DBZ-specific dub rule (no opening lyrics) stays in dbz_rm_subs | in pipeline (clear cases) |
| 10-11 | R&M S07: forced track named "FORZADO" without the forced flag | release mislabel | flag set by `dbz_rm_subs.py` | `spa_unblock.py`: a sub named forzado/forced with < 300 words gets the flag | in pipeline |
| 10-11 | VobSub (23 tracks, 15 files) | no VobSub OCR tool installed | — | ocr_pass drops them like a failed gate, .idx/.sub to .recycle, Bazarr fills (768b962) | in pipeline |
| 10-11 | Dropped subtitles must be replaced | Bazarr only searches series with a profile | homelab searches by hand | `bazarr_watch.py` lists what is still missing; the 6 h check sends it to homelab (2bbf9b4). **Gap**: no direct Bazarr API access from media-manager | partly |
| 10-11 | DBZ S01E14, S04E01/02/06/08/09 (+4 ties): no Latino sub anywhere | DBZ has no Bazarr profile; providers return 0 results | — | Antonio's call (profile for DBZ) | waiting 10-19 |
| 10-11 | Pirates of the Caribbean (2003): F6 "duration of track 1 differs" | packets identical; audio end 0.36 s later after remux; checking DTS timestamp gaps | pending | pending | investigating |
| 10-11 | ASS subtitles not checked for ads/sync | subs_clean parses SRT only | — | **gap** | open |
