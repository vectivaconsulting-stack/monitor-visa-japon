#!/usr/bin/env python3
"""
Aviso de vida diario.

El monitor solo escribe cuando hay cupo, así que su silencio es ambiguo: puede
ser que no haya nada, o que lleve días muerto. Este script mira el historial de
corridas y manda un correo una vez al día diciendo si sigue vivo.

La regla es al revés que la del monitor: si este correo NO llega, hay que
mirar. Un fallo silencioso es peor que no tener monitor.

Solo librería estándar, como el resto del proyecto.
"""
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from monitor import enviar_correo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("latido")

REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
BOGOTA = timezone(timedelta(hours=-5))

# Con cron cada 10 min caben ~144 corridas al día. GitHub se salta algunas bajo
# carga, así que el umbral es holgado: por debajo de esto algo va mal de verdad.
MINIMO_DIARIO = 60
# Si la última corrida es más vieja que esto, el cron se detuvo.
MAX_SILENCIO_MIN = 45


def pedir_corridas() -> list[dict]:
    url = (f"https://api.github.com/repos/{REPO}/actions/workflows/"
           f"monitor.yml/runs?per_page=100")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "monitor-visa-latido",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["workflow_runs"]


def main() -> int:
    try:
        corridas = pedir_corridas()
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
        log.error("no se pudo consultar la API de GitHub: %s", e)
        return 1

    ahora = datetime.now(timezone.utc)
    dia = ahora - timedelta(hours=24)

    ultimas = [c for c in corridas
               if datetime.fromisoformat(c["created_at"].replace("Z", "+00:00")) > dia]
    fallidas = [c for c in ultimas if c["conclusion"] not in ("success", None)]

    if not corridas:
        asunto = "[REVISAR] Monitor de visa: sin corridas registradas"
        cuerpo = ["El workflow no tiene ninguna corrida. Puede que GitHub lo haya",
                  "desactivado, o que nunca haya arrancado.", ""]
        estado_ok = False
    else:
        reciente = corridas[0]
        cuando = datetime.fromisoformat(reciente["created_at"].replace("Z", "+00:00"))
        minutos = int((ahora - cuando).total_seconds() // 60)
        local = cuando.astimezone(BOGOTA).strftime("%Y-%m-%d %H:%M")

        problemas = []
        if minutos > MAX_SILENCIO_MIN:
            problemas.append(f"La última corrida fue hace {minutos} minutos. "
                             f"El cron debería disparar cada 10.")
        if len(ultimas) < MINIMO_DIARIO:
            problemas.append(f"Solo {len(ultimas)} corridas en 24 horas. "
                             f"Se esperaban al menos {MINIMO_DIARIO}.")
        if fallidas:
            problemas.append(f"{len(fallidas)} corrida(s) fallaron en 24 horas. "
                             f"Revisa el log: código 2 es cambio del sitio, "
                             f"3 es red o bloqueo.")

        estado_ok = not problemas
        asunto = ("Monitor de visa: vivo" if estado_ok
                  else "[REVISAR] Monitor de visa: algo va mal")

        cuerpo = [
            f"Última corrida: {local} (hace {minutos} min), "
            f"resultado {reciente['conclusion'] or 'en curso'}.",
            f"Corridas en las últimas 24 horas: {len(ultimas)}.",
            "",
        ]
        if problemas:
            cuerpo.append("PROBLEMAS DETECTADOS")
            cuerpo += [f"  - {p}" for p in problemas]
        else:
            cuerpo.append("Todo normal. No hace falta hacer nada.")
        cuerpo.append("")

    cuerpo += [
        "Este correo llega una vez al día. Si algún día no llega, es señal de",
        "que el monitor dejó de correr y hay que mirarlo.",
        "",
        f"Historial: https://github.com/{REPO}/actions",
    ]
    texto = "\n".join(cuerpo)
    color = "#0a7f3f" if estado_ok else "#b3261e"
    html = (f'<html><body style="margin:0;padding:24px;background:#f6f4ef;'
            f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Helvetica,'
            f'Arial,sans-serif;"><div style="max-width:520px;margin:0 auto;'
            f'background:#fff;border-radius:8px;padding:28px;border:1px solid #e5e2db;">'
            f'<h1 style="margin:0 0 12px;font-size:18px;color:{color};">'
            f'{"El monitor sigue corriendo" if estado_ok else "Revisa el monitor"}</h1>'
            f'<pre style="margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,'
            f'monospace;font-size:13px;color:#333;white-space:pre-wrap;">{texto}</pre>'
            f'</div></body></html>')

    if not enviar_correo(asunto, texto, html):
        log.error("no se pudo enviar el latido")
        return 1

    log.info("latido enviado | %s", asunto)
    # El job no falla por un monitor enfermo: el correo ya lo dijo. Falla solo
    # si el propio latido no pudo avisar.
    return 0


if __name__ == "__main__":
    sys.exit(main())
