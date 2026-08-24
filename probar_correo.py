#!/usr/bin/env python3
"""
Verifica que el envío por SMTP funciona, sin tocar el calendario ni el estado.

Manda un correo con franjas inventadas y el rótulo PRUEBA bien visible, para
confirmar que SMTP_PASS sirve y que las direcciones de EMAIL_PARA están bien
escritas. No consulta el sitio de la embajada ni escribe estado.json.

Se dispara a mano desde la pestaña Actions. No corre en el cron.
"""
import logging
import sys

from monitor import Franja, redactar_texto, redactar_html, enviar_correo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")

AVISO = ("=== ESTO ES UNA PRUEBA. NO HAY CUPO. ===\n"
         "Se envía para confirmar que las alertas llegan bien.\n"
         "Las franjas de abajo son inventadas. No intentes agendarlas.\n")

# Franjas inventadas: un día con tres horas y otro con una, para ver la
# agrupación y el bloque de recordatorios tal como se verán de verdad.
nuevas = [
    Franja("2026/09/14", "09:00", 3),
    Franja("2026/09/14", "09:15", 4),
    Franja("2026/09/14", "14:30", 3),
]
recordatorio = [Franja("2026/09/16", "11:15", 3)]

texto = AVISO + "\n" + redactar_texto(nuevas, recordatorio)
html = redactar_html(nuevas, recordatorio).replace(
    "Hay cita disponible para la visa japonesa",
    "PRUEBA — así se verá el aviso real",
).replace(
    "franjas libres\n      en este momento",
    "franjas de ejemplo. ESTO ES UNA PRUEBA: no hay cupo y estas citas no existen",
)

ok = enviar_correo("[PRUEBA] Monitor de visa japonesa — verificación de envío",
                   texto, html)

if not ok:
    logging.error("el envío falló. Revisa SMTP_PASS, SMTP_USER y EMAIL_PARA.")
    sys.exit(1)

logging.info("prueba enviada. Revisa las bandejas de entrada (y spam).")
