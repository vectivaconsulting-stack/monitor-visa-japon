#!/usr/bin/env python3
"""
Pruebas del parser contra el HTML real del endpoint.
Correr con: python3 test_monitor.py
"""

import sys
from pathlib import Path

from monitor import Franja, SitioCambio, parsear_calendario

REAL = Path(__file__).parent / "fixture_real.html"
CELDA_CERO = ('<p class="c_cal_time_cell c_cal_time_cell--disabled">'
              '<span>残<i>0件／ Solicitud(es)</i></span></p>')
CELDA_CUPO = ('<a href="#" class="c_cal_time_cell js_reserve">'
              '<span>残<i>2件／ Solicitud(es)</i></span></a>')

fallos = 0


def check(nombre, cond, detalle=""):
    global fallos
    marca = "OK   " if cond else "FALLA"
    print(f"{marca}  {nombre}" + (f"  -> {detalle}" if detalle else ""))
    if not cond:
        fallos += 1


html = REAL.read_text(encoding="utf-8")

# --- HTML real, todo en cero ---
f = parsear_calendario(html, 2026, 9)
check("parsea el HTML real", len(f) == 25, f"{len(f)} franjas")
check("5 horas distintas", len({x.hora for x in f}) == 5,
      ", ".join(sorted({x.hora for x in f})))
check("ignora sábado y domingo",
      not any(x.fecha.endswith(("09/12", "09/13")) for x in f))
check("salta la fila separadora", not any(x.hora == "" for x in f))
check("arma la fecha completa", f[0].fecha == "2026/09/14", f[0].fecha)
check("cero cupos en el real", sum(x.cupos for x in f) == 0)

# --- Con cupo, y con otra clase CSS ---
con_cupo = html.replace(CELDA_CERO, CELDA_CUPO, 3)
g = parsear_calendario(con_cupo, 2026, 9)
libres = [x for x in g if x.cupos > 0]
check("detecta cupo pese al cambio de clase CSS", len(libres) == 3,
      f"{len(libres)} libres")
check("lee bien la cantidad", all(x.cupos == 2 for x in libres))
check("no altera el total de franjas", len(g) == len(f))

# --- Cambio de año en la semana que cruza diciembre ---
dic = html.replace("09/12", "12/28").replace("09/13", "12/29") \
          .replace("09/14", "12/30").replace("09/15", "12/31") \
          .replace("09/16", "01/01").replace("09/17", "01/02") \
          .replace("09/18", "01/03")
h = parsear_calendario(dic, 2026, 12)
anios = {x.fecha[:4] for x in h}
check("resuelve el salto de año", anios == {"2026", "2027"}, ", ".join(sorted(anios)))

# --- Detección de cambios en el sitio ---
try:
    parsear_calendario("<div>otra cosa</div>", 2026, 9)
    check("levanta SitioCambio sin thead", False)
except SitioCambio:
    check("levanta SitioCambio sin thead", True)

try:
    parsear_calendario(html.replace("残", "X"), 2026, 9)
    check("levanta SitioCambio si no lee ninguna celda", False)
except SitioCambio:
    check("levanta SitioCambio si no lee ninguna celda", True)

# --- Clave de deduplicación ---
check("clave estable", Franja("2026/09/14", "09:00", 1).clave == "2026/09/14 09:00")

print("\nTodas las pruebas pasaron." if fallos == 0 else f"\n{fallos} fallo(s).")
sys.exit(0 if fallos == 0 else 1)
