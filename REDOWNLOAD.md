# Pendientes de re-descarga

Títulos cuyo archivo fuente está dañado. No hay arreglo local: mkvmerge copia
fielmente lo que hay, así que re-remuxear vuelve a producir el mismo archivo
corto. La única salida es conseguir otra fuente.

Actualizado: 2026-09-06 (archivos rotos borrados)

## La tanda de diciembre 2025

Cinco descargas consecutivas, del 9 al 12 de diciembre de 2025, **todas
truncadas**. Mismo origen, cuatro días seguidos, cero supervivientes. Vale la
pena revisar de dónde salieron antes de volver a bajar de ahí.

| Título | Descargado | Tamaño | Duración real | Duración en header | Cómo falló |
|---|---|---|---|---|---|
| ~~Ballerina (2025)~~ | 2025-12-09 | 53.5 GB | 5129s (1h25) | 7479s (2h05) | **RESUELTO 2026-09-05**: sustituido por descarga 4K de 23.1 GB, 124.7 min verificados |
| ~~Thunderbolts (2025)~~ | 2025-12-10 | 34.1 GB | 4723s (1h19) | 7610s (2h07) | **RESUELTO 2026-09-05**: sustituido por descarga 4K de 22.7 GB, 126.8 min verificados |
| Captain America: Brave New World (2025) | 2025-12-11 | 44.1 GB | 5631s (1h34) | 7112s (1h59) | ruidoso: error de estructura Matroska en el byte 44099026690 |
| Superman (2025) | 2025-12-11 | 44.7 GB | 5143s (1h26) | 7762s (2h09) | ruidoso: se rompe a 00:21:19, resync fallido |
| Sinners (2025) | 2025-12-12 | 45.3 GB | — | 8255s (2h18) | verificación de duración |

Los cuatro remux truncados (185 GB) se borraron el 2026-09-03. **Las fuentes
originales siguen en disco** por si quieres compararlas con la nueva descarga
antes de reemplazarlas.

**Estado 2026-09-06: 2 de 5 resueltos, y los archivos rotos ya no están.**

Ballerina y Thunderbolts se sustituyeron por descargas 4K verificadas. Los
otros tres se borraron el 2026-09-06 tras confirmar por segunda vez que
decodifican cero fotogramas pasado el corte —135.9 GB entre los cuatro rotos,
contando The Good Girls—. Se conservaron las carpetas y su artwork: así Radarr
y Jellyfin mantienen la entrada, y Radarr pasa a `hasFile=false`, que convierte
un archivo aparentemente satisfactorio en una ausencia real.

Los cuatro son las únicas películas monitorizadas en Radarr de 324, así que se
buscarán solas en cuanto Prowlarr tenga indexadores. Si algún día aparece una
quinta monitorizada, alguien amplió la excepción sin decidirlo.

| Título | tmdb | Estado |
|---|---|---|
| Captain America: Brave New World | 822119 | borrado, monitorizado, seerr #24 |
| Sinners | 1233413 | borrado, monitorizado, seerr #26 |
| Superman | 1061474 | borrado, monitorizado, seerr #27 |
| The Good Girls | 541339 | borrado, monitorizado, seerr #25 |

## Anterior

| Título | Tamaño | Problema |
|---|---|---|
| The Good Girls (2019) | 1.77 GB | `moov atom not found`; descarga truncada de mayo 2025, ni mkvmerge ni ffprobe pueden abrirla |

Este es el mismo archivo que antes figuraba aquí como «Las Niñas Bien (2018)».
TMDB 541339: título internacional *The Good Girls*, original *Las niñas bien*,
estreno 2019-03-22. Estuvo listado dos veces bajo dos nombres —en este documento
con el título original y en la biblioteca con el internacional— y la duplicación
sobrevivió a una verificación que lo marcó «sin paquetes de video» sin que nadie
uniera los dos. Un título es su id de TMDB, no su nombre.

## Cómo se detectan estos

Dos firmas distintas, y una es traicionera:

- **Ruidosa**: mkvmerge sale con `EXIT:1` e imprime `Error in the Matroska file
  structure at position N` más `Resync failed`. Imposible de pasar por alto.
- **Silenciosa**: mkvmerge sale con `EXIT:0` sin una sola advertencia y produce
  un archivo corto. Solo lo atrapa `verify_output` comparando la duración
  medida contra la del header.

**No confíes en un código de salida 0.** Cuenta los frames:
`ffmpeg -ss <cerca del final> -i F -t 3 -map 0:v:0 -f null - -stats`. Un seek
más allá del final sale con código 0 y no decodifica nada — eso me hizo declarar
sano a Thunderbolts durante media hora.

La forma barata de medir la duración real es
`mkvpropedit --add-track-statistics-tags F`, que escribe un tag `DURATION` por
pista. Lee el archivo entero (~50 min para 34 GB en este disco) y **cambia el
mtime**, con lo que se pierde la fecha de descarga: anótala antes.
