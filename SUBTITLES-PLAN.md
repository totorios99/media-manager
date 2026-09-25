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
- **Sincronía**: desfase medido **por tramos** contra una referencia (la pista oficial,
  aunque sea PGS: sus eventos dan los tiempos, o el audio).
  - Si la mediana es igual en todos los tramos, es un desfase fijo: se **corrige**
    moviendo todos los tiempos. Straight Outta Compton iba 0,19 s tarde en los cuatro
    tramos y se adelantó 190 ms. Man of Steel salía con 16 ms, no hizo falta.
  - Si la mediana crece de un tramo a otro, el subtítulo es de otro montaje u otra
    velocidad (cines frente a Director's Cut, 23,976 frente a 25): se **rechaza**.
- **OCR**: diccionario español; revisión de `l`/`I`, `¿¡` y cursivas.

Lo que no pasa se queda fuera, en una lista de revisión manual.

## Fases

0. **Inventario comprobado fichero por fichero** (solo lectura): qué subtítulos lleva
   cada película, en qué formato, con qué marcas, y qué `.srt` hay al lado. Sustituye
   las cifras sacadas de la base de datos (159 sin español completo, 102 solo PGS,
   78 texto), que no están comprobadas.
   **Resultado (2026-09-24, 342 películas, 0 ilegibles):**

   | | texto | imagen (PGS) | ninguno |
   |---|---|---|---|
   | Español completo | 80 | 103 | 159 |
   | English completo | 176 | 119 | 47 |
   | Español forzados | 33 | 10 | 299 |

   - De las 159 sin español completo, 11 son habladas en español y 4 son animación.
   - 44 no tienen ni español ni inglés completos.
   - 155 llevan algún PGS dentro. 26 tienen subtítulo al lado (24 `.es-MX`, 2 `.hi`).
   - 72 ya llevan español e inglés completos en texto; les falta comprobar la variante
     y el orden.

1. **Variante de lo que ya es texto**: latino, castellano o forzados mal marcados.
   **Subtítulos sueltos, resultado (2026-09-24):** 26 `.es*.srt`, todos completos
   (cobertura 0,96-0,97, ninguno es de forzados sin marcar). 14 latinos claros, 9
   probablemente latinos (0 formas de vosotros en más de 400 frases). A revisión:
   - The Rocky Horror Picture Show: **castellano con etiqueta `es-MX`** (21 frente a 0).
     Hay que sustituirlo.
   - Mrs. Doubtfire (2 frente a 4) y Nine to Five (1 frente a 2): muy pocas marcas.
   Los subtítulos de dentro de los `.mkv` se revisan en una pasada nocturna
   (`subs_variant.py embedded`), porque hay que leer cada fichero entero.

2. **OCR a SRT de los PGS oficiales** (español completo y los 10 forzados en imagen),
   con tiempos por evento, no por muestreo.
3. **Proveedores** para lo que siga faltando; configurar Bazarr para que cuente los
   subtítulos internos como presentes e ignore los PGS internos.
4. **media-manager**: nuevo orden y marcas en `suggest_tracks`; los SRT verificados
   entran por `ext_path` en el remux de cada import. **El OCR de PGS pasa a ser un paso
   automático del import**: casi todos los releases BluRay que baja Radarr traen PGS, y
   sin este paso la biblioteca volvería a llenarse de ellos. Si el OCR pasa el filtro,
   el SRT sustituye al PGS en ese mismo remux. Si no, la película entra con su PGS y
   va a la lista de revisión.
5. **Pasada masiva** (cuando esté el cable SATA): una reescritura por película con el
   estándar completo, quitando los PGS. Unas 110-140 h de disco por USB 2.0.
   **No se quita ningún PGS sin su sustituto verificado.**

## Qué PGS pueden quedar

Solo los de películas sin ningún sustituto verificado (sin proveedor y con un OCR que
no pasa la revisión). Están en una lista con nombre y motivo, y se retiran en cuanto
aparece un SRT que pase el filtro. No hay excepciones silenciosas.

## Pendiente de comprobar

- Radarr, "extra files": que al importar o mejorar un release no se lleve ni deje
  sueltos los `.srt` que están en espera (de homelab).
- SubDL no devuelve nada en Bazarr aunque el DNS ya resuelve (AdGuard lo desbloqueó el
  2026-09-24). Sospecha no comprobada: su conector no soporta el código `ea`.
