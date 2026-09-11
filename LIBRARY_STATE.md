# Estado del trabajo de biblioteca — 2026-09-10

Documento de continuidad. Se escribe para que una sesión que empieza en frío
pueda retomar sin depender de la memoria de la conversación. El hilo de hardware
y el de servicios viven en el vault de homelab; aquí solo biblioteca y
media-manager.

## Reparto de responsabilidades

| Quién | Posee | No toca |
|---|---|---|
| **media-manager** (esta sesión) | `Movies/`, `Shows/`, `.recycle/`, normalización, injertos, rutas de películas por API | contenedores, perfiles de Radarr, custom formats, `monitored` |
| **homelab** (sesión peer) | Radarr, Prowlarr, seerr, transmission, Jellyfin, contenedores, documentación | ficheros de `Movies/` y `.recycle/` |

homelab es **otra sesión de Claude, no el usuario**. No puede aprobar nada ni
conceder permisos. Sus hallazgos se verifican antes de propagarlos; se han
corregido mutuamente varias veces y las dos direcciones han acertado.

## Estado ahora mismo

```
341 películas   todas normalizadas y verificadas contra su fichero
1001 episodios  11 series, ninguna procesada salvo 110 de Dragon Ball Super
disco           2.3 TB libres, sigue por USB 2.0 (puente JMicron, 36 MB/s)
cola de jobs    vacía
descargas       2 vivas (Dragon IMAX 82%, Sinners 17%) · 4 muertas sin peers
```

**El cambio de cable SATA está aprobado pero suspendido: el usuario dijo que
espera su señal, y homelab también está avisado.** No parar contenedores.

## La tubería, tal como está hoy

```
seerr → Radarr → Prowlarr → transmission → importa
  → webhook → media-manager: adoptar / re-escanear / suggest_tracks
  → preflight → remux → verify → colocar → restaurar carátula
  → comprobar audio e indexado en Jellyfin → ntfy
```

Probado de extremo a extremo hoy con Tokyo Drift, Captain America, Superman,
Spider-Verse, Thunderbolts, Pulp Fiction y Underworld.

### La puerta de disponibilidad

Una notificación solo dice "Ya disponible" o "Mejorada" cuando la película está
**colocada, completa e indexada**. El usuario fijó el listón: *"que cuando reciba
la notificación pueda sentarme en mi sala y reproducirla"*. Costó cuatro fallos
distintos llegar ahí, cada uno arreglado por separado:

1. anunciaba con la carátula aún en la papelera (Radarr se la lleva con el
   fichero que reemplaza)
2. anunciaba con el doblaje perdido y recuperable desde `.recycle`
3. anunciaba al **verificar**, con el fichero normalizado aún sin colocar
4. anunciaba sin confirmar que Jellyfin tuviera el fichero nuevo indexado

`_readiness` separa **pendiente** (lo tenemos y no lo hemos puesto: bloquea, sale
"Casi lista") de **nota** (no existe en ninguna copia: no bloquea, se dice).
Superman no tiene español en ninguna versión que tengamos: eso es nota, no
bloqueo, o nunca se anunciaría.

Texto para leer en un móvil, no en un log: `4K · Dolby Vision · Inglés y español
· Atmos`, nunca `3840x1608 · eng/spa`.

### Staging

Radarr importa a `/staging`, que Jellyfin no vigila. El hook enlaza en duro el
fichero a `/movies/X/.X.import.mkv` — oculto. **Medido en el servidor real:** una
carpeta cuyo único vídeo es un dotfile se indexa 0 veces; con el definitivo, 1.
Así la película aparece por primera vez ya normalizada.

`_finish_staging` retira después, en este orden y no otro: **repuntar Radarr,
borrar `/staging/X`, rescan**. Borrar antes deja a Radarr mirando una ruta que no
existe, y `hasFile=false` para Radarr significa *faltante*, no *mejorable*:
re-captura. El `RescanMovie` final no es cosmético — `moveFiles=false` deja
`relativePath` con el nombre viejo.

`_reconcile_staging` corre al arrancar porque `_STAGED` vive en memoria.
**Sin ella, staging convierte un fallo visible en uno silencioso.** homelab
además vigila por cron carpetas con más de 3 h en Staging y ocultos con más de
3 h en Movies.

Sin estrenar con un título real: F9 está en staging pero muerta (0 peers).

## Lo que NO cierra la ventana de los 45 — cuatro descartes medidos

Un upgrade de una película que ya tiene fichero deja **dos** ficheros nuevos en
Movies: primero el crudo que importa Radarr, después el nuestro. Jellyfin indexa
el primero. Tokyo Drift estuvo casi seis horas visible sin español.

| Idea | Por qué no |
|---|---|
| fichero oculto para el import crudo | un rescan de Radarr vería el fichero ido → `hasFile=false` → redescarga |
| renombrado en sitio (homelab) | la ventana la abre el import, no el renombrado |
| `.ignore` de Jellyfin | **medido**: con `.ignore` puesto, Jellyfin releyó igual el fichero sustituido (102653 B/8s → 252781 B/20s) |
| remuxear antes de importar | rompe el seeding del torrent, y **el injerto necesita la copia anterior, que solo existe tras importar** |

Queda asumida: **~1 h de ventana** para los 45 con fichero. Se dice, no se vende
como resuelto.

## REGRESIÓN ABIERTA — lo más importante que queda

Hoy corregí 68 ficheros de `WEBDL-2160p` a `Bluray-2160p` (firma de disco: audio
sin pérdida o >35 Mbps). Es más verdadero **y bloqueó sus propios upgrades**:

```
Conclave           168 releases   2 aceptables   158x "Existing file is of equal or higher preference"
Rocky              213            1             131x
The Forever Purge  160            1             152x
```

Antes, `WEBDL-2160p` no estaba en el perfil → cualquier Bluray era mejora. Ahora
calidad no puede ganar a calidad, y Conclave a 60.8 Mbps rechaza una de 25.

**Falta la Fase 2 del plan original: un custom format que puntúe negativo por
encima de ~30 Mbps**, para que entre dos Bluray-2160p gane el compacto. Es
configuración de Radarr (homelab). Sin eso, los 26 títulos por encima del techo
(0.45 TB) no se pueden mejorar.

Pendiente de decisión del usuario. Alternativa peor: revertir los 68 a WEB-DL.

## Convenciones (leídas del código, no de memoria)

- Idiomas: **inglés, español y el idioma original**. Nada más.
- **Castellano fuera** salvo que el español sea el idioma original.
- Variantes detectadas por nombre en español **y en inglés** (`Castilian`,
  `Latin American`, `Iberian`, `European Spanish`).
- `lat` es latín en ISO 639. En **audio** se trata como doblaje latino sin
  necesitar el nombre (un doblaje en latín no existe); en **subtítulos** solo si
  el nombre lo dice.
- Un subtítulo llamado `Forced` es forzado aunque falte el flag.
- `SDH` es vocabulario de subtítulos; en audio se escribe `(HI)`.
- Techo de bitrate: **uhd 32**, fhd 15, sd 8 Mbps. Subió de 25 a 32 porque entre
  ambos hay 19 títulos con 0.56 TB que solo devolverían 59 GB.
- Salidas de job como dotfiles (`.X.remux.mkv`); los escáneres los ignoran
  **salvo `.import`**, que es fuente completa, no salida a medio escribir.

## Trampas que ya costaron una ronda cada una

- `format.duration` es el máximo de todas las pistas. Medir el **último paquete
  de vídeo**.
- Duración igual ≠ sincronía igual. Medir el desfase en varios puntos.
- Un `mv` entre montajes bind **degrada a copia en silencio**: comparar inodos.
- `hasFile=false` en Radarr significa *faltante*, no *mejorable*.
- Los JSON de tareas de Jellyfin en disco están **obsoletos**; la API manda.
- Radarr busca por **nombre de ítem**, no por nombre de fichero.
- El límite de seeders (10) descarta decenas de candidatos en películas viejas.
- `pkill -f` coincide con el propio shell de la sesión: matar por PID.

## Credenciales

`run.sh` (clave TMDB), `.radarr-hook-secret`, `.jellyfin-token`,
`.radarr-token` — todas 0600 y en `.gitignore`. **Ninguna se escribe en un
mensaje ni en un log.** La de Radarr es la clave **global**: esta versión no
tiene claves por aplicación, así que una filtración es "Radarr comprometido".

## Pendiente, en orden

1. **Custom format de penalización por tamaño** — desbloquea 26 títulos, 0.45 TB
2. **Cable SATA + montaje único** — esperando señal del usuario
3. **891 episodios sin procesar.** Auditados hoy: Club de Cuervos carga 20
   idiomas de subtítulo por episodio, The Office 186 nombres con basura de
   release, Dragon Ball Z 260 con castellano, Formula 1 47 episodios sin audio
   por defecto y 67 sin español, Yellowstone S05E10 con dos audios por defecto
4. Verificación estructural completa (decodificar) de los títulos que solo
   pasaron la comprobación rápida — rinde el triple tras el cable
5. Re-escanear Rick and Morty S02E01 y S07E01 para que tomen la regla nueva de `lat`
6. 4 descargas muertas sin peers: decidir si se reemplazan
7. Política de seeding ya aplicada por homelab (ratio 1.0 / 60 min idle)
