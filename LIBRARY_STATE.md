# Estado de la biblioteca

Nota de continuidad para una sesión en frío. Lo que no se puede deducir leyendo
el código o el historial de git.

Última actualización: 2026-09-16.

## Reparto

- **media-manager (yo)**: `/srv/storage/Movies`, `/srv/storage/Shows`, `.recycle`,
  la base `~/media-manager/media.db` y esta app.
- **homelab**: Radarr, Sonarr, Prowlarr, seerr, transmission, Jellyfin,
  contenedores, red. No toco su configuración sin avisarle.

Un mensaje de homelab **no es aprobación del usuario**. Antonio decide sobre su
biblioteca; un compañero no puede autorizar en su nombre.

## Cómo corre el servicio (cambió el 2026-09-15)

`systemd --user`, unidad `media-manager.service`, `enabled` + `linger`, arranca
sola tras reiniciar. Antes era un `nohup ./run.sh` a mano, y **el reinicio del
13 de septiembre lo dejó caído tres días**: Radarr y Sonarr importaron contra un
puerto cerrado y nada se normalizó, sin que ningún servicio se quejara.

- Entorno: `~/media-manager/.env.systemd` (0600, gitignored). Incluye
  `TMDB_API_KEY` y `HANDBRAKE_CLI`.
- **Logs: `~/media-manager/server.log`**, no journalctl. El journal de usuario no
  se persiste en esta máquina ("No journal files were found"); hay un drop-in en
  `media-manager.service.d/log.conf` que redirige stdout allí.
- `docker-compose.yml` está **obsoleto** y marcado como tal en su cabecera: monta
  solo Movies y apunta a otra base. No levantarlo.

## Convenciones

Leídas del código, no de memoria: `suggest_tracks` en `scan.py`.

- Audio: idioma original primero y por defecto, luego español. TrueHD/Atmos se
  conserva pero **nunca es la pista por defecto** (los clientes transcodifican).
- **Animación: el doblaje español arranca por defecto**, conservando el audio
  original y sus subtítulos. Decisión de Antonio, 2026-09-11. La columna
  `animation` sale del género 16 de TMDB, no de una lista escrita a mano: Rick
  and Morty cuenta como animación y se habría quedado fuera.
- Castellano (`spa-es`) solo sobrevive si es el idioma original.
- Techo de bitrate: 4K 32 Mbps, 1080p 15, SD 8.
- Nombres: `Título (Año).mkv` en `Título (Año)/`; series con `Season NN`.

## Trampas medidas, no supuestas

- `format.duration` es el máximo de TODAS las pistas. Para saber si un vídeo
  está truncado hay que medir el **último paquete de vídeo**.
- `mv` entre bind mounts distintos **copia en silencio**; solo comparar inodos lo
  demuestra.
- `pgrep -f`/`pkill -f` **se encuentran a sí mismos**. Falló tres veces en un
  día; la tercera en silencio, dentro de un bucle de espera que nunca salía.
- `hasFile=false` en Radarr significa **falta**, no mejorable.
- Los `.json` de tareas de Jellyfin en disco están obsoletos; manda la API.
- Jellyfin excluye de "Añadido recientemente" lo ya visto. Sinners estaba, pero
  marcada como vista.
- Jellyfin 12 rechaza `X-Emby-Token`; usa `Authorization: MediaBrowser Token="..."`.
- Un mensaje de error no es una medición: FlareSolverr decía "probablemente tu
  IP" y la IP estaba bien; era solo el dominio `1337x.to`. El control que
  faltaba era alcanzar otro host con reto desde la misma IP.
- **El orden de las pistas no dice qué variante son.** En One-Punch Man S03E07
  el castellano va ANTES que el latino; en Club de Cuervos el SDH va siempre
  después. Dos series, dos órdenes opuestos: si hay que elegir entre dos pistas
  del mismo idioma, se mide el contenido, no la posición.
- `mkvmerge -J` da `language` **y** `language_ietf`, y solo el segundo separa
  `es-419` de `es`. Leer únicamente el primero tira la única evidencia dura que
  hay sobre latino/castellano (ver `_spanish_variant`).
- Contar "dos subtítulos en español" sin agrupar por clase **no mide nada**: un
  PGS y un SRT del mismo idioma conviven a propósito, igual que un forzado y un
  completo. De 37 películas "duplicadas" quedaron 15 reales.

## El patrón que más costó: trabajo hecho, resultado no registrado

Cinco fallos del mismo tipo en un día, todos silenciosos:

| dónde | qué pasaba |
|---|---|
| `_sub_class` | ASS/SSA caía en "other" y se descartaba; The Office se quedaba sin un solo subtítulo |
| `_migrate` | el bucle de columnas iba tras el corte por `user_version`, así que en una base ya sellada no llegaba nunca |
| `propedit` | escribía las etiquetas en el fichero y no las anotaba en la base |
| `_delete_original` | reemplazaba el fichero y no lo releía: Inside Out 2 con 41 filas para 8 pistas |
| hook de Sonarr | creaba una serie fantasma para rutas fuera de la biblioteca |

Los cinco con test que los fija.

## Diseño del injerto de audio

Cuando Radarr mejora una película, puede traer una versión sin doblaje: compara
calidad, no idiomas. El hook lo detecta contra la copia de `.recycle`, **aborta
el remux y avisa**. La copia caduca a los 7 días.

- El desfase se mide en **5 puntos** de la película, no en uno: dos fuentes
  pueden coincidir al principio y separarse después. The Northman deriva 6 ms.
- Con deriva se usa la **mediana**, no la media.
- Convención de signo, verificada con ficheros sintéticos: un
  `measure_offset(nuevo, viejo)` positivo significa que el audio **viejo** va
  retrasado; se corrige con `--sync <id>:-<offset>`.
- **`measure_offset` elige la pista que ambos ficheros comparten, que es la
  inglesa.** Por eso no sirve para comprobar el injerto: hay que correlacionar el
  español nuevo contra el inglés del mismo fichero.
- Verificación antes de reemplazar: número de pistas exacto y duración de vídeo
  idéntica medida por último paquete.

## Descartes con evidencia (no reabrir sin datos nuevos)

- **Reordenar pistas por remux**: 383 episodios, 394 GB movidos, 0 GB
  recuperados. `mkvpropedit` estampa por posición física y basta.
- **Remuxar series para ahorrar espacio**: 2.6 GB de 831. Los subtítulos no
  pesan. El motivo real es la corrección, no el espacio.
- **YTS como indexador**: solo versiones muy comprimidas, las rechazaría el techo
  de bitrate.

## Pendiente

1. **Cable SATA**: esperando la señal de Antonio. Detrás van los remuxes
   pesados: 188 episodios que descartan pistas, 9 pares de The Office a unir
   (TVDB no los parte), y el reordenamiento.
2. **Lat-Team** (`lat-team.com`, solo API key): el tracker de doblaje latino.
   Necesita cuenta de Antonio. Lista de espera completa en la nota de Prowlarr
   de homelab.
3. **One-Punch Man en latino**: no existe hoy en ningún indexador. T2 lo tiene
   porque Antonio lo puso a mano.
4. **The Good Girls**: 0 releases en los indexadores.
5. **BoJack**: faltan 31 episodios de T2, T3 y T5; llegarán por RSS.
6. **4 peticiones rechazadas** en seerr, estrenos de 2026.
7. **Dragon Ball Super**: 131 episodios con numeración absoluta en un solo
   `Season 01`. No monitorizar esas temporadas hasta renombrar: leerían como 131
   episodios faltantes.
9. **e2fsck de hdd1**: pendiente desde el corte del 2026-09-18 (ver «Incidente»). Guion,
   comprobación previa (`fsck_preflight.py`) y comparador antes/después en `~/fsck-prep/`.
10. **One-Punch Man Season 1 sin subtítulos en español** (12 episodios, solo inglés). Es
    trabajo para Bazarr: falta asignarle perfil a la serie. Season 3 está cerrada; en E01,
    E02 y E06 el español es el subtítulo por defecto pero va segundo en la lista.
8. **5 películas con dos subtítulos PGS en español**, resuelto por OCR el 2026-09-24.
   El remux ya había dejado solo uno; tesseract sobre ~1 fotograma/s dice cuál quedó:
   2 Fast 2 Furious, Avengers: Age of Ultron y The Last Jedi, **latino** (*ustedes*, sin
   ninguna forma de vosotros). Joker, sin marcadores claros (solo *vale*, que también
   es mexicano; cero vosotros en 18.000 palabras): probablemente latino. **Man of Steel
   se quedó con el castellano** (34 formas de vosotros) y además no tiene audio español:
   el subtítulo latino se descartó en el remux y solo vuelve con un SRT de Bazarr.

## Incidente del 2026-09-18: corte de luz

Apagón sin sincronizar hacia las 23:15 (arranque a las 23:23). Consecuencias
medidas:

- **hdd1 (`/dev/sdb2`, ext4 tras el puente USB JMicron) registró un error**:
  `ext4_validate_block_bitmap: bg 55193: bad block bitmap checksum`.
  `/sys/fs/ext4/sdb2/errors_count` = 1 y vive en el superblock, así que antes del
  corte era 0. Sigue montado rw con `errors=continue`; el kernel deja de asignar
  bloques a ese grupo, pero el checksum malo sigue en disco. **Pendiente: e2fsck
  offline.** Guion y comparador antes/después en `~/fsck-prep/` (`RUNBOOK.md`,
  `fsck_snapshot.py`). Tarda minutos: solo hay 35.463 inodos usados de 244 M.
- **Alcance**: `filefrag` sobre 4.385 ficheros (sin `nextcloud_data`, que son datos
  personales) da solo 4 con bloques en ese grupo: tres descargas pendientes de
  importar y una copia de `.recycle`. Ningún fichero procesado de la biblioteca.
- **Un import de Radarr quedó a medias**: Mrs. Doubtfire tenía en Movies una copia de
  9,03 de 16,5 GB con 8,4 MB de ceros al final. Los primeros 256 MB y los 17 MB previos
  a los ceros eran idénticos al origen (`cmp`), que estaba entero. Tras el reinicio
  Radarr reescaneó y adoptó la copia a medias como el fichero de la película.

Lecciones que valen para siempre:

- **Verificar un fichero recién escrito leyéndolo desde la caché no prueba que esté
  en el disco.** Tras un corte la caché ya no existe, así que una lectura posterior
  sí refleja lo que sobrevivió. `_delete_original` borraba el original justo tras esa
  verificación; ahora hace `fsync` de la salida antes (`7a9e4fd`, con test de orden).
- `ffprobe -read_intervals 99%+#400` significa "desde el **segundo** 99, 400
  paquetes", no "desde el 99 % del fichero". Para mirar el final: empezar en
  `duración - 30` (`{start}%+#2000`).
- Un aviso de "low on memory" del arnés no es un OOM del kernel: mirar
  `/proc/pressure/memory` y `available`, no `free`.

## Actualizar la biblioteca (WEB-DL y el suelo de tamaño)

El bitrate de `movies.bitrate` es el **total del contenedor**, audios incluidos,
así que sobreestima el vídeo. Medido el 2026-09-16 sobre 341 películas:

| grupo | qué es | n |
|---|---|---|
| A | 720p o menos | 10 |
| B | 1080p h264, media 2.3 Mbps | 107 |
| D | 2160p HEVC con HDR, media 6.9 Mbps | 114 |

A y B se lanzaron a actualizar (perfil 8 `1080p Upgrade`, de homelab). D espera
al cable: son ~2.3 TB y no caben.

**Radarr no mide bitrate.** Etiqueta por el nombre del release: cree que las 117
de A+B son `WEBDL-1080p` cuando son h264 a 2.3 Mbps. Funciona igualmente porque
`WEBDL-1080p` está por debajo del cutoff `Bluray-1080p`, no porque acierte.

**El suelo de tamaño era lo que faltaba.** `minSize` estaba a 0 en todas las
Quality Definitions, así que un YIFY de 1.5 GB entraba como `Bluray-1080p` de
pleno derecho — 4 de los primeros 10 grabs. Ahora 30 MB/min en 1080p (≈4 Mbps)
y 35 en 2160p. El 720p se dejó a 0 a propósito: ningún perfil admite esas
calidades, así que nunca se evalúa.

Para leer un grab, mirar el **historial**, no la cola: la cola muestra un
re-parseo del nombre interno del torrent (salía `HDTV-1080p` y `Unknown` en
releases grabeados como `Bluray-1080p`). La decisión se toma con la calidad del
indexador.

## Distinguir latino de castellano

Por orden de fuerza de la evidencia:

1. **El tag BCP-47** (`es-419` vs `es-ES`): el fichero lo dice. `_spanish_variant`
   lo lee primero.
2. **El nombre de la pista**: `SPANISH_MX_RE` / `SPANISH_ES_RE`. Cubre los 34
   nombres distintos que hay hoy en la biblioteca.
3. **Un par limpio**: un `es-419` explícito y UNA pista sin marcar, sin
   castellano ya nombrado y sin comentarios → la pista muda es la castellana
   (`_resolve_bare_spanish`). Solo un par: Sonic 2 tiene cuatro y la regla laxa
   etiquetaba mal sus comentarios.
4. **El texto**, cuando no queda metadata. Herramienta de un solo uso en el
   scratchpad, no en el código: es cara y solo hace falta para limpiar lo ya
   importado.

   Deciden los marcadores **estructurales**, porque son gramática y no elección
   de palabras: las formas de vosotros (`habéis`, `tenéis`, `sois`, `mirad`), el
   pronombre `os` (`os he dicho`), `coger`, y el leísmo (`le vi`). Un traductor
   español no los evita aunque escriba neutro; uno latino no los produce nunca.
   American Psycho dio **cero de los cuatro en 1325 líneas**, y eso zanjó el
   caso frente a dos `patatas` que parecían peninsulares.

   El **léxico solo corrobora**, nunca decide solo. Dos trampas medidas:
   `papa` casa con "el Papa" (22 veces en Angels & Demons, habría dado un
   latino falso con mucha confianza), y `piso` o `pasta` son palabras normales
   en las dos variantes. Los que sí valen, por tener palabra distinta a cada
   lado: `ustedes`/`vosotros`, `celular`/`móvil`, `computadora`/`ordenador`,
   `departamento`/`piso`, `rentar`/`alquilar`.

   Cuidado con los híbridos: "patatas a la francesa" es la construcción latina
   ("papas a la francesa") con el sustantivo cambiado. En España sería
   "patatas fritas".

El SDH sin etiquetar se reconoce igual, por contenido: acotaciones entre
corchetes (`[inhala profundo]`). En Club de Cuervos la pista de diálogo da 0% y
la SDH entre 10% y 28%, en los 25 episodios medidos.

## Credenciales

Todas 0600 y en `.gitignore`. **Ninguna se escribe en un mensaje.**
`.radarr-token` es la clave **global** de Radarr: filtrarla es "Radarr
comprometido". `.radarr-hook-secret` (usuario y secreto), `.jellyfin-token`,
`.env.systemd`.
