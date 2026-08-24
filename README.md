# Monitor de citas — visa japonesa (Colombia)

Vigila el calendario de la Embajada del Japón en Colombia y avisa por correo
o Telegram cuando se libera un cupo de visa de corta estadía. No agenda: avisa.

Reporta **todas** las franjas libres de la ventana, no la primera que encuentra,
y vuelve a insistir con las que sigan disponibles pasado un rato, para que un
aviso perdido no cueste el cupo.

Solo librería estándar de Python. Sin `pip install`, sin entorno virtual.

## Cómo funciona

1. GET a `/reservations/calendar` para abrir sesión y tomar los tokens CSRF de
   CakePHP. Caducan, por eso se piden frescos en cada corrida.
2. Un POST por semana a `/ajax/reservations/calendar` con `date` y
   `disp_type=week`. Responde JSON con una llave `html`.
3. Lee los cupos del texto `残<i>N件` de cada celda.
4. Compara contra `estado.json` y avisa solo de lo que no había visto.

## Montaje

Hay que configurar al menos un canal de aviso. Puedes poner los dos.

### 1a. Correo (recomendado si el aviso es para otra persona)

Con Gmail necesitas una **contraseña de aplicación**, no la clave normal de la
cuenta. Se genera en la configuración de seguridad de Google, con la
verificación en dos pasos activada. Genérala tú: no la pongas en ningún archivo
del proyecto, va solo en variables de entorno.

```bash
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT=587
export SMTP_USER="tucorreo@gmail.com"
export SMTP_PASS="la-contraseña-de-aplicación"
export EMAIL_DE="Monitor de citas <tucorreo@gmail.com>"

# Varios destinatarios separados por coma
export EMAIL_PARA="tuamiga@correo.com,tucorreo@gmail.com"
```

El correo va en HTML con las franjas agrupadas por día y un botón directo al
calendario, más una versión en texto plano para clientes que no rendericen HTML.

### 1b. Telegram (opcional, llega más rápido al celular)

Escríbele a [@BotFather](https://t.me/BotFather), manda `/newbot`, guarda el
token. Después mándale cualquier mensaje a tu bot nuevo y abre:

```
https://api.telegram.org/bot<TU_TOKEN>/getUpdates
```

De ahí sacas el `chat.id`.

### 2. Primera corrida

```bash
python3 monitor.py check --semanas 6 --solicitudes 1
```

Debe imprimir algo como `126 franjas revisadas | 0 con cupo | 0 nuevas`.
La primera corrida solo guarda estado, no avisa de lo que ya estuviera libre.

Si sale error, el código de salida dice qué pasó:

| Código | Qué pasó |
|---|---|
| 2 | El sitio cambió. Correr `diagnose` |
| 3 | Red caída, o bloqueo por frecuencia |

### 3. Dejarlo corriendo solo

Hay dos caminos. La diferencia real no es técnica, es de disponibilidad.

| | GitHub Actions | launchd en el Mac |
|---|---|---|
| Corre con el equipo apagado | Sí | No |
| Costo | Gratis si el repo es público | Gratis |
| Puntualidad | Se retrasa 5 a 15 min en horas pico | Puntual |
| Montaje | Subir repo y cargar secrets | Un comando |

Si el aviso es para otra persona, usa GitHub Actions. Un monitor que solo
funciona mientras tu portátil está abierto no es un monitor confiable.

#### Opción A: GitHub Actions (recomendada)

Crea un repositorio **público** y sube estos archivos, con el workflow en
`.github/workflows/monitor.yml`.

Público importa: los repos públicos tienen minutos de Actions ilimitados, los
privados traen 2.000 al mes. Cada pasada se factura como 1 minuto completo
aunque dure 26 segundos, así que correr cada 10 minutos cuesta unos 4.320
minutos al mes. No cabe en un repo privado gratis.

No hay riesgo en publicarlo: las credenciales van en Secrets, no en el código.
Lo único que se commitea es `estado.json`, con horarios de citas.

En Settings > Secrets and variables > Actions, pestaña **Secrets**:

```
SMTP_HOST        smtp.gmail.com
SMTP_PORT        587
SMTP_USER        tucorreo@gmail.com
SMTP_PASS        la contraseña de aplicación
EMAIL_PARA       tuamiga@correo.com,tucorreo@gmail.com
EMAIL_DE         Monitor de citas <tucorreo@gmail.com>
```

En la pestaña **Variables** del mismo lugar (no son secretas, y así las cambias
sin tocar código):

```
SEMANAS          6
SOLICITUDES      1
RECORDAR         45
```

Antes de dejarlo solo, dispáralo a mano: pestaña Actions, el workflow, botón
"Run workflow". Revisa el log del paso "Revisar calendario". Debe imprimir algo
como `126 franjas revisadas | 0 con cupo | 0 nuevas`. Si sale error, el código
de salida dice qué pasó.

Dos advertencias sobre el cron de GitHub: no es puntual, se retrasa en horas
pico, y GitHub desactiva los workflows programados en repos sin actividad
durante 60 días. Para un trámite de unas semanas no estorba, pero si esto se
alarga, revisa que siga activo.

Si prefieres el repo privado, sube el intervalo a 30 minutos en el cron
(`*/30 * * * *`) para caber en los 2.000 minutos gratis.

#### Opción B: launchd en el Mac

Sirve si prefieres no subir nada a la nube y el equipo se queda encendido.

```bash
RUTA=$(pwd)
sed -e "s|__RUTA__|$RUTA|g" \
    -e "s|__SMTP_USER__|$SMTP_USER|g" \
    -e "s|__SMTP_PASS__|$SMTP_PASS|g" \
    -e "s|__EMAIL_PARA__|$EMAIL_PARA|g" \
    com.vectiva.visa-monitor.plist > ~/Library/LaunchAgents/com.vectiva.visa-monitor.plist

launchctl load ~/Library/LaunchAgents/com.vectiva.visa-monitor.plist
tail -f monitor.log
```

Para detenerlo:

```bash
launchctl unload ~/Library/LaunchAgents/com.vectiva.visa-monitor.plist
```

Dos cosas que muerden acá. launchd no despierta el Mac: si duerme, el monitor
duerme. Y el plist usa rutas absolutas, así que si mueves la carpeta después
hay que regenerarlo. Déjala en un sitio definitivo desde el principio.

Para probar sin esperar, corre el ciclo en primer plano:

```bash
python3 monitor.py watch --intervalo 600
```

## Parámetros

| Opción | Por defecto | Qué hace |
|---|---|---|
| `--semanas` | 6 | Semanas hacia adelante. Cada una es un POST. |
| `--solicitudes` | 1 | Personas que viajan. Filtra celdas con menos cupos. |
| `--intervalo` | 600 | Segundos entre pasadas en modo `watch`. |
| `--recordar` | 45 | Minutos antes de reinsistir con un cupo que sigue libre. `0` lo desactiva. |
| `--estado` | `estado.json` | Dónde guardar lo ya visto. |
| `-v` | | Registro detallado. |

## Cobertura y recordatorios

`--semanas` define la ventana. Con 6 cubres mes y medio; el sistema permite
agendar hasta 3 meses antes del viaje, o sea unas 13 semanas. Cada semana es un
POST, así que 13 semanas son 13 peticiones por pasada. Si la fecha de viaje ya
está definida, es mejor cubrir solo hasta ahí.

Dentro de esa ventana el monitor reporta **todas** las franjas que sirvan, no
una por día ni una por semana. Si un lunes se liberan tres, el correo trae las
tres, agrupadas por día.

Sobre los recordatorios: la primera vez que aparece un cupo llega el aviso. Si
45 minutos después sigue libre, vuelve a avisar bajo el rótulo "siguen
disponibles". Así un correo que no se vio a tiempo no cuesta el cupo. Si una
franja desaparece y más tarde reaparece, cuenta como nueva otra vez.

## Sobre la frecuencia

Con los valores por defecto son 7 peticiones cada 10 minutos, espaciadas entre
sí con pausas aleatorias. Es el sitio de una embajada con capacidad limitada,
no un API público. Bajar el intervalo a segundos no consigue más cupos y sí
aumenta el riesgo de que bloqueen la IP.

Si empiezas a ver códigos 403 o 429 sostenidos, sube el intervalo.

## Pruebas

```bash
python3 test_monitor.py
```

```bash
python3 test_avisos.py
```

El primero corre contra `fixture_real.html`, capturado del endpoint real: cubre
cero cupos, cupos con otra clase CSS, el salto de año en la semana que cruza
diciembre, y la detección de cambios en el sitio.

El segundo cubre la agrupación por día, que ninguna franja se pierda en la
redacción, y el ciclo de vida del estado: cuándo una franja es nueva, cuándo
toca recordarla, y qué pasa cuando desaparece y vuelve.

## Lo que este monitor no hace

No reserva. El formulario tiene reCAPTCHA y pide datos personales del
solicitante. Llega la alerta, y una persona entra y agenda.
