# Estado de la biblioteca

Nota de continuidad para una sesión en frío. Lo que no se puede deducir leyendo
el código o el historial de git.

Última actualización: 2026-09-16.

## Reparto

- **media-manager (yo)**: `/media/hdd1/Movies`, `/media/hdd1/Shows`, `.recycle`,
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
8. **5 películas con dos subtítulos PGS en español** y ninguna metadata que los
   separe: 2 Fast 2 Furious, Avengers: Age of Ultron, Joker: Folie à Deux, Man
   of Steel, Star Wars: The Last Jedi. `suggest_tracks` se quedó con la primera,
   que es una elección **sin evidencia** (ver la trampa del orden de pistas).
   Medirlas pide OCR, que no está instalado. Son `clean`, así que corregirlas
   cuesta un remux completo y puede ir con la cola del cable.

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
4. **El texto**, cuando no queda metadata: el castellano usa vosotros
   (`prestad`, `habéis`, `tenéis`) y léxico ibérico que el latino nunca usa.
   Herramienta de un solo uso en el scratchpad, no en el código: es una
   heurística cara y solo hace falta para limpiar lo ya importado.

El SDH sin etiquetar se reconoce igual, por contenido: acotaciones entre
corchetes (`[inhala profundo]`). En Club de Cuervos la pista de diálogo da 0% y
la SDH entre 10% y 28%, en los 25 episodios medidos.

## Credenciales

Todas 0600 y en `.gitignore`. **Ninguna se escribe en un mensaje.**
`.radarr-token` es la clave **global** de Radarr: filtrarla es "Radarr
comprometido". `.radarr-hook-secret` (usuario y secreto), `.jellyfin-token`,
`.env.systemd`.
