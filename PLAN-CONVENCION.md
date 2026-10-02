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
- Capítulos de Dragon Ball Super (`chapters_clean.py`): 131 episodios con `OP/ED/Preview`;
  Jellyfin genera ya Intro/Outro/Preview correctos en 130 (el 131 no tiene intro ni avance).
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
- **Pendiente:** `subs_variant.py embedded` de noche, para el español de texto sin
  evidencia en el nombre. Lee cada fichero entero (mkvextract recorre todos los clusters):
  horas de disco USB, así que va después de DBZ y no junto a otras cargas.

### F3. Filtro de entrada en producción (depende de una decisión)
- **Activo desde 2026-10-02**: Bazarr llama a `/config/mm-subs-fix.sh {{subtitles}}` →
  `POST 172.17.0.1:8500/api/subs/fix` (mapa `/data`→`/srv/storage`; el script sale siempre
  con 0 en 10 s). Sin comprobar aún: la primera línea `[subs]` que venga de Bazarr y no de mí.
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
2. DBZ: se quita el castellano y el Latino queda como default (en marcha,
   `dbz_castellano.py`); el episodio 199 no tiene Latino y no se toca.
3. Subtítulos externos de Bazarr: se incrustan en F6.
4. Bazarr post-procesado: activo (F3).
5. Sin formato personalizado ni perfil de audio Latino en Radarr/Sonarr.
6. Default de audio: TrueHD/Atmos solo si es la única pista del idioma; si no, el mejor
   codec con mayor compatibilidad.

**Abiertas:** ninguna por ahora. Siguiente decisión probable: cuándo lanzar la pasada de
variante de subtítulos (horas de disco) y la señal del cable SATA para F6.
