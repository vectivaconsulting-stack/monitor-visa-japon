#!/usr/bin/env python3
"""
Monitor de citas de visa de corta estadía.
Embajada del Japón en Colombia — embjpcol.rsvsys.jp

Lee el calendario, detecta cupos liberados y avisa. NO agenda:
la reserva la hace una persona.

Solo librería estándar. Python 3.9 o superior.

Subcomandos:
    check      una pasada y sale (para cron o launchd)
    watch      ciclo continuo con intervalo configurable
    diagnose   vuelca la respuesta cruda del servidor a disco

Canales de aviso (configurar al menos uno):

  Correo        SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS,
                EMAIL_PARA (uno o varios separados por coma), EMAIL_DE
  Telegram      TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

Variables de entorno:
    SEMANAS            semanas hacia adelante (por defecto 6)
    SOLICITUDES        personas que viajan (por defecto 1)
    ESTADO             ruta del archivo de estado
    CONTACTO           correo que se añade al User-Agent\n    RECORDAR           minutos para reinsistir con un cupo que sigue libre
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import logging
import os
import random
import re
import smtplib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from email.message import EmailMessage
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = "https://embjpcol.rsvsys.jp"
CALENDARIO = f"{BASE}/reservations/calendar"
AJAX = f"{BASE}/ajax/reservations/calendar"

log = logging.getLogger("monitor")


# --------------------------------------------------------------------- modelo

@dataclass(frozen=True)
class Franja:
    fecha: str   # 2026/09/14
    hora: str    # 09:00
    cupos: int

    @property
    def clave(self) -> str:
        return f"{self.fecha} {self.hora}"


class SitioCambio(Exception):
    """El HTML no tiene la forma esperada. Requiere revisión humana o del agente."""


# --------------------------------------------------------------------- parser
#
# El número de cupos se lee del texto `残<i>N件`, no de la clase CSS.
# Las celdas ocupadas vienen como c_cal_time_cell--disabled, pero si el
# sitio renombra esa clase un parser basado en clases se queda callado
# para siempre sin que nadie se entere. El número es más difícil de romper.

RE_THEAD = re.compile(r"<thead[\s\S]*?</thead>")
RE_TBODY = re.compile(r"<tbody[\s\S]*?</tbody>")
RE_TH_HEAD = re.compile(r"<th[^>]*>[\s\S]*?</th>")
RE_TR = re.compile(r"<tr>[\s\S]*?</tr>")
RE_TH_HORA = re.compile(r"<th>([\s\S]*?)</th>")
RE_TD = re.compile(r"<td[^>]*>[\s\S]*?</td>")
RE_TAGS = re.compile(r"<[^>]*>")
RE_FECHA = re.compile(r"(\d{2})/(\d{2})")
RE_CUPOS = re.compile(r"残\s*<i>\s*(\d+)\s*件")


def _fechas_encabezado(html: str) -> list[tuple[int, int] | None]:
    m = RE_THEAD.search(html)
    if not m:
        raise SitioCambio("No se encontró <thead> en el calendario")
    celdas = RE_TH_HEAD.findall(m.group(0))
    if len(celdas) < 2:
        raise SitioCambio(f"El encabezado trae {len(celdas)} celdas, se esperaban 8")
    salida = []
    for celda in celdas[1:]:  # la primera es la esquina vacía
        f = RE_FECHA.search(celda)
        salida.append((int(f.group(1)), int(f.group(2))) if f else None)
    return salida


def _filas(html: str) -> list[tuple[str, list[str]]]:
    m = RE_TBODY.search(html)
    if not m:
        raise SitioCambio("No se encontró <tbody> en el calendario")
    salida = []
    for fila in RE_TR.findall(m.group(0)):
        th = RE_TH_HORA.search(fila)
        hora = RE_TAGS.sub("", th.group(1)).strip() if th else ""
        salida.append((hora, RE_TD.findall(fila)))
    return salida


def parsear_calendario(html: str, anio_ancla: int, mes_ancla: int) -> list[Franja]:
    """
    Convierte el HTML de una semana en franjas.

    El calendario solo trae mes/día. `anio_ancla` y `mes_ancla` son los de la
    fecha que se pidió, y sirven para resolver el cambio de año en la semana
    que cruza diciembre a enero.
    """
    fechas = _fechas_encabezado(html)
    franjas: list[Franja] = []

    for hora, celdas in _filas(html):
        if not hora:
            continue  # fila separadora entre la jornada de mañana y la de tarde
        for i, celda in enumerate(celdas):
            if i >= len(fechas) or fechas[i] is None:
                continue
            m = RE_CUPOS.search(celda)
            if not m:
                continue  # día cerrado: celda vacía
            mes, dia = fechas[i]
            anio = anio_ancla + 1 if (mes_ancla == 12 and mes == 1) else anio_ancla
            franjas.append(Franja(f"{anio}/{mes:02d}/{dia:02d}", hora, int(m.group(1))))

    if not franjas:
        # Cero franjas no es lo mismo que cero cupos. Si no se leyó ni una
        # celda, es señal de que el HTML cambió, no de que no haya citas.
        raise SitioCambio("Se parsearon 0 franjas. Revisar con: monitor.py diagnose")
    return franjas


# ------------------------------------------------------------------- clientela

class Cliente:
    """
    Sesión contra el sistema de reservas.

    El backend es CakePHP con protección CSRF y hash anti-manipulación de
    campos. Ambos tokens salen de un GET fresco: no se pueden reutilizar
    entre corridas ni fabricar.
    """

    def __init__(self, contacto: str = "", timeout: int = 30):
        self.timeout = timeout
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self.ua = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        )
        if contacto:
            self.ua += f" monitor-personal ({contacto})"
        self.csrf = self.campos = self.libres = None
        self.event = "9"
        self.plan = "8"

    def _input(self, html: str, nombre: str) -> str | None:
        patron = re.escape(nombre)
        m = re.search(
            rf'<input[^>]*name="{patron}"[^>]*value="([^"]*)"', html
        )
        return m.group(1) if m else None

    def abrir(self) -> None:
        req = urllib.request.Request(
            CALENDARIO,
            headers={"User-Agent": self.ua, "Accept-Language": "es-CO,es;q=0.9"},
        )
        with self.opener.open(req, timeout=self.timeout) as r:
            html = r.read().decode("utf-8", errors="replace")

        self.csrf = self._input(html, "_csrfToken")
        self.campos = self._input(html, "_Token[fields]")
        self.libres = self._input(html, "_Token[unlocked]") or ""
        self.event = self._input(html, "event") or "9"
        self.plan = self._input(html, "plan") or "8"

        if not self.csrf or not self.campos:
            raise SitioCambio(
                "No se leyeron los tokens del formulario. El sitio cambió."
            )
        log.debug("sesión abierta, event=%s plan=%s", self.event, self.plan)

    def semana_cruda(self, fecha: date, solicitudes: int) -> str:
        """Devuelve el HTML crudo de la semana que contiene a `fecha`."""
        if not self.csrf:
            raise RuntimeError("Llamar abrir() antes de pedir semanas")

        cuerpo = urllib.parse.urlencode({
            "_method": "POST",
            "_csrfToken": self.csrf,
            "event": self.event,
            "plan": self.plan,
            "stock": str(solicitudes),
            "date": fecha.strftime("%Y/%m/%d"),
            "disp_type": "week",
            "_Token[fields]": self.campos,
            "_Token[unlocked]": self.libres,
        }).encode()

        req = urllib.request.Request(AJAX, data=cuerpo, headers={
            "User-Agent": self.ua,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": self.csrf,
            "Origin": BASE,
            "Referer": CALENDARIO,
        })

        with self.opener.open(req, timeout=self.timeout) as r:
            payload = json.loads(r.read().decode("utf-8", errors="replace"))

        if "html" not in payload:
            raise SitioCambio(
                f"Respuesta sin llave 'html'. Llaves: {list(payload)}"
            )
        return payload["html"]

    def semana(self, fecha: date, solicitudes: int) -> list[Franja]:
        return parsear_calendario(
            self.semana_cruda(fecha, solicitudes), fecha.year, fecha.month
        )


# ---------------------------------------------------------------- notificación

def _fmt_dia(fecha: str) -> str:
    """2026/09/14 -> 'lunes 14 de septiembre'"""
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    d = datetime.strptime(fecha, "%Y/%m/%d").date()
    return f"{dias[d.weekday()]} {d.day} de {meses[d.month - 1]}"


def agrupar(franjas: list[Franja]) -> list[tuple[str, list[Franja]]]:
    """Agrupa por día, en orden. Un día con cinco franjas se lee mucho mejor así."""
    por_dia: dict[str, list[Franja]] = {}
    for f in sorted(franjas, key=lambda x: (x.fecha, x.hora)):
        por_dia.setdefault(f.fecha, []).append(f)
    return list(por_dia.items())


def redactar_texto(nuevas: list[Franja], recordatorio: list[Franja]) -> str:
    partes = []
    if nuevas:
        partes.append("CUPOS NUEVOS")
        for fecha, fs in agrupar(nuevas):
            horas = ", ".join(f"{f.hora} ({f.cupos})" for f in fs)
            partes.append(f"  {_fmt_dia(fecha)}: {horas}")
    if recordatorio:
        partes.append("\nSIGUEN DISPONIBLES")
        for fecha, fs in agrupar(recordatorio):
            horas = ", ".join(f"{f.hora} ({f.cupos})" for f in fs)
            partes.append(f"  {_fmt_dia(fecha)}: {horas}")
    partes.append(f"\nAgendar: {CALENDARIO}")
    partes.append("Las citas se cancelan hasta las 5:00 p.m. del día anterior, "
                  "así que los cupos se van rápido.")
    return "\n".join(partes)


def redactar_html(nuevas: list[Franja], recordatorio: list[Franja]) -> str:
    def bloque(titulo: str, fs: list[Franja], color: str) -> str:
        if not fs:
            return ""
        filas = ""
        for fecha, grupo in agrupar(fs):
            horas = "".join(
                f'<span style="display:inline-block;background:#fff;'
                f'border:1px solid #d9d9d9;border-radius:4px;padding:4px 10px;'
                f'margin:2px 4px 2px 0;font-family:monospace;font-size:14px;">'
                f'{f.hora}<span style="color:#888;font-size:12px;"> · '
                f'{f.cupos}</span></span>'
                for f in grupo
            )
            filas += (
                f'<tr><td style="padding:10px 0;border-bottom:1px solid #eee;">'
                f'<div style="font-size:15px;font-weight:600;color:#222;'
                f'margin-bottom:6px;">{_fmt_dia(fecha)}</div>{horas}</td></tr>'
            )
        return (
            f'<p style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;'
            f'color:{color};font-weight:700;margin:24px 0 4px;">{titulo}</p>'
            f'<table style="width:100%;border-collapse:collapse;">{filas}</table>'
        )

    total = len(nuevas) + len(recordatorio)
    return f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f6f4ef;
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:8px;
  padding:28px;border:1px solid #e5e2db;">
    <h1 style="margin:0 0 4px;font-size:20px;color:#111;">
      Hay cita disponible para la visa japonesa</h1>
    <p style="margin:0;color:#666;font-size:14px;">
      {total} franja{'s' if total != 1 else ''} libre{'s' if total != 1 else ''}
      en este momento</p>
    {bloque('Cupos nuevos', nuevas, '#0052FF')}
    {bloque('Siguen disponibles', recordatorio, '#888')}
    <a href="{CALENDARIO}" style="display:block;margin:28px 0 0;padding:14px;
    background:#0052FF;color:#fff;text-align:center;text-decoration:none;
    border-radius:6px;font-weight:600;font-size:15px;">Agendar ahora</a>
    <p style="margin:16px 0 0;color:#888;font-size:12px;line-height:1.5;">
      Las citas se cancelan hasta las 5:00 p.m. del día anterior, así que estos
      cupos se van rápido. El número entre paréntesis es cuántas solicitudes
      caben en esa franja.</p>
  </div>
</body></html>"""


def enviar_correo(asunto: str, texto: str, html: str) -> bool:
    """
    Envía por SMTP. Las credenciales salen del entorno, nunca del código.
    Con Gmail hay que usar una contraseña de aplicación, no la del correo.
    """
    servidor = os.environ.get("SMTP_HOST")
    usuario = os.environ.get("SMTP_USER")
    clave = os.environ.get("SMTP_PASS")
    destinos = [d.strip() for d in os.environ.get("EMAIL_PARA", "").split(",") if d.strip()]

    if not (servidor and usuario and clave and destinos):
        return False

    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = os.environ.get("EMAIL_DE", usuario)
    msg["To"] = ", ".join(destinos)
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")

    puerto = int(os.environ.get("SMTP_PORT", 587))
    try:
        if puerto == 465:
            with smtplib.SMTP_SSL(servidor, puerto, timeout=30) as s:
                s.login(usuario, clave)
                s.send_message(msg)
        else:
            with smtplib.SMTP(servidor, puerto, timeout=30) as s:
                s.starttls()
                s.login(usuario, clave)
                s.send_message(msg)
        log.info("correo enviado a %s", ", ".join(destinos))
        return True
    except (smtplib.SMTPException, OSError) as e:
        log.error("el correo falló: %s", e)
        return False


def enviar_telegram(texto: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    cuerpo = json.dumps({"chat_id": chat, "text": texto}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=cuerpo, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
        log.info("telegram enviado")
        return True
    except urllib.error.URLError as e:
        log.error("telegram falló: %s", e)
        return False


def avisar(nuevas: list[Franja], recordatorio: list[Franja]) -> None:
    """Manda por todos los canales configurados. Uno solo ya sirve."""
    total = len(nuevas) + len(recordatorio)
    asunto = (f"Cita de visa japonesa disponible"
              f"{f' ({total} franjas)' if total > 1 else ''}")
    texto = redactar_texto(nuevas, recordatorio)

    enviados = [
        enviar_correo(asunto, texto, redactar_html(nuevas, recordatorio)),
        enviar_telegram(f"{asunto}\n\n{texto}"),
    ]
    if not any(enviados):
        log.warning("NINGÚN canal configurado. Aviso solo por consola:\n%s", texto)


# --------------------------------------------------------------------- estado
#
# El estado guarda, por franja, cuándo se avisó por última vez. Eso permite
# dos cosas: no repetir el aviso en cada pasada, y volver a insistir si la
# franja sigue libre pasado un rato. Cuando una franja desaparece se borra del
# estado, así que si reaparece vuelve a contar como nueva.

def cargar_estado(ruta: Path) -> dict[str, str]:
    try:
        datos = json.loads(ruta.read_text())
        return datos.get("avisadas", {})
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return {}


def guardar_estado(ruta: Path, avisadas: dict[str, str]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps({
        "actualizado": datetime.now().isoformat(timespec="seconds"),
        "avisadas": avisadas,
    }, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------- pasada

def lunes_de(d: date) -> date:
    return d - timedelta(days=d.weekday())


def una_pasada(semanas: int, solicitudes: int, ruta_estado: Path,
               recordar_min: int) -> int:
    """
    Una revisión completa de la ventana.
    Devuelve cuántas franjas se reportaron (nuevas más recordatorios).
    """
    cliente = Cliente(contacto=os.environ.get("CONTACTO", ""))
    cliente.abrir()

    todas: list[Franja] = []
    inicio = lunes_de(date.today())
    for i in range(semanas):
        todas += cliente.semana(inicio + timedelta(weeks=i), solicitudes)
        if i < semanas - 1:
            # Espaciado deliberado. Es el sitio de una embajada con capacidad
            # limitada, no un API público.
            time.sleep(random.uniform(1.5, 3.0))

    # Todas las franjas que sirven, no solo la primera del día ni de la semana.
    libres = [f for f in todas if f.cupos >= solicitudes]
    avisadas = cargar_estado(ruta_estado)
    ahora = datetime.now()

    nuevas: list[Franja] = []
    recordatorio: list[Franja] = []
    for f in sorted(libres, key=lambda x: (x.fecha, x.hora)):
        marca = avisadas.get(f.clave)
        if marca is None:
            nuevas.append(f)
        elif recordar_min > 0:
            transcurrido = (ahora - datetime.fromisoformat(marca)).total_seconds() / 60
            if transcurrido >= recordar_min:
                recordatorio.append(f)

    log.info(
        "%d franjas revisadas | %d con cupo | %d nuevas | %d recordadas",
        len(todas), len(libres), len(nuevas), len(recordatorio),
    )

    if nuevas or recordatorio:
        avisar(nuevas, recordatorio)

    # Solo se conservan las franjas que siguen libres. Si una desaparece y
    # más tarde vuelve, se vuelve a tratar como nueva.
    sigue = {f.clave for f in libres}
    reportadas = {f.clave for f in nuevas + recordatorio}
    guardar_estado(ruta_estado, {
        k: (ahora.isoformat(timespec="seconds") if k in reportadas else v)
        for k, v in {**{f.clave: ahora.isoformat(timespec="seconds")
                        for f in nuevas},
                     **avisadas}.items()
        if k in sigue
    })
    return len(nuevas) + len(recordatorio)


# ------------------------------------------------------------------------ cli

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("comando", choices=["check", "watch", "diagnose"])
    p.add_argument("--semanas", type=int,
                   default=int(os.environ.get("SEMANAS", 6)))
    p.add_argument("--solicitudes", type=int,
                   default=int(os.environ.get("SOLICITUDES", 1)))
    p.add_argument("--intervalo", type=int, default=600,
                   help="segundos entre pasadas en modo watch")
    p.add_argument("--recordar", type=int,
                   default=int(os.environ.get("RECORDAR", 45)),
                   help="minutos antes de reinsistir con una franja que sigue "
                        "libre. 0 desactiva los recordatorios.")
    p.add_argument("--estado", type=Path,
                   default=Path(os.environ.get("ESTADO", "estado.json")))
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        if args.comando == "check":
            una_pasada(args.semanas, args.solicitudes, args.estado, args.recordar)

        elif args.comando == "watch":
            log.info("vigilando cada ~%d s. Ctrl+C para salir.", args.intervalo)
            while True:
                try:
                    una_pasada(args.semanas, args.solicitudes, args.estado, args.recordar)
                except SitioCambio:
                    raise
                except Exception as e:
                    log.error("pasada falló, se reintenta: %s", e)
                # Jitter para no golpear siempre en el mismo segundo del reloj
                time.sleep(args.intervalo + random.uniform(-30, 30))

        elif args.comando == "diagnose":
            cliente = Cliente(contacto=os.environ.get("CONTACTO", ""))
            cliente.abrir()
            crudo = cliente.semana_cruda(lunes_de(date.today()), args.solicitudes)
            destino = Path("diagnostico.html")
            destino.write_text(crudo, encoding="utf-8")
            print(f"HTML crudo guardado en {destino} ({len(crudo)} bytes)")
            print(f"csrf leído  : {'sí' if cliente.csrf else 'NO'}")
            print(f"campos leído: {'sí' if cliente.campos else 'NO'}")
            print(f"event={cliente.event}  plan={cliente.plan}")
            try:
                franjas = parsear_calendario(
                    crudo, date.today().year, date.today().month
                )
                print(f"franjas parseadas: {len(franjas)}")
                for f in franjas[:5]:
                    print("  ", asdict(f))
            except SitioCambio as e:
                print(f"EL PARSER NO LEE: {e}")
                return 1

    except SitioCambio as e:
        log.error("EL SITIO CAMBIÓ: %s", e)
        log.error("Correr `python3 monitor.py diagnose` y revisar el HTML.")
        return 2
    except urllib.error.HTTPError as e:
        # 403 sostenido suele ser bloqueo por frecuencia. Bajar el ritmo.
        log.error("El servidor respondió %s %s", e.code, e.reason)
        if e.code in (403, 429):
            log.error("Posible bloqueo por frecuencia. Subir --intervalo.")
        return 3
    except urllib.error.URLError as e:
        log.error("Sin conexión al servidor: %s", e.reason)
        return 3
    except TimeoutError:
        log.error("El servidor no respondió a tiempo")
        return 3
    except KeyboardInterrupt:
        log.info("detenido")
    return 0


if __name__ == "__main__":
    sys.exit(main())
