# Plan de acción: biblioteca en convención

2026-10-02. Parte de la auditoría de hoy (`library_audit.py`, 346 películas y 1201
episodios leídos fichero a fichero) y absorbe `SUBTITLES-PLAN.md`: ese plan sigue
siendo el estándar de subtítulos; este documento ordena **cómo y en qué orden** se
llega a él con toda la biblioteca.

## 1. Punto de partida

**Ningún título cumple todo el estándar** (0/346 y 0/1201). Casi todo es subtítulos,
que es justo lo que el plan de subtítulos aún no ha aplicado.

| fallo | películas | episodios | se arregla con |
|---|---|---|---|
| español sin etiqueta de variante (es-419) | 183 | 793 | propedit (tras medir la variante) |
| orden de subtítulos incorrecto | 145 | 640 | **remux** |
| pistas extra fuera de Latino/Inglés/Latino forzado | 29 | 382 | **remux** |
| PGS / VobSub | 157 / 13 | 211 | OCR + **remux** |
| sin inglés de texto incrustado | 46 | 453 | proveedor u OCR + remux |
| sin Latino de texto incrustado | 159 (138 con sidecar es-MX) | 215 | incrustar el sidecar o proveedor |
| audio por defecto incorrecto | ~25 → 3 falsos positivos | ~200 → 193 The Office (decisión §8.1) | propedit (hecho, F1) |
| idioma de vídeo `und` / título vacío o con basura | ~22 → 0 | ~290 → 0 (más 37 de Death Note, corregidos) | propedit (hecho, F1) |
| sin doblaje latino (límite de la fuente) | 229 | 264 | otro release (§5) |
| castellano presente (audio / subs) | 2 (subs) | 74 audio (DBZ) / 193 subs (The Office) | decisión §8, remux |

Volumen de la pasada que reescribe ficheros: **224 películas (3,33 TB) y 821 episodios
(0,89 TB)**; unos 4,2 TB. El disco sigue por USB 2.0 (`lsblk`: `usb`, ~17 MB/s medido
en el remux de ayer): **~70 h de escritura y otras ~35 h verificando, unas 100–140 h**,
como ya estimaba el plan de subtítulos. Bloqueado por hardware (§6).

## 2. Principios

1. **Primero lo barato y reversible**: etiquetas con `mkvpropedit` (segundos por fichero,
   sin copia) antes que remux (24 GB leídos y escritos).
2. **Un fichero se reescribe una sola vez.** Todo lo que exija remux (orden, quitar
   PGS/castellano, incrustar SRT) se acumula y se hace en la misma pasada (§6), no en
   tres.
3. **Ningún PGS sale sin un sustituto verificado** (regla del plan de subtítulos).
4. **Verificar la propiedad, no el código de salida**: hash por stream del original y
   del remux (`ffmpeg -c copy -f streamhash`) antes de borrar el original. `delete-original`
   **borra**, no recicla.
5. **Lo que entra nuevo ya nace conforme** (§4): así la lista no vuelve a crecer.
6. Medir antes de afirmar: la sincronía solo cuenta si la re-medición global
   (`ffsubsync --gss`) da factor 1,000 y desfase ~0; la medición por ventanas no es fiable.

## 3. Fases

### F0. Hecho (2026-09-24 → 10-02)
- Auditoría completa y repetible: `library_audit.py` (solo lectura, `library_audit.json`).
- Filtro de subtítulos: `subs_clean.py` + `POST /api/subs/fix` (quita publicidad,
  corrige desfase fijo o velocidad estándar, rechaza el resto, guarda `.srt.orig`).
  Backlog: 28 de 30 `.srt` corregidos; 2 rechazados con motivo.
- Capítulos de Dragon Ball Super (`chapters_clean.py`): 131 episodios con `OP/Episodio/ED/Preview`
  (2026-10-04). El capítulo tras el OP no puede quitarse: Jellyfin guarda los capítulos sin fin y el
  Intro dura hasta el siguiente; sin él el salto llegaba al ED. 131/131 con segmentos, ningún Intro > 150 s.
- Importaciones: escaneo de Jellyfin al importar y avisos "Ya disponible / No disponible".
- Faltantes: búsquedas en curso con homelab (§5).

### F1. Etiquetas con propedit (HECHO, 2026-10-02)
Dos herramientas, porque la primera no alcanza:
- `propedit_batch.py` lanza el job `propedit` de la app (aplica `suggest_tracks`). Aceptó
  **26 de 299**: la app rechaza (400) cualquier título cuyo plan quite o añada pistas
  (castellano, extras): 272 casos, que quedan para F6.
- `meta_fix.py` repara **sin tocar pistas**: idioma de vídeo `und` → el original, título
  vacío → `title_display`, y audio por defecto (nunca un comentario; un default válido no se
  mueve; TrueHD/Atmos solo cede ante una pista ligera **del mismo idioma**). 268 ficheros
  editados + los 37 de Death Note, 0 fallos, valores anteriores en `meta_fix.jsonl`.
  The Office queda fuera del audio por defecto hasta la decisión §8.1.
- Efecto medido con la auditoría: fallos de audio y metadata de ~25 películas y ~500
  episodios a un puñado, todos con decisión pendiente (The Office 193, DBZ castellano 74)
  o falso positivo conocido (idiomas `nob`/`und`).
- Lección: dos reglas mías de la auditoría marcaban falsos positivos (TrueHD sin pista
  ligera del mismo idioma; animación sin doblaje español). Se corrigieron antes de actuar.

### F2. Variante y etiqueta es-419 (sin remux)
- **Hecho (2026-10-02):** `language=spa` por sí solo **reseteaba un es-419 existente a "es"**
  (medido con `mkvpropedit` v101), de modo que cada job propedit que estampó idiomas borró
  la única evidencia dura Latino/castellano. `commands.py` escribe ahora `language-ietf`
  (propedit) y `--language id:es-419` (remux), con test sobre un mkv real.
  `meta_fix.py` etiqueta es-419/es-ES donde el **nombre** de la pista ya lo dice (sin leer el
  contenido).
- **En marcha (2026-10-04):** `subs_variant_pass.py` (supervisor `overnight.py`), para el español
  de texto sin evidencia en el nombre. Mide y escribe `subs_variant_pass.json`; no etiqueta nada.
  Plan de cierre en §9, P1.

### F3. Filtro de entrada en producción (depende de una decisión)
- **Activo desde 2026-10-02**: Bazarr llama a `/config/mm-subs-fix.sh {{subtitles}}` →
  `POST 172.17.0.1:8500/api/subs/fix` (mapa `/data`→`/srv/storage`; el script sale siempre
  con 0 en 10 s). Comprobado: `server.log` tiene POST desde 172.17.0.2 (el contenedor de Bazarr).
  El backlog de sidecars sin filtrar (91) terminó el 2026-10-04; 4 rechazados por velocidad no
  estándar (otro corte): Indiana Jones and the Last Crusade 1,078, The Devil's Advocate 1,100,
  Underworld 0,977, The Lord of the Rings: The Return of the King 1,099.
- El perfil 1 de Bazarr ahora pide también **inglés** completo (el estándar lo exige):
  faltan 43 películas y 141 episodios. Bazarr los busca cada 6 h y cada uno pasa por el
  filtro de uno en uno. Coste: los episodios son rápidos, pero cada película obliga a
  leer ~20 GB (~10 min de disco USB 2.0 saturado), unas 7 h en total para las 43. Si choca
  con descargas o remuxes, homelab puede espaciar la búsqueda.
- Pendiente de comprobar (ya en `SUBTITLES-PLAN.md`): que Radarr no pierda `.srt` en
  mejoras ("extra files"); SubDL no devuelve resultados.

### F4. Que el import ya nazca conforme (código)
- **Hecho:** `suggest_tracks` aplica el estándar de subtítulos: Latino completo, inglés
  completo (SDH solo si no hay otro), Latino forzado; sin forzados en inglés ni subtítulos
  de otros idiomas; castellano solo si el original es español; default = Latino completo
  cuando el audio que suena no es español (Neptune no activa el forzado por su marca) y
  ninguno cuando suena un español, original o doblaje de animación. PGS solo si su idioma
  no tiene texto. Tests en `test_sub_policy.py`.
- **Hecho:** el default de audio lo decide `audio_default.py` (TrueHD/Atmos solo si es la
  única pista del idioma; si no, el codec más compatible; nunca un comentario).
- **Corrección al plan original:** Bazarr descarga los subtítulos **después** del import,
  así que al remuxear en el import el SRT casi nunca existe todavía. Incrustar solo puede
  hacerse en una pasada posterior (F6, y luego un barrido periódico que incruste los SRT
  que Bazarr haya dejado desde la última vez), no dentro del remux del import.
- **Pendiente:** los SRT verificados entran por `ext_path` en el remux del import
  (`ADOPT_EXTERNAL_SUBS` sigue apagado: decisión del 2026-09-16; Antonio confirmó el
  2026-10-02 que se incrustan en F6 y el plan del 09-24 la revierte para lo que pase el
  filtro).
- **Pendiente:** OCR de PGS como paso del import: `tesseract` con `spa` y `eng` ya está
  instalado, y `ffmpeg` renderiza el PGS (probado con F&F6: reconoce frases completas).
  Falta el empaquetado: tiempos por evento, diccionario español, revisión de `l`/`I`, `¿¡` y
  cursivas, y el filtro de §2.6. Si el OCR no pasa el filtro, el título entra con su PGS y
  va a la lista de revisión.
- Criterio: un título nuevo de Radarr/Sonarr sale del import con 0 fallos en la auditoría.

### F5. Faltantes y doblaje (homelab; ver §5)

### F6. Pasada masiva de remux (bloqueada por hardware)
Una reescritura por título con el estándar completo: orden, PGS→SRT verificado, quitar
castellano/extras, incrustar el SRT Latino ya filtrado, etiquetas es-419. Orden de
trabajo por riesgo y valor:
1. Series (0,89 TB, ficheros pequeños, 821 episodios): barato, deja la serie entera
   conforme y valida el proceso.
2. Películas sin PGS, solo orden/extras (las que no necesitan OCR).
3. Películas con PGS, una vez el OCR haya pasado el filtro; las que no pasen se quedan
   con su PGS en una lista nombrada, sin excepciones silenciosas.

Cada título: remux → hash por stream → auditoría del fichero → `delete-original`.
Sin trabajos concurrentes con descargas pesadas (comparten el mismo disco SMR).

### F7. Mantenimiento
- Auditoría semanal con timer (`library_audit.py`) y diferencia respecto a la anterior;
  aviso solo si **sube** el número de incumplimientos.
- Cada import se audita al terminar; si no cumple, aviso "No disponible: <motivo>".

## 4. Orden y dependencias

```
F1 (hoy) ──► F2 (noche + código) ──► F4 (código) ──► F6 (hardware)
F3 (decisión) ──────────────────────► F4
F5 (homelab) ── en paralelo, sin dependencias
```
F1, F2, F3 y F5 no dependen del hardware y reducen la lista antes de F6. F4 puede
empezar ya, porque beneficia a todo lo que se descargue a partir de hoy.

## 5. Faltantes y doblaje latino

- Faltantes (Antonio: "las demás sí me gustaría tenerlas completas"; Formula 1 es
  intencional; Mr. Robot S02–S04 sin monitorizar "de momento", dicho por él a homelab).
  Estado según homelab a 2026-10-02: Rick and Morty S08–S09 descargadas; **Death Note**:
  la versión BD completa sustituyó los 37 episodios (los 26 HDTV antiguos están 7 días en la
  papelera de Sonarr); BoJack se queda en 1080p como mínimo (Antonio), así que los
  episodios que solo existen en 720p siguen faltando (~22); The Office S04E14 y S07E22
  ("Goodbye, Michael", Extended Cut) y Yellowstone S01E08 (fichero doble E08-E09) grabados;
  **sin release**: The Sopranos S05E01 "Two Tonys" y DBZ 251/253 (0–2 seeders).
- Radarr: ~~The Good Girls~~ cancelada y borrada por Antonio (2026-10-02);
  *Never Back Down* (casi sin seeders) y *The Hobbit: An Unexpected Journey* en curso.
- **Doblaje latino ausente: 229 películas y 264 episodios.** No se persigue: Antonio
  decidió el 2026-10-02 **no** crear formato personalizado ni perfil de audio Latino en
  Radarr/Sonarr; solo los subtítulos Latino (Bazarr). Si un release trae Latino, bien.

## 6. Hardware

El cuello de botella es el puente USB 2.0 del disco de la biblioteca (`/srv/storage`,
`TRAN=usb`, SMR). Hasta que haya SATA directo o un recinto USB 3 con UASP, F6 son
~100–140 h en las que el disco no admite otra carga. Todo lo demás (F1–F5, F7) es
ligero y se hace ya.

## 7. Qué mide el cierre

- `library_audit.py`: 0 fallos de audio y metadata, salvo "sin doblaje latino" (límite de
  la fuente, listado aparte).
- 0 PGS salvo la lista nombrada de títulos sin sustituto verificado.
- Subtítulos de toda la biblioteca en el mismo orden y con es-419/inglés etiquetados.
- Un import nuevo de prueba que salga conforme sin intervención.

## 8. Decisiones

**Resueltas (Antonio, 2026-10-02):**
1. The Office: el default de audio pasa al original, inglés (hecho, 193 episodios).
2. DBZ: se quita el castellano y el Latino queda como default (hecho, 288/288 con hash
   por stream, `dbz_castellano.py`); el episodio 199 no tiene Latino y no se toca.
3. Subtítulos externos de Bazarr: se incrustan en F6.
4. Bazarr post-procesado: activo (F3).
5. Sin formato personalizado ni perfil de audio Latino en Radarr/Sonarr.
6. Default de audio: TrueHD/Atmos solo si es la única pista del idioma; si no, el mejor
   codec con mayor compatibilidad.

**Resueltas (Antonio, 2026-10-03):**
7. F6 empieza sin esperar al cable SATA, pero solo cuando acaben la cola de sidecars y la pasada de
   variante **y** Transmission esté en reposo.

**Resueltas (Antonio, 2026-10-04):**
8. D1: un episodio que solo difiere en orden o default **no se reescribe**; `mkvpropedit` corrige el default.
9. D2: el sidecar incrustado se mueve a `/srv/storage/.recycle` (misma ruta relativa).
10. D4: OCR con `uvx pgsrip`. Prueba con Death Note S01E05 (PGS español): 382 subtítulos en 9 s,
    `¿?` 50/50 y `¡!` 5/5, un error visible (`cológueselo`); 4,7 % de palabras fuera de un vocabulario
    hecho con los 144 `.es-MX.srt` de la biblioteca, frente a 3,9–7,3 % de cinco SRT buenos.

**Abierta:** D3, revisión de los veredictos latino/castellano (P1).

## 9. Plan de implementación de lo pendiente (2026-10-04)

Orden: P1 → P2 (en paralelo P4) → P3 → F6 películas. Cada paso deja su comprobación medida.

### P1. Cerrar F2: etiquetas es-419 / es-ES (propedit, sin remux)
Depende de: que termine `subs_variant_pass.py` (268 de 280 muestras medidas el 2026-10-04 10:15).
1. `subs_variant_pass.py --report`: veredicto por grupo. Un grupo (serie, temporada, firma de
   pistas) hereda el veredicto de sus muestras si todas las que deciden coinciden; los grupos solo
   `unknown` y las películas `unknown` van a una lista aparte, sin etiqueta. Re-medir los veredictos
   `review` (los de antes de la regla débil de `coger`).
2. **D3:** Antonio revisa la tabla (serie/película, veredicto, nº de marcadores, ejemplos).
3. `--apply` (hoy se niega a propósito): `mkvpropedit --edit track:<mkv_id+1> --set
   language-ietf=es-419|es-ES`, el mismo selector que `meta_fix.py`; solo pistas con `spa` sin
   variante; salta ficheros con job activo; valores anteriores en `subs_variant_apply.jsonl`;
   `app._reinspect_in_place` para que la base vea la etiqueta.
4. Después de cualquier tanda de propedit: esperar a que Jellyfin relea los ficheros y lanzar
   "Media Segment Scan" (lección del 2026-10-03: editar un fichero le borra los segmentos).
5. Comprobar: releer las etiquetas con `mkvmerge -J` y repetir `library_audit.py`; la fila "español
   sin etiqueta de variante" debe bajar en lo etiquetado. Coste: segundos por fichero.
Los subtítulos castellano que aparezcan no se quitan aquí: quitar pistas exige remux (P2).

### P2. F6 en series (`f6_series.py`, escrito el 2026-10-04 con tests; sin ejecutar sobre la biblioteca)
Plan con la base de hoy (`f6_series.py` sin argumentos): 489 remux, 278 solo propedit, 254 conformes,
**218 bloqueados** hasta P1 (DBZ 178, Club de Cuervos 25, Rick and Morty 8, BoJack 7); 18 sidecars a incrustar.
Depende de: P1 (sin él no se sabe cuál de dos subtítulos españoles completos es el latino: DBZ
tiene dos `spa` sin variante y `suggest_tracks` elegiría a ciegas), D1, D2 y Transmission en reposo.
`f6_plan.py` (solo lee una copia de la base) da hoy: 1239 episodios; 698 con pistas que quitar
(698 GB), 382 que solo cambian de orden o de default (325 GB), 159 conformes.
1. **D1** (propuesta: no reescribir por orden). Para esos 382 basta `mkvpropedit` para el default;
   el orden es cosmético en Jellyfin y cuesta 325 GB de escritura por un USB que se ha caído dos
   veces. Con D1 = no, F6 series son ~700 GB, unas 11 h a 36 MB/s (estimación).
2. Molde `dbz_castellano.py`, no `delete-original` (que renombra y pierde el título del episodio):
   plan de `suggest_tracks` sobre la base → `commands.build_mkvmerge_remux` a un temporal oculto en
   la misma carpeta → verificación: pistas y orden según el plan, duración ±0,1 s, flags de default,
   etiquetas de idioma, **hash por stream** de todo lo conservado, capítulos idénticos → `fsync` →
   `os.replace` con el mismo nombre → `_reinspect_in_place` → línea en `f6_series.jsonl`.
3. Sidecars de series: solo hay 20 `.srt` (8 es-MX, 8 en, 4 hi); se incrustan los que pasaron el
   filtro. **D2**: qué hacer con el sidecar después (Jellyfin mostraría la pista dos veces).
   Propuesta: moverlo a `.recycle` (reversible); Bazarr lo volvería a bajar si falta, y el
   barrido periódico (F4) lo incrustaría.
4. Arranque: 1 episodio → revisión en Jellyfin (pistas, default, salto de intro) → una serie
   pequeña (Club de Cuervos, 45) → el resto como trabajo 3 del supervisor, de uno en uno, con la
   guardia de montaje y una guardia nueva: no arranca un episodio si algún `.part` de
   `Downloads/incomplete` creció en los últimos 10 min.
5. Tras cada serie: Jellyfin relee + "Media Segment Scan"; muestra de segmentos (Intro < 150 s).

### P3. OCR de PGS → SRT (F4 y F6.3)
Herramientas comprobadas: `tesseract` con `spa` y `eng`; no hay `pgsrip` ni `subtile-ocr`.
1. **D4** (propuesta): probar `uvx pgsrip` (sin instalarlo en el proyecto, como `ffsubsync`);
   si no da la calidad, decodificador propio del `.sup` (segmentos PCS/WDS/PDS/ODS) + tesseract.
2. Prototipo con 3 películas (F&F6 ya probado a mano): extraer el PGS (lee el fichero entero,
   de noche), OCR, limpieza (`l`/`I`, `¿¡`, cursivas), filtro `subs_clean` y medición `--gss`.
3. Puerta de calidad: confianza media de tesseract y % de palabras en diccionario por encima de un
   umbral que se fija con el prototipo; lo que no pase va a la lista nombrada de PGS que se quedan.
4. Salida: SRT junto al vídeo, que F6 incrusta en lugar del PGS. Alcance: 157 películas y 211 episodios.

### P4. F7 mantenimiento (código, sin disco pesado)
- `library_audit.py --diff` contra la última auditoría y timer semanal de `systemd --user` (como
  `bazarr-profile-batch.timer`); ntfy solo si sube el número de incumplimientos.
- Comprobar si el import ya avisa "No disponible: <motivo>" cuando un título sale no conforme
  (sin verificar hoy); si no, añadirlo al final del job.

### Fuera de este repo (homelab / Antonio)
- Los 4 sidecars rechazados: homelab los pone en la lista negra de Bazarr y busca otro; el filtro
  verifica el nuevo.
- Radarr "extra files" con `.srt` en espera; SubDL sin resultados (SUBTITLES-PLAN.md).
- LIBRARY_STATE "Pendiente": cable SATA, Lat-Team, One-Punch Man S1 sin perfil de Bazarr, Dragon Ball
  Super en un solo `Season 01`, peticiones rechazadas en Seerr.
