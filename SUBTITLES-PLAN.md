# Plan de subtítulos: un solo estándar para toda la biblioteca

Decidido con Antonio el 2026-09-24. Objetivo: que cualquier película se abra igual,
con los mismos subtítulos en las mismas posiciones, y que todos estén bien hechos.

## El estándar

Subtítulos **dentro del `.mkv`**, en texto (SRT), en este orden fijo:

| pos. | pista | marcas |
|---|---|---|
| 1 | Español latino, completo | *default* si el audio original no es español |
| 2 | English, completo (SDH solo si no hay otro) | `hearing-impaired` si es SDH |
| 3 | Español latino, forzados | `forced`; solo si existen |

- **Sin PGS.** Excepción: anime o animación cuya única fuente sea ASS con estilos
  (carteles, karaoke), que se conserva como ASS.
- **Sin forzados en inglés**: Antonio ve siempre audio original con subtítulos
  completos en español, que ya cubren las partes en otro idioma.
- Castellano solo en películas cuyo idioma original es el español (como hasta ahora).

**Por qué dentro y no junto al vídeo:** el orden de las pistas solo se puede fijar
dentro del fichero. Jellyfin pone los subtítulos externos detrás de los internos y en
orden alfabético (`.en` antes que `.es`), así que "la segunda pista es el inglés" no se
sostendría.

**Probado en el Apple TV (Neptune), 2026-09-24:** Neptune **no** activa solo los
subtítulos forzados por su marca; hay que elegir la pista. Por eso el español completo
va primero y como *default*, y los forzados, que solo se usan con el doblaje, van al final.

## Fuentes, por orden de preferencia

1. **Oficial ya dentro del fichero**: SRT/texto que venía con el release.
2. **OCR del PGS oficial** (traducción del disco, ya sincronizada con ese máster).
   Es la fuente preferida para las películas que solo tienen PGS, no un plan B.
3. **Proveedores vía Bazarr**: Subsource, SubDL, subx (Subdivx) y OpenSubtitles.
   Solo donde no hay nada oficial. Son traducciones de aficionados, de calidad variable.

## Filtro de entrada (nada entra en un `.mkv` sin pasarlo)

Una vez dentro, cada corrección cuesta reescribir la película entera, así que se
verifica antes:

- **Completo**: número de frases y tramo cubierto frente a la duración de la película.
  El primer SRT de Man of Steel eran 13 frases de forzados sin marcar.
- **Variante**: formas de *vosotros* frente a *ustedes* y léxico; se rechaza el castellano.
- **Sincronía**: desfase medido contra una referencia (la pista oficial o el audio),
  con umbral.
- **OCR**: diccionario español; revisión de `l`/`I`, `¿¡` y cursivas.

Lo que no pasa se queda fuera, en una lista de revisión manual.

## Fases

0. **Inventario comprobado fichero por fichero** (solo lectura): qué subtítulos lleva
   cada película, en qué formato, con qué marcas, y qué `.srt` hay al lado. Sustituye
   las cifras sacadas de la base de datos (159 sin español completo, 102 solo PGS,
   78 texto), que no están comprobadas.
1. **Variante de lo que ya es texto**: latino, castellano o forzados mal marcados.
2. **OCR a SRT de los PGS oficiales** (español completo y los 10 forzados en imagen),
   con tiempos por evento, no por muestreo.
3. **Proveedores** para lo que siga faltando; configurar Bazarr para que cuente los
   subtítulos internos como presentes e ignore los PGS internos.
4. **media-manager**: nuevo orden y marcas en `suggest_tracks`; los SRT verificados
   entran por `ext_path` en el remux de cada import; no se conservan PGS.
5. **Pasada masiva** (cuando esté el cable SATA): una reescritura por película con el
   estándar completo, quitando los PGS. Unas 110-140 h de disco por USB 2.0.
   **No se quita ningún PGS sin su sustituto verificado.**

## Pendiente de comprobar

- Radarr, "extra files": que al importar o mejorar un release no se lleve ni deje
  sueltos los `.srt` que están en espera (de homelab).
- SubDL no devuelve nada en Bazarr aunque el DNS ya resuelve (AdGuard lo desbloqueó el
  2026-09-24). Sospecha no comprobada: su conector no soporta el código `ea`.
