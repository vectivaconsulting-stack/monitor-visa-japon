#!/usr/bin/env python3
"""Pruebas de agrupación, redacción y ciclo de vida del estado."""
import json, sys, tempfile
from datetime import datetime, timedelta
from pathlib import Path
from monitor import (Franja, agrupar, redactar_texto, redactar_html,
                     cargar_estado, guardar_estado, _fmt_dia)

fallos = 0
def check(n, c, d=""):
    global fallos
    print(f"{'OK   ' if c else 'FALLA'}  {n}" + (f"  -> {d}" if d else ""))
    if not c: fallos += 1

# --- agrupación: varias franjas el mismo día ---
fs = [Franja("2026/09/16","14:30",1), Franja("2026/09/14","09:00",2),
      Franja("2026/09/14","09:15",1), Franja("2026/09/14","13:30",1)]
g = agrupar(fs)
check("agrupa por día", len(g) == 2, f"{len(g)} días")
check("el día con 3 franjas conserva las 3", len(g[0][1]) == 3)
check("días en orden", g[0][0] == "2026/09/14" and g[1][0] == "2026/09/16")
check("horas ordenadas dentro del día",
      [f.hora for f in g[0][1]] == ["09:00","09:15","13:30"])

check("fecha legible en español", _fmt_dia("2026/09/14") == "lunes 14 de septiembre",
      _fmt_dia("2026/09/14"))

# --- redacción: ninguna franja se pierde ---
txt = redactar_texto(fs, [])
for f in fs:
    if f.hora not in txt: check(f"texto incluye {f.hora}", False); break
else: check("el texto lista TODAS las franjas", True, f"{len(fs)} franjas")

html = redactar_html(fs[:2], fs[2:])
check("html trae ambos bloques",
      "Cupos nuevos" in html and "Siguen disponibles" in html)
check("html lista todas las horas", all(f.hora in html for f in fs))
check("html sin placeholders sin resolver", "{" not in html.split("<body")[1])

# --- estado: ida y vuelta ---
with tempfile.TemporaryDirectory() as d:
    ruta = Path(d)/"estado.json"
    check("estado inexistente devuelve vacío", cargar_estado(ruta) == {})
    ahora = datetime.now().isoformat(timespec="seconds")
    guardar_estado(ruta, {"2026/09/14 09:00": ahora})
    check("estado persiste", cargar_estado(ruta) == {"2026/09/14 09:00": ahora})
    ruta.write_text("no es json")
    check("estado corrupto no revienta", cargar_estado(ruta) == {})

# --- reglas de recordatorio (misma lógica que una_pasada) ---
def clasificar(libres, avisadas, recordar_min, ahora):
    nuevas, recordar = [], []
    for f in libres:
        m = avisadas.get(f.clave)
        if m is None: nuevas.append(f)
        elif recordar_min > 0 and (ahora - datetime.fromisoformat(m)).total_seconds()/60 >= recordar_min:
            recordar.append(f)
    return nuevas, recordar

ahora = datetime.now()
a = Franja("2026/09/14","09:00",1)
hace5  = (ahora - timedelta(minutes=5)).isoformat(timespec="seconds")
hace60 = (ahora - timedelta(minutes=60)).isoformat(timespec="seconds")

n,r = clasificar([a], {}, 45, ahora)
check("franja nunca vista es nueva", len(n)==1 and len(r)==0)
n,r = clasificar([a], {a.clave: hace5}, 45, ahora)
check("avisada hace 5 min no repite", len(n)==0 and len(r)==0)
n,r = clasificar([a], {a.clave: hace60}, 45, ahora)
check("avisada hace 60 min sí recuerda", len(n)==0 and len(r)==1)
n,r = clasificar([a], {a.clave: hace60}, 0, ahora)
check("--recordar 0 desactiva recordatorios", len(n)==0 and len(r)==0)

# --- una franja que desaparece y reaparece vuelve a ser nueva ---
libres2 = []
sigue = {f.clave for f in libres2}
podado = {k:v for k,v in {a.clave: hace60}.items() if k in sigue}
check("franja que desaparece se borra del estado", podado == {})
n,r = clasificar([a], podado, 45, ahora)
check("si reaparece cuenta como nueva otra vez", len(n)==1)

print("\nTodas las pruebas pasaron." if fallos==0 else f"\n{fallos} fallo(s).")
sys.exit(0 if fallos==0 else 1)
