# Estado del trabajo de biblioteca — 2026-09-02

Documento de continuidad. Solo biblioteca; el hilo de hardware vive en
`~/homelab-design/hardware-architecture.md` y en el vault de homelab.

## Dónde va la cola

| | Películas |
|---|---|
| clean | **288** (2.46 TB) |
| ready (pendientes) | **23** (1.14 TB, casi todo 4K) |
| error permanente | 1 |

Series: 110 episodios clean, 870 sin procesar (aún no se han tocado).

Batch corriendo en segundo plano:
`scratchpad/remux_batch.py` → log en `scratchpad/remux3.log`.
Cero fallos desde el relanzamiento. Va en 11/35 de esta tanda.

**Único error permanente:** `Las Ninas Bien (2018)` — descarga truncada,
`moov atom not found`, ilegible por mkvmerge y ffprobe. Solo re-descarga.

## Cómo funciona el batch

Serial por diseño (el runner del app es de slot único global). Por título:
remux → verificar → borrar original → renombrar carpeta.

Protecciones añadidas tras las caídas:
- `storage_alive()` antes de cada título y tras cada fallo. Aborta si
  `/media/hdd1` desaparece o queda read-only, en vez de quemar la cola entera.
  (Una caída marcó 54 títulos como fallidos en segundos.)
- Reintentos con backoff, 5 intentos. Un HTTP 500 por contención de disco ya
  no descarta el título (perdió 36 en una corrida).
- 20 s de pausa entre títulos para que el disco vacíe caché.

## Convención de salida

Películas (14 de 315 carpetas aún fuera de convención, bajando):
```
/media/hdd1/Movies/<Título> (<Año>)/<Título> (<Año>).mkv
+ backdrop/landscape/logo/poster
```

Series (ya estable, no tocar):
```
/media/hdd1/Shows/<Serie> (<Año>)/Season NN/<Serie> (<Año>) - SNNENN - <Título>.mkv
```

Pistas: idioma original primero y por defecto, luego español, y
TrueHD/Atmos al final sin ser nunca default.

## Cambios al app que quedaron en esta sesión

Todos en `~/media-manager`, servidor en `run.sh` puerto 8500 (NO docker).

- **`kind="propedit"`** — edita metadata Matroska en sitio con mkvpropedit.
  Sin copia, sin requisito de espacio libre. 27 títulos pasaron por ahí.
  Rechaza no-mkv y cualquier cosa que descarte o agregue pistas.
- **Calidad en tres niveles** (`bloated`/`ideal`/`lean`) contra techos y pisos
  de bitrate por resolución, con piso ajustado por códec para HEVC/AV1/VP9.
  Reemplazó el binario encode/keep.
- **Atmos, SDH y comentarios** detectados y persistidos; el remux escribe los
  cinco flags de subtítulo explícitamente y deriva nombres canónicos.
- **`get_db` timeout 30 s → 300 s.** Los "database is locked" eran hambre de
  disco, no bug del app.
- Dos bugs corregidos en `build_mkvpropedit_chain`: nunca ponía `flag-default`
  en video ni limpiaba `flag-forced` en audio.

Tests: `test_quality.py`, `test_sub_flags.py` nuevos. Los 8 pasan.

## Pendientes de la biblioteca

- Terminar los 23 remuxes restantes.
- **870 episodios sin procesar** — el grueso del trabajo que queda.
- DBZ: episodios 251/253 faltan; el 199 no tiene audio latino.
- `Seen The Sequel Mrs Doubtfire (1993)` — remuxeada bien pero sin match TMDB,
  el rename falla con 400. Necesita match manual.
- Avengers: Age of Ultron — el usuario lo difirió ("not ready yet").
- Avisar a la sesión `homelab` cuando el layout de Movies deje de moverse;
  está esperando para documentar el stack de medios.

## Estado del entorno

- Jellyfin **detenido** a petición del usuario mientras drena la cola.
  Revivir con `docker start jellyfin`. Al volver: `docker update --cpus=2
  jellyfin` y trickplay capado/non-blocking, mejor apagado hasta terminar.
- `/media/hdd1` está en **`sdb2`** (era sdc2 antes de la re-enumeración USB).
- transmission-daemon escribe al mismo disco; es contención real.
