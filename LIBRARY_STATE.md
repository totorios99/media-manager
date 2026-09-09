# Estado del trabajo de biblioteca — 2026-09-08

Documento de continuidad. Se escribe para que una sesión nueva pueda retomar sin
depender de la memoria de la conversación. El hilo de hardware y el de servicios
viven en el vault de homelab; aquí solo biblioteca y media-manager.

## Reparto de responsabilidades

| Quién | Posee | No toca |
|---|---|---|
| **media-manager** (esta sesión) | `/media/hdd1/Movies`, `/media/hdd1/.recycle`, normalización, injertos | Radarr, Prowlarr, seerr, transmission (salvo lectura) |
| **homelab** (sesión peer) | Radarr, Prowlarr, seerr, transmission, Jellyfin, contenedores, documentación | Archivos de `Movies/` y `.recycle/` |

Esa línea es real, no teórica: Shutter Island fue el primer caso donde ambos
íbamos a escribir el mismo archivo.

## La tubería, tal como está hoy

```
seerr (auto-aprueba) → Radarr (perfil "4K Preferred") → Prowlarr → transmission
  → importa → webhook On Import/On Upgrade → media-manager
  → suggest_tracks → remux → verify → finaliza → ntfy tema "media"
```

**Construido y probado en producción:** webhook con HTTP Basic, normalización,
verificación, finalización automática (borra el crudo, renombra, barre basura),
notificación ntfy. El job 743 fue el primer disparo real de extremo a extremo.

**Escrito y con tests, sin cablear:** `graft.py` — recupera pistas que una
actualización tiró, usando la copia de la papelera. Mide el desfase por
correlación cruzada y **se niega** si no hay pista común, si el pico es < 0.5, o
si el desfase supera 15 s.

**Sin escribir:** la pasada de reconciliación. Radarr no reintenta un webhook
fallido, así que si media-manager está caído cuando llega una actualización, el
evento se pierde y la copia de la papelera expira en 7 días sin que nadie avise.
Es el hueco más peligroso que queda.

## En curso al momento de escribir

- Injerto de Shutter Island: ~16 GB de 34. Sincronía medida en 3 puntos: ±0.4 ms,
  pico 0.97-0.99. Mismo máster, sin corrección necesaria.
- 4 remuxes de normalización en cola (de 9 lanzados).
- 3 descargas activas.
- Todo compite por 36 MB/s, de ahí la lentitud.

## Watchers vivos (procesos, sobreviven a la sesión)

| Script | Qué hace |
|---|---|
| `scratchpad/watch_recycle.sh` | Cada 2 min compara idiomas de audio entre papelera y biblioteca. ntfy prioridad 4 si una actualización perdió pistas. **Es la única red mientras no exista la reconciliación.** |
| `scratchpad/watch2.sh` | Progreso del injerto, solo `stat`. |

Ambos son `nohup`/`setsid` de esta sesión: **mueren en un reinicio de la máquina
y no vuelven solos.** Convertirlos en servicio, o mejor, meter la reconciliación
dentro de media-manager, que ya arranca solo.

## Convenciones (fuente: `scan.py suggest_tracks`, `app.py _BANDS`)

Audio: idioma original primero **y** por defecto (mejor códec que no sea
TrueHD/Atmos), luego español, luego Atmos — nunca por defecto. Comentarios
ordenan al final. Series (`multi_audio`) conservan todos los doblajes.

Subtítulos: forzados primero (el del idioma original, único por defecto), luego
una imagen (PGS/VobSub) por idioma, luego un SRT por idioma. Solo idiomas
deseados: original + eng + spa.

Triaje de bitrate: techos 25/15/8 Mbps (uhd/fhd/sd), pisos 15/8/4, piso
escalado ×0.6 para HEVC/AV1/VP9. Resolución **por ancho primero** — un 4K scope
es 3840x1608 y por altura se leería como 1080p.

Nombres: `Título (Año)/Título (Año).mkv` + `folder/backdrop/landscape/logo`.

## Pendiente, por orden

1. Cablear `graft.py` al webhook (rama On Upgrade) + **pasada de reconciliación**
2. **45 películas por encima del techo** — ~1.36 TB recuperables
3. **870 episodios de series** sin procesar — el grueso real
4. Verificación estructural completa de 78 títulos (la rápida ya pasó)
5. Migrar media-manager a contenedor CasaOS

## Migración a contenedor: lo que rompe

- Montar `/media/hdd1` **en la misma ruta dentro y fuera**. 40 filas guardan
  rutas absolutas, y un montaje padre único evita el *cross-device link* que
  homelab encontró en Radarr.
- La imagen necesita `mkvtoolnix`, `ffmpeg`, `tmux`. tmux es crítico: cada
  trabajo se lanza en una sesión tmux, así que una imagen sin él falla al
  arrancar un job, no al construirse.
- `MM_NO_SYSTEMD=1`. El control de CPU se descarta por decisión del usuario.
- Montar `.radarr-hook-secret`; `TMDB_API_KEY` pasa a variable del compose.

## Cosas que cuestan caro si se olvidan

- **Radarr copia, no enlaza.** Bind mounts separados → EXDEV. Cada importación
  ocupa el doble hasta que se borra la fuente.
- **El bus es USB 2.0** por un puente JMicron JM20337 que solo habla USB 2.0.
  40 MB/s es el techo real y no lo arregla ningún puerto ni cable.
- **`format.duration` miente.** Es el máximo de todas las pistas. La duración de
  imagen es el timestamp del último paquete de video.
- **Duración igual no es sincronía igual.** The Fast and the Furious: +995.4 ms
  constante con duración idéntica.
- **Cuatro archivos truncados** vivieron 9 meses pareciendo completos porque la
  cabecera anunciaba la duración entera. Ya borrados; sus carpetas siguen y las
  4 películas son las únicas monitorizadas en Radarr.
