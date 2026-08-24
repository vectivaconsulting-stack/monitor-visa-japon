# Monitor de citas — visa japonesa

Proyecto de un solo script. Vigila el calendario de la Embajada del Japón en
Colombia y avisa cuando se libera un cupo de visa de corta estadía.

## Reparto de responsabilidades

Python hace el trabajo determinista: pedir, parsear, comparar, avisar. Corre
solo, en cron o launchd, sin agente.

El agente hace tres cosas, y solo esas:

1. **Montaje.** Configurar Telegram, dejar el launchd corriendo, primera corrida.
2. **Diagnóstico.** Cuando el script sale con código 2, leer el HTML crudo y
   corregir el parser.
3. **Ajuste.** Cambiar semanas, solicitudes o intervalo cuando cambien los planes.

El agente **no** entra al ciclo de sondeo. Correr `watch` dentro de una sesión
del agente quema tokens en las corridas que no encuentran nada, que son casi
todas.

## Reglas duras

- **No agendar nunca.** El formulario de reserva tiene reCAPTCHA y pide datos
  personales. El script avisa; una persona reserva. No escribir código que
  intente completar la reserva, resolver el captcha o automatizar el formulario.
- **No bajar el intervalo por debajo de 5 minutos.** Es una embajada con
  capacidad limitada. Más frecuencia no consigue más cupos, y sí consigue que
  bloqueen la IP, que es el resultado contrario al que se busca.
- **Nunca escribir credenciales en archivos del proyecto.** `SMTP_PASS` y
  `TELEGRAM_TOKEN` van en variables de entorno o en el plist de launchd, que no
  se versiona. Si hace falta una contraseña de aplicación de Gmail, la genera la
  persona en su cuenta; el agente no la pide ni la maneja.
- **No cambiar `--solicitudes` sin preguntar.** Define cuántas personas viajan
  y filtra qué celdas cuentan como servibles. Un valor equivocado hace que el
  monitor avise de cupos que no sirven, o que se calle ante los que sí.

## Códigos de salida

| Código | Significado | Qué hacer |
|---|---|---|
| 0 | Corrida normal | Nada |
| 1 | El parser no lee (desde `diagnose`) | Ver abajo |
| 2 | El sitio cambió | Ver abajo |
| 3 | Error de red o bloqueo | Si es 403 o 429 sostenido, subir el intervalo |

## Cuando el sitio cambia (código 2)

```bash
python3 monitor.py diagnose
```

Deja `diagnostico.html` con la respuesta cruda. El parser vive en la sección
marcada `# parser` de `monitor.py` y se apoya en dos supuestos:

- El calendario es una `<table>` con `<thead>` de fechas `MM/DD` y `<tbody>`
  con filas por hora.
- Cada celda con cita trae el texto `残<i>N件`, donde N son los cupos libres.

Si cambió el formato, ajustar las constantes `RE_*`. Después, **siempre**:

```bash
python3 test_monitor.py && python3 test_avisos.py
```

Las pruebas corren contra `fixture_real.html`, capturado del endpoint real.
Si el HTML nuevo es distinto, actualizar el fixture con el contenido de
`diagnostico.html` y ajustar los conteos esperados en `test_monitor.py`.

## Por qué el parser lee el número y no la clase CSS

Las celdas ocupadas vienen como `c_cal_time_cell--disabled`. Sería más directo
buscar la ausencia de esa clase, pero si el sitio la renombra, un parser basado
en clases interpreta "no hay celdas ocupadas" como "no hay nada" y se queda
callado para siempre sin que nadie lo note. Leer `残<i>N件` falla ruidosamente:
si no encuentra ninguna celda, levanta `SitioCambio`.

Esa distinción importa más que la elegancia del código. Un monitor que falla en
silencio es peor que no tener monitor, porque genera confianza falsa.

## Contexto del trámite que afecta el diseño

- Solo se puede agendar dentro de los 3 meses previos al viaje. Vigilar semanas
  fuera de esa ventana no sirve.
- Se cancela hasta las 5:00 p.m. del día anterior, y no se puede modificar una
  cita: solo cancelar y volver a agendar. Cada cambio de planes ajeno libera un
  cupo, y por eso las tardes entre semana concentran movimiento.
- El estudio toma 13 días hábiles, y no reciben solicitudes con menos de 13 días
  hábiles antes de la salida de Colombia. Si la fecha de viaje ya no da para
  eso, el monitor no resuelve nada y hay que decirlo en vez de seguir vigilando.
- Existe un canal aparte para agencias de viajes y tramitadores, con 30 cupos
  diarios. Es una cola distinta y para casos urgentes suele resolver antes.

## Archivos

```
monitor.py           script único, solo stdlib
test_monitor.py      pruebas del parser
test_avisos.py       pruebas de redacción y del estado de recordatorios
fixture_real.html    HTML real del endpoint, base de las pruebas
estado.json          generado, lo ya visto (no versionar contenido sensible)
com.vectiva.visa-monitor.plist   agente de launchd para macOS
```
