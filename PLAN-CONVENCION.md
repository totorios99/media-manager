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
| audio por defecto incorrecto | ~25 | ~200 (193 The Office, Yellowstone, Sopranos) | propedit (en curso) |
| idioma de vídeo `und` / título vacío o con basura | ~22 | ~290 | propedit (en curso) |
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

### F1. Etiquetas con propedit (en curso, hoy)
`propedit_batch.py` lanza el job `propedit` de la propia app sobre los 299 ficheros que la
auditoría marcó por audio por defecto o metadata: aplica `suggest_tracks`
(original primero, TrueHD/Atmos nunca por defecto, idioma de vídeo, título, nombres) y se
verifica con la auditoría (`--verify`). La app **rechaza** un propedit que quite o añada
pistas: esos títulos (castellano, extras) quedan para la pasada de remux, que es lo
correcto. No borra nada. Criterio de cierre: `--verify` sin
"still failing" salvo los rechazados.

### F2. Variante y etiqueta es-419 (sin remux, esfuerzo bajo)
- Lanzar de noche `subs_variant.py embedded` (lee cada fichero entero: horas) para saber
  qué español de texto es Latino y cuál castellano **por el contenido**, no por la etiqueta.
- **Código nuevo**: `build_mkvpropedit_chain` escribe `language=spa` y nunca
  `language-ietf`, así que no puede poner `es-419`. Añadir `--set language-ietf=es-419`
  para los Latino verificados y `es-ES` para los castellanos, con test.
- Resultado: baja a casi 0 el fallo más grande (793 + 183) sin tocar un solo fotograma.

### F3. Filtro de entrada en producción (depende de una decisión)
- Activar el post-procesado de Bazarr (`{{subtitles}}` → `POST /api/subs/fix`,
  `172.17.0.1:8500`, mapa `/data`→`/srv/storage` ya hecho). Lo configura homelab cuando
  Antonio lo confirme en su sesión. Hasta entonces, cada subtítulo nuevo entra sin filtro.
- Pendiente de comprobar (ya en `SUBTITLES-PLAN.md`): que Radarr no pierda `.srt` en
  mejoras ("extra files"); SubDL no devuelve resultados.

### F4. Que el import ya nazca conforme (código)
- `suggest_tracks`: nuevo orden y marcas del estándar (Latino completo, Inglés completo,
  Latino forzado; default según el audio original); sin forzados en inglés; castellano
  solo si el original es español.
- Los SRT verificados entran por `ext_path` en el remux del import (hoy
  `ADOPT_EXTERNAL_SUBS` está apagado por la decisión del 2026-09-16; el plan del
  2026-09-24 la revierte para lo que pase el filtro).
- **OCR de PGS como paso del import**: `tesseract` con `spa` y `eng` ya está instalado, y
  `ffmpeg` renderiza el PGS (probado con F&F6: reconoce frases completas). Falta el
  empaquetado: tiempos por evento (no por muestreo), diccionario español, revisión de
  `l`/`I`, `¿¡` y cursivas, y el filtro de §2.6. Si el OCR no pasa el filtro, el título
  entra con su PGS y va a la lista de revisión.
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

- Faltantes que busca homelab (Antonio: "las demás sí me gustaría tenerlas completas";
  Formula 1 es intencional; Mr. Robot S02–S04 se queda sin monitorizar "de momento",
  dicho por él a homelab). Según homelab: Rick and Morty S08–S09 buscadas y en cola;
  BoJack (31), Death Note (11), The Office S04E14, Yellowstone S01E08 (llegó como fichero
  doble E08-E09) en curso; **sin release todavía**: The Office "Goodbye, Michael" (S07E22),
  The Sopranos "Two Tonys" (S05E01) y DBZ 251/253.
- Radarr: *The Good Girls* (2019) no está en Radarr; *Never Back Down* (release casi sin
  seeders) y *The Hobbit: An Unexpected Journey* en curso.
- **Doblaje latino ausente: 229 películas y 264 episodios.** No se arregla en la
  biblioteca: exige otro release con audio Latino. Sonarr no tiene hoy preferencia de
  idioma. Propuesta, **si Antonio la quiere**: un formato personalizado "Latino" en
  Radarr/Sonarr (homelab) para que las mejoras futuras prefieran releases duales, y una
  búsqueda dirigida solo de los títulos que él elija (no 229 a la vez: cada uno son
  decenas de GB por el mismo disco).

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

## 8. Decisiones que necesito de Antonio

1. **The Office (US)**: 193 episodios llevan Latino por defecto aunque el original es
   inglés y no es animación. La convención dice "original primero". ¿Lo dejo como está
   (intencional) o lo paso a inglés?
2. **Dragon Ball Z**: 74 episodios conservan el castellano (Montaje Selecta). La
   convención lo elimina salvo original español; en el episodio 199 es el único doblaje
   disponible además del japonés. ¿Se quita, se conserva, o solo se deja de marcar?
3. **Subtítulos externos de Bazarr**: ¿se incrustan en F6 (estándar del plan) o se
   mantienen como sidecar de Bazarr (decisión del 2026-09-16)? Recomiendo incrustar en
   la pasada, no antes.
4. **Bazarr post-procesado**: confirmar en la sesión de homelab (§3, F3).
5. **Búsqueda de doblaje latino** (§5): ¿sí o no, y para qué títulos?
