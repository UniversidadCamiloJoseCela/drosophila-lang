#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
envivo.py — El cerebro de la mosca en tiempo real.

Arranca un pequeño servidor local y abre el navegador. Desde la página puedes
escribir un programa Drosophila-Lang, darle una entrada y ejecutarlo: la
simulación corre en tu ordenador mientras miras, y cada paso se dibuja en 3D
sobre las 138 639 neuronas del cerebro real (FlyWire v783) en el momento en
que ocurre. La velocidad se puede cambiar, pausar o detener sobre la marcha.

Uso:
    python envivo.py                 # abre http://127.0.0.1:8765
    python envivo.py --puerto 9000
    python envivo.py --no-abrir
    python envivo.py --cerebro sintetico

Para salir: Ctrl + C en la Terminal.
"""
from __future__ import annotations

import argparse
import base64
import codecs
import json
import queue
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import numpy as np

import drosophila as d

THREE_URL = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"
SIN_LIMITE = 100_000          # pasos por segundo que significan «lo más rápido posible»

EJEMPLOS_INTEGRADOS = {
    "efe.dros": ";; la primera palabra de la mosca\n~~~> (DA1)\n(DA1) !'F' x_x\n",
    "hola.dros": ";; saludo completo\n~~~> (DM1)\n(DM1) !\"¡Hola, Mundo!\\n\" ~> (DA1)\n(DA1) x_x\n",
    "suma.dros": (";; la mosca que aprende a contar\n$ alfa = 0\n$ beta = 0\n~~~> (DM1)\n"
                  "(DM1) <alfa <beta ~> (VA2)\n(VA2) ?beta ~> (VA3) | (DL5)\n(VA3) +alfa ~> (VA2)\n"
                  "(DL5) !\"suma: \" ~> (DA1)\n(DA1) ?alfa ~> (DA2) | (VM7d)\n(DA2) !* ~> (DA1)\n"
                  "(VM7d) !'\\n' x_x\n"),
}


class Detenida(Exception):
    """Señal interna para interrumpir una simulación en curso."""


def _b64(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode("ascii")


class Simulacion(threading.Thread):
    """Una vida de la mosca, ejecutada paso a paso en segundo plano."""

    _contador = 0

    def __init__(self, cerebro: d.Cerebro, codigo: str, olor: List[int], pps: float,
                 detectar: bool, max_pasos: int) -> None:
        super().__init__(daemon=True)
        Simulacion._contador += 1
        self.id = Simulacion._contador
        self.vm = d.DrosophilaVM(codigo, cerebro=cerebro, flujo_info=None)  # puede lanzar DrosophilaError
        self.olor, self.detectar, self.max_pasos = olor, detectar, max_pasos
        self.pps = pps
        self.cola: "queue.Queue[Dict]" = queue.Queue(maxsize=4000)
        self.detener = threading.Event()
        self._ref: Optional[tuple] = None
        self._natural = ~self.vm.implante.es_implante
        self._decodificador = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._pendiente = bytearray()

    # -- datos para el navegador al empezar -----------------------------------
    def descripcion(self) -> Dict:
        P, C = self.vm.implante, self.vm.cerebro
        return {
            "id": self.id,
            "reclutadas": [{"i": int(i), "papel": P.papel[int(i)], "tipo": C.tipo_visible(int(i))}
                           for i in P.reclutadas],
            "engramas": P.engramas,
            "pesos0": [float(P.peso[e]) for e in P.arista_engrama],
        }

    # -- ritmo ------------------------------------------------------------------
    def _esperar(self, t: int) -> None:
        while True:
            if self.detener.is_set():
                raise Detenida()
            pps = self.pps
            if pps <= 0:                          # en pausa
                self._ref = None
                time.sleep(0.05)
                continue
            if pps >= SIN_LIMITE:
                return
            ahora = time.monotonic()
            if self._ref is None or self._ref[2] != pps:
                self._ref = (t, ahora, pps)
                return
            objetivo = self._ref[1] + (t - self._ref[0]) / pps
            falta = objetivo - ahora
            if falta <= 0:
                return
            time.sleep(min(falta, 0.05))

    def _poner(self, evento: Dict) -> None:
        while True:
            if self.detener.is_set():
                raise Detenida()
            try:
                self.cola.put(evento, timeout=0.2)
                return
            except queue.Full:
                continue

    # -- ejecución --------------------------------------------------------------
    def run(self) -> None:
        def al_emitir(b: bytes) -> None:
            self._pendiente.extend(b)

        def observar(t: int, S: np.ndarray, pesos: np.ndarray) -> None:
            self._esperar(t)
            activas = np.flatnonzero(S).astype("<i4")
            texto = self._decodificador.decode(bytes(self._pendiente))
            self._pendiente.clear()
            self._poner({"tipo": "paso", "t": t, "idx": _b64(activas),
                         "cuenta": int(self._natural[activas].sum()),
                         "salida": texto, "pesos": [float(x) for x in pesos]})

        try:
            res = self.vm.ejecutar(self.olor, max_pasos=self.max_pasos,
                                   detectar_atractores=self.detectar,
                                   flujo_salida=al_emitir, observador=observar)
            cola = self._decodificador.decode(bytes(self._pendiente), final=True)
            causas = {"muerte": "La Fibra Gigante disparó: la mosca ha muerto.",
                      "coma": "El programa cayó en silencio.",
                      "ciclo": "El programa entró en un bucle infinito y se detuvo.",
                      "agotamiento": "Se alcanzó el máximo de pasos."}
            self._poner({"tipo": "fin", "causa": causas[res.causa], "pasos": res.pasos,
                         "salida": cola, "despertadas": res.neuronas_despertadas})
        except Detenida:
            try:
                self.cola.put_nowait({"tipo": "fin", "causa": "Ejecución detenida.", "salida": ""})
            except queue.Full:
                pass
        except Exception as e:                      # nunca dejar al navegador esperando
            try:
                self.cola.put_nowait({"tipo": "fin", "causa": f"Error durante la ejecución: {e}",
                                      "salida": ""})
            except queue.Full:
                pass


class Estado:
    """Estado compartido del servidor: el cerebro y la simulación actual."""

    def __init__(self, cerebro: d.Cerebro, html: bytes) -> None:
        self.cerebro, self.html = cerebro, html
        self.sim: Optional[Simulacion] = None
        self.cerrojo = threading.Lock()


def crear_manejador(estado: Estado):
    class Manejador(BaseHTTPRequestHandler):
        server_version = "Drosophila/2.2"

        def log_message(self, *args) -> None:        # silencio en la Terminal
            pass

        def _json(self, codigo: int, datos: Dict) -> None:
            cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def _leer(self) -> Dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(estado.html)))
                self.end_headers()
                self.wfile.write(estado.html)
            elif url.path == "/flujo":
                self._flujo(parse_qs(url.query).get("id", ["0"])[0])
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            url = urlparse(self.path)
            datos = self._leer()
            if url.path == "/ejecutar":
                self._ejecutar(datos)
            elif url.path == "/velocidad":
                with estado.cerrojo:
                    if estado.sim is not None:
                        estado.sim.pps = float(datos.get("pps", 10))
                self._json(200, {"ok": True})
            elif url.path == "/detener":
                with estado.cerrojo:
                    if estado.sim is not None:
                        estado.sim.detener.set()
                self._json(200, {"ok": True})
            else:
                self.send_error(404)

        def _ejecutar(self, datos: Dict) -> None:
            codigo = str(datos.get("codigo", ""))
            try:
                olor = d._leer_olor(str(datos.get("olor", "")), None)
                sim = Simulacion(estado.cerebro, codigo, olor, float(datos.get("pps", 10)),
                                 not bool(datos.get("sin_bucles", False)),
                                 int(datos.get("max_pasos", 100_000)))
            except d.DrosophilaError as e:
                self._json(200, {"ok": False, "error": str(e)})
                return
            with estado.cerrojo:
                if estado.sim is not None:
                    estado.sim.detener.set()
                estado.sim = sim
            sim.start()
            self._json(200, {"ok": True, **sim.descripcion()})

        def _flujo(self, id_txt: str) -> None:
            with estado.cerrojo:
                sim = estado.sim
            if sim is None or str(sim.id) != id_txt:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                while True:
                    try:
                        primero = sim.cola.get(timeout=1.0)
                    except queue.Empty:
                        self.wfile.write(b": latido\n\n")
                        self.wfile.flush()
                        if sim.detener.is_set() and not sim.is_alive():
                            return
                        continue
                    lote = [primero]
                    while len(lote) < 200:
                        try:
                            lote.append(sim.cola.get_nowait())
                        except queue.Empty:
                            break
                    pasos = [e for e in lote if e["tipo"] == "paso"]
                    fin = next((e for e in lote if e["tipo"] == "fin"), None)
                    if pasos:
                        self.wfile.write(b"event: pasos\ndata: " +
                                         json.dumps(pasos, ensure_ascii=False).encode("utf-8") + b"\n\n")
                    if fin is not None:
                        self.wfile.write(b"event: fin\ndata: " +
                                         json.dumps(fin, ensure_ascii=False).encode("utf-8") + b"\n\n")
                        self.wfile.flush()
                        return
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                sim.detener.set()                   # el navegador se cerró

    return Manejador


def construir_html(cerebro: d.Cerebro, pos: np.ndarray, ejemplos: Dict[str, str]) -> bytes:
    lo, hi = pos.min(axis=0), pos.max(axis=0)
    span = np.where(hi - lo > 0, hi - lo, 1.0)
    q = np.round((pos - lo) / span * 65535).astype("<u2")
    nombres = sorted(set(cerebro.superclase.tolist()))
    codigo = {n: i for i, n in enumerate(nombres)}
    clase = np.array([codigo[s] for s in cerebro.superclase], dtype=np.uint8)
    datos = {"cerebro": cerebro.nombre, "n": int(cerebro.n), "lo": lo.tolist(), "hi": hi.tolist(),
             "pos": _b64(q), "clase": _b64(clase), "clases": nombres, "ejemplos": ejemplos}
    js = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (PLANTILLA.replace("__THREE__", THREE_URL).replace("/*__DATOS__*/", js)).encode("utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="envivo",
                                 description="El cerebro de la mosca en tiempo real, en tu navegador.")
    ap.add_argument("--puerto", type=int, default=8765)
    ap.add_argument("--cerebro", choices=["flywire", "sintetico"], default="flywire")
    ap.add_argument("--no-abrir", action="store_true", help="no abrir el navegador")
    a = ap.parse_args(argv)

    print("· cargando el cerebro …", file=sys.stderr)
    try:
        cerebro = d.cargar_cerebro(a.cerebro)
        pos = d.cargar_posiciones(cerebro)
    except d.DrosophilaError as e:
        print(f"envivo: {e}", file=sys.stderr)
        return 1
    carpeta = Path(__file__).resolve().parent
    ejemplos = {p.name: p.read_text(encoding="utf-8") for p in sorted(carpeta.glob("*.dros"))}
    for k, v in EJEMPLOS_INTEGRADOS.items():
        ejemplos.setdefault(k, v)
    estado = Estado(cerebro, construir_html(cerebro, pos, ejemplos))

    servidor = None
    for puerto in range(a.puerto, a.puerto + 20):
        try:
            servidor = ThreadingHTTPServer(("127.0.0.1", puerto), crear_manejador(estado))
            break
        except OSError:
            continue
    if servidor is None:
        print("envivo: no hay ningún puerto libre entre "
              f"{a.puerto} y {a.puerto + 19}", file=sys.stderr)
        return 1
    servidor.daemon_threads = True
    url = f"http://127.0.0.1:{servidor.server_address[1]}/"
    print(f"· cerebro {cerebro.nombre} listo: {cerebro.n} neuronas", file=sys.stderr)
    print(f"· abre {url} en tu navegador (Ctrl + C para salir)", file=sys.stderr)
    if not a.no_abrir:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        servidor.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\n· servidor detenido. Hasta la próxima.", file=sys.stderr)
    finally:
        if estado.sim is not None:
            estado.sim.detener.set()
        servidor.server_close()
    return 0


PLANTILLA = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Cerebro de mosca en vivo</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400&display=swap" rel="stylesheet">
<style>
:root {
  box-sizing: border-box;
  --campo: #060b1c; --campo-centro: #0d1a3a;
  --tinta: #e8e4d8; --tinta-suave: #97a0bb; --linea: rgba(151, 160, 187, 0.22);
  --fuego: #ff8a1f; --panel: rgba(8, 14, 34, 0.8);
  font-family: "Instrument Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  color: var(--tinta);
}
*, *::before, *::after { box-sizing: inherit; }
html, body { height: 100%; margin: 0; }
body { background: radial-gradient(ellipse at 50% 42%, var(--campo-centro), var(--campo) 70%);
  overflow: hidden; font-feature-settings: "tnum" 1; }
#escena { position: fixed; inset: 0; }
#escena canvas { display: block; touch-action: none; cursor: grab; }
#escena canvas:active { cursor: grabbing; }
.panel { position: fixed; top: calc(16px + env(safe-area-inset-top, 0px));
  background: var(--panel); backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);
  border: 1px solid var(--linea); border-radius: 10px; padding: 16px 18px;
  max-height: calc(100% - 132px); overflow-y: auto; }
#programa { left: 16px; width: 330px; }
#estado { right: 16px; width: 290px; }
.panel h1 { font-size: 19px; font-weight: 600; margin: 0 0 4px; letter-spacing: -0.01em; }
.panel h2 { font-size: 13px; font-weight: 600; margin: 16px 0 7px; color: var(--tinta-suave); }
.panel p { margin: 0; font-size: 13px; line-height: 1.45; color: var(--tinta-suave); }
textarea, input[type=text], select, button {
  font: inherit; color: var(--tinta); background: rgba(6, 11, 28, 0.6);
  border: 1px solid var(--linea); border-radius: 7px; }
textarea { width: 100%; height: 200px; resize: vertical; padding: 9px 10px;
  font-family: "JetBrains Mono", ui-monospace, Menlo, monospace; font-size: 12px; line-height: 1.5;
  white-space: pre; overflow: auto; }
input[type=text] { width: 100%; padding: 7px 10px; }
select { padding: 7px 10px; width: 100%; }
button { padding: 8px 12px; cursor: pointer; background: transparent; }
button:hover, select:hover { border-color: var(--tinta-suave); }
button.principal { background: var(--fuego); color: #1a0d00; border-color: var(--fuego); font-weight: 600; }
button.principal:hover { filter: brightness(1.08); }
button:disabled { opacity: 0.45; cursor: default; }
button:focus-visible, select:focus-visible, input:focus-visible, textarea:focus-visible, #linea:focus-visible {
  outline: 2px solid var(--fuego); outline-offset: 2px; }
.fila { display: flex; gap: 8px; }
.fila > * { flex: 1; }
.campo { display: grid; gap: 5px; margin-top: 10px; font-size: 13px; color: var(--tinta-suave); }
.check { display: flex; gap: 8px; align-items: center; font-size: 13px; margin-top: 10px; cursor: pointer; }
.check input { accent-color: var(--fuego); }
#aviso { margin-top: 10px; font-size: 13px; line-height: 1.45; min-height: 18px; }
#aviso.error { color: #ffb38a; }
#salida { font-size: 19px; font-weight: 500; min-height: 26px; white-space: pre-wrap; word-break: break-word; }
#salida .cursor { display: inline-block; width: 9px; height: 19px; vertical-align: -3px;
  background: var(--fuego); margin-left: 2px; }
#memoria { display: grid; gap: 7px; font-size: 13px; }
#memoria .eng { display: grid; grid-template-columns: 70px 1fr 34px; gap: 8px; align-items: center; }
#memoria .barra { height: 8px; border-radius: 4px; background: rgba(151,160,187,0.18); overflow: hidden; }
#memoria .barra i { display: block; height: 100%; background: var(--fuego); width: 0; }
#memoria .valor { text-align: right; }
#ahora { list-style: none; margin: 0; padding: 0; font-size: 13px; min-height: 22px; }
#ahora li { padding: 3px 0; border-bottom: 1px solid var(--linea); }
#ahora li small { color: var(--tinta-suave); margin-left: 6px; }
#ahora .vacio { color: var(--tinta-suave); border: 0; }
#cifras { font-size: 13px; color: var(--tinta-suave); line-height: 1.6; }
#cifras b { color: var(--tinta); font-weight: 500; }
.escala-fuego { height: 8px; border-radius: 4px; margin: 4px 0 3px;
  background: linear-gradient(90deg, #3b0073, #d9143f, #ff8c0d, #fffbd9); }
.escala-fuego-rotulos { display: flex; justify-content: space-between; font-size: 11px; color: var(--tinta-suave); }
.etiqueta { position: fixed; pointer-events: none; font-size: 12px; line-height: 1.25; padding: 2px 7px;
  border-left: 2px solid var(--fuego); background: rgba(6, 11, 28, 0.72); white-space: nowrap;
  transform: translate(10px, -50%); }
.etiqueta small { color: var(--tinta-suave); margin-left: 4px; }
#barra { position: fixed; left: 16px; right: 16px; bottom: calc(16px + env(safe-area-inset-bottom, 0px));
  display: flex; gap: 12px; align-items: center; background: var(--panel);
  backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);
  border: 1px solid var(--linea); border-radius: 10px; padding: 10px 14px; }
#barra select { width: auto; }
#pausa { min-width: 104px; }
#directo { white-space: nowrap; }
#indicador { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--tinta-suave);
  margin-right: 6px; vertical-align: 1px; }
#indicador.vivo { background: var(--fuego); box-shadow: 0 0 8px var(--fuego); }
#linea { flex: 1; height: 44px; cursor: pointer; display: block; min-width: 80px; }
#reloj { font-size: 14px; min-width: 120px; text-align: right; color: var(--tinta-suave); }
#reloj b { color: var(--tinta); font-weight: 600; }
#escala { position: fixed; left: 366px; bottom: 104px; font-size: 12px; color: var(--tinta-suave); }
#escala div { height: 2px; background: var(--tinta); margin-bottom: 5px; }
#error-global { position: fixed; inset: 0; display: none; place-items: center; text-align: center;
  padding: 24px; font-size: 16px; }
.solo-movil { display: none; }
@media (max-width: 900px) {
  .panel { display: none; left: 8px !important; right: 8px !important; width: auto !important;
    top: auto; bottom: 120px; max-height: 55%; }
  .panel.abierto { display: block; }
  .solo-movil { display: inline-block; }
  #barra { left: 8px; right: 8px; gap: 8px; padding: 8px; flex-wrap: wrap; }
  #linea { order: 9; flex-basis: 100%; }
  #escala { left: 16px; bottom: 132px; }
}
@media (prefers-reduced-motion: reduce) { #indicador.vivo { box-shadow: none; } }

.hd { display: grid; gap: 8px; }
.hd label { display: grid; gap: 5px; font-size: 13px; color: var(--tinta-suave); }
.hd .fila-hd { display: flex; gap: 8px; }
.hd .fila-hd select { flex: 1; width: auto; }
.hd button, .hd select { width: 100%; font-size: 13px; padding: 7px 10px; }
.hd .fila-hd button { white-space: nowrap; }
.hd input[type=range] { width: 100%; accent-color: var(--fuego); }
.hd .fila-hd button { width: auto; }
.nota { margin: 0; font-size: 12px; color: var(--tinta-suave); line-height: 1.4; min-height: 16px; }
</style>
</head>
<body>
<div id="escena"></div>

<section class="panel" id="programa" aria-label="Programa">
  <h1>Cerebro de mosca en vivo</h1>
  <p id="subtitulo"></p>
  <label class="campo">Ejemplo
    <select id="ejemplo"></select>
  </label>
  <label class="campo">Programa
    <textarea id="codigo" spellcheck="false" autocomplete="off"></textarea>
  </label>
  <label class="campo">Olor de entrada (números separados por espacios)
    <input type="text" id="olor" value="3 4" autocomplete="off">
  </label>
  <label class="check"><input type="checkbox" id="sin-bucles"> Dejar que los bucles sigan para siempre</label>
  <div class="fila" style="margin-top:12px">
    <button class="principal" id="ejecutar" type="button">Ejecutar en vivo</button>
    <button id="detener" type="button" disabled>Detener</button>
  </div>
  <div id="aviso" role="status"></div>
</section>

<aside class="panel" id="estado" aria-label="Estado del cerebro">
  <h2 style="margin-top:0">Lo que escribe la mosca</h2>
  <div id="salida" aria-live="polite"></div>
  <h2>Memoria: sinapsis KC → MBON</h2>
  <div id="memoria"><p>Sin engramas en este programa.</p></div>
  <h2>Neuronas del programa activas</h2>
  <ul id="ahora"><li class="vacio">Pulsa «Ejecutar en vivo» para empezar</li></ul>
  <h2>Cerebro</h2>
  <div id="cifras"></div>
  <h2>Intensidad de disparo</h2>
  <div class="escala-fuego"></div>
  <div class="escala-fuego-rotulos"><span>hace 3 pasos</span><span>ahora</span></div>
  <h2>Vista</h2>
  <label class="check" style="margin-top:0"><input type="checkbox" id="ocultar-optico"> Ocultar los lóbulos ópticos</label>
  <label class="check"><input type="checkbox" id="girar"> Girar el cerebro solo</label>
  <button id="frontal" type="button" style="margin-top:10px;width:100%">Volver a la vista frontal</button>
  <h2>Alta resolución</h2>
  <div class="hd">
    <label>Calidad en pantalla
      <select id="calidad">
        <option value="1">Estándar (1×)</option>
        <option value="2">Retina (2×)</option>
        <option value="3">Supermuestreo (3×)</option>
      </select>
    </label>
    <label>Tamaño de los puntos
      <input type="range" id="tam-puntos" min="0.2" max="1.6" step="0.05" value="1">
    </label>
    <div class="fila-hd">
      <select id="tam-imagen" aria-label="Tamaño de la imagen">
        <option value="3840x2160">Imagen 4K</option>
        <option value="7680x4320">Imagen 8K</option>
      </select>
      <button id="guardar-imagen" type="button">Guardar imagen</button>
    </div>
    <button id="grabar" type="button">Grabar vídeo</button>
    <p class="nota" id="aviso-hd"></p>
  </div>
</aside>

<div id="escala"><div id="escala-barra"></div><span>100 µm</span></div>

<div id="barra">
  <button id="pausa" type="button" disabled>Pausa</button>
  <select id="velocidad" aria-label="Velocidad">
    <option value="3">Lento (3 pasos/s)</option>
    <option value="10" selected>Normal (10 pasos/s)</option>
    <option value="30">Rápido (30 pasos/s)</option>
    <option value="100">Muy rápido (100 pasos/s)</option>
    <option value="1000">Tiempo biológico (~1 ms por paso)</option>
    <option value="100000">Sin límite</option>
  </select>
  <button id="directo" type="button" title="Seguir el paso más reciente"><span id="indicador"></span>En directo</button>
  <canvas id="linea" tabindex="0" aria-label="Línea de tiempo: arrastra para revisar pasos anteriores"></canvas>
  <div id="reloj">sin ejecutar</div>
  <button class="solo-movil" id="ver-programa" type="button">Programa</button>
  <button class="solo-movil" id="ver-estado" type="button">Estado</button>
</div>

<div id="error-global"></div>

<script id="datos" type="application/json">/*__DATOS__*/</script>
<script src="__THREE__"></script>
<script>
(function () {
"use strict";
const D = JSON.parse(document.getElementById("datos").textContent);
const $ = id => document.getElementById(id);
if (!window.THREE) {
  const e = $("error-global"); e.style.display = "grid";
  e.textContent = "No se pudo cargar la librería 3D (three.js). Comprueba tu conexión a internet y recarga la página.";
  return;
}
function b64(s, T) {
  const bin = atob(s), u = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
  return new T(u.buffer);
}
function escapar(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

// ---------- anatomía ----------
const N = D.n, q = b64(D.pos, Uint16Array), clase = b64(D.clase, Uint8Array), lo = D.lo, hi = D.hi;
const centro = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
const COLORES = { optic: "#34507f", visual_projection: "#40619a", visual_centrifugal: "#40619a",
  central: "#6d7ca3", sensory: "#3b8a7b", sensory_ascending: "#3b8a7b", ascending: "#8a78a6",
  descending: "#8a78a6", motor: "#a89d69", endocrine: "#9a6f7d", "": "#555c70" };
const posicion = new Float32Array(N * 3), base = new Float32Array(N * 3), tam = new Float32Array(N), optico = new Float32Array(N);
const colorClase = new Float32Array(N * 3), tamClase = new Float32Array(N);
const col = new THREE.Color();
for (let i = 0; i < N; i++) {
  posicion[3 * i] = lo[0] + q[3 * i] / 65535 * (hi[0] - lo[0]) - centro[0];
  posicion[3 * i + 1] = -(lo[1] + q[3 * i + 1] / 65535 * (hi[1] - lo[1]) - centro[1]);
  posicion[3 * i + 2] = -(lo[2] + q[3 * i + 2] / 65535 * (hi[2] - lo[2]) - centro[2]);
  const sc = D.clases[clase[i]];
  col.set(COLORES[sc] || "#555c70");
  colorClase[3 * i] = base[3 * i] = col.r; colorClase[3 * i + 1] = base[3 * i + 1] = col.g; colorClase[3 * i + 2] = base[3 * i + 2] = col.b;
  const esOptico = sc.indexOf("optic") >= 0 || sc.indexOf("visual") >= 0;
  optico[i] = esOptico ? 1 : 0;
  tamClase[i] = tam[i] = esOptico ? 3.2 : 4.2;
}
$("subtitulo").textContent = D.cerebro + ": " + N.toLocaleString("es") + " neuronas en su posición real. La simulación corre en tu ordenador mientras la miras.";

// ---------- escena ----------
const cont = $("escena");
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
cont.appendChild(renderer.domElement);
const lienzo = renderer.domElement;
const escena = new THREE.Scene();
const camara = new THREE.PerspectiveCamera(35, 1, 1, 20000);

const geo = new THREE.BufferGeometry();
geo.setAttribute("position", new THREE.BufferAttribute(posicion, 3));
const atrBase = new THREE.BufferAttribute(base, 3), atrTamBase = new THREE.BufferAttribute(tam, 1);
geo.setAttribute("aBase", atrBase);
geo.setAttribute("aTam", atrTamBase);
geo.setAttribute("aOptico", new THREE.BufferAttribute(optico, 1));
const materialReposo = new THREE.ShaderMaterial({
  uniforms: { uEscala: { value: 1 }, uOcultar: { value: 0 }, uReposo: { value: 0.34 }, uTam: { value: 1 } },
  vertexShader: `
    attribute vec3 aBase; attribute float aTam; attribute float aOptico;
    uniform float uEscala; uniform float uOcultar; uniform float uTam; varying vec3 vBase;
    void main() {
      vec4 mv = modelViewMatrix * vec4(position, 1.0);
      gl_PointSize = uOcultar * aOptico > 0.5 ? 0.0 : max(1.0, aTam * uTam * uEscala / -mv.z);
      gl_Position = projectionMatrix * mv; vBase = aBase;
    }`,
  fragmentShader: `
    uniform float uReposo; varying vec3 vBase;
    void main() {
      float r = length(gl_PointCoord - 0.5); if (r > 0.5) discard;
      gl_FragColor = vec4(vBase, uReposo * smoothstep(0.5, 0.1, r));
    }`,
  transparent: true, depthWrite: false, blending: THREE.NormalBlending
});
const nube = new THREE.Points(geo, materialReposo);
escena.add(nube);

let capacidad = 0, posAct, valAct, tamAct, atrPos, atrVal, atrTam;
const geoAct = new THREE.BufferGeometry();
function asegurarCapacidad(n) {
  if (n <= capacidad) return;
  capacidad = Math.max(n, capacidad * 2, 20000);
  posAct = new Float32Array(capacidad * 3); valAct = new Float32Array(capacidad); tamAct = new Float32Array(capacidad);
  atrPos = new THREE.BufferAttribute(posAct, 3); atrVal = new THREE.BufferAttribute(valAct, 1); atrTam = new THREE.BufferAttribute(tamAct, 1);
  [atrPos, atrVal, atrTam].forEach(a => a.setUsage(THREE.DynamicDrawUsage));
  geoAct.setAttribute("position", atrPos); geoAct.setAttribute("aAct", atrVal); geoAct.setAttribute("aTam", atrTam);
}
asegurarCapacidad(20000);
geoAct.setDrawRange(0, 0);
const materialActivo = new THREE.ShaderMaterial({
  uniforms: { uEscala: { value: 1 }, uTam: { value: 1 } },
  vertexShader: `
    attribute float aAct; attribute float aTam; uniform float uEscala; uniform float uTam; varying float vAct;
    void main() {
      vec4 mv = modelViewMatrix * vec4(position, 1.0);
      gl_PointSize = max(2.0, (aTam * 1.3 + aAct * 6.5) * uTam * uEscala / -mv.z);
      gl_Position = projectionMatrix * mv; vAct = aAct;
    }`,
  fragmentShader: `
    varying float vAct;
    vec3 fuego(float x) {
      vec3 a = vec3(0.23, 0.0, 0.45), b = vec3(0.85, 0.08, 0.25), c = vec3(1.0, 0.55, 0.05), d = vec3(1.0, 0.98, 0.85);
      if (x < 0.33) return mix(a, b, x / 0.33);
      if (x < 0.66) return mix(b, c, (x - 0.33) / 0.33);
      return mix(c, d, (x - 0.66) / 0.34);
    }
    void main() {
      float r = length(gl_PointCoord - 0.5); if (r > 0.5) discard;
      float a = smoothstep(0.5, 0.0, r) * (0.38 + 0.45 * vAct);
      gl_FragColor = vec4(fuego(vAct) * a, a);
    }`,
  transparent: true, depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending
});
const chispas = new THREE.Points(geoAct, materialActivo);
chispas.frustumCulled = false; chispas.renderOrder = 1;
escena.add(chispas);

// ---------- cámara ----------
const TAN = Math.tan(THREE.MathUtils.degToRad(35) / 2);
const radioX = (hi[0] - lo[0]) / 2, radioY = (hi[1] - lo[1]) / 2;
function encuadre(aspecto) { return Math.max(radioY / TAN, radioX / (TAN * aspecto)) * 1.3; }
let DIST0 = encuadre(window.innerWidth / Math.max(1, window.innerHeight));
let azimut = 0, elevacion = 0.12, distancia = DIST0, girarSolo = false;
function colocarCamara() {
  camara.position.set(distancia * Math.sin(azimut) * Math.cos(elevacion), distancia * Math.sin(elevacion),
                      distancia * Math.cos(azimut) * Math.cos(elevacion));
  camara.lookAt(0, 0, 0);
}
const punteros = new Map(); let pellizco0 = 0, dist0 = DIST0;
lienzo.addEventListener("pointerdown", e => {
  lienzo.setPointerCapture(e.pointerId); punteros.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (punteros.size === 2) { const [a, b] = [...punteros.values()]; pellizco0 = Math.hypot(a.x - b.x, a.y - b.y); dist0 = distancia; }
});
lienzo.addEventListener("pointermove", e => {
  if (!punteros.has(e.pointerId)) return;
  const p = punteros.get(e.pointerId);
  if (punteros.size === 1) {
    azimut -= (e.clientX - p.x) * 0.006;
    elevacion = Math.max(-1.45, Math.min(1.45, elevacion + (e.clientY - p.y) * 0.006));
  }
  p.x = e.clientX; p.y = e.clientY;
  if (punteros.size === 2) {
    const [a, b] = [...punteros.values()], dd = Math.hypot(a.x - b.x, a.y - b.y);
    if (pellizco0 > 0) distancia = Math.max(DIST0 * 0.04, Math.min(DIST0 * 3, dist0 * pellizco0 / dd));
  }
  sucio = true;
});
const soltar = e => { punteros.delete(e.pointerId); pellizco0 = 0; };
lienzo.addEventListener("pointerup", soltar); lienzo.addEventListener("pointercancel", soltar);
lienzo.addEventListener("wheel", e => {
  e.preventDefault();
  distancia = Math.max(DIST0 * 0.04, Math.min(DIST0 * 3, distancia * Math.exp(e.deltaY * 0.0012))); sucio = true;
}, { passive: false });

// ---------- estado de la ejecución ----------
let cuadros = [], cuentas = [], salidaHasta = [""], pesosHasta = [];
let T = 0, t = 0, enDirecto = true, fuente = null, corriendo = false, pausado = false, finTexto = "";
let prog = new Map(), engramas = [], pesos0 = [], prevProg = [], despertadasFin = null;
let sucio = true, lineaSucia = true, cuadroSucio = true;
const act = new Float32Array(N);
const PESOS = [1.0, 0.62, 0.36, 0.18];

function prepararEjecucion(desc) {
  for (const i of prevProg) {
    base[3 * i] = colorClase[3 * i]; base[3 * i + 1] = colorClase[3 * i + 1]; base[3 * i + 2] = colorClase[3 * i + 2]; tam[i] = tamClase[i];
  }
  prog = new Map(desc.reclutadas.map(r => [r.i, r]));
  prevProg = desc.reclutadas.map(r => r.i);
  for (const i of prevProg) { base[3 * i] = 0.91; base[3 * i + 1] = 0.89; base[3 * i + 2] = 0.85; tam[i] = 8.0; }
  atrBase.needsUpdate = atrTamBase.needsUpdate = true;
  engramas = desc.engramas; pesos0 = desc.pesos0;
  cuadros = []; cuentas = []; salidaHasta = [""]; pesosHasta = []; T = 0; t = 0; finTexto = ""; despertadasFin = null;
  enDirecto = true; lineaSucia = cuadroSucio = sucio = true;
}

function aplicarCuadro() {
  act.fill(0);
  const lista = [];
  for (let k = PESOS.length - 1; k >= 0; k--) {
    const tt = t - k; if (tt < 1 || tt > T) continue;
    const w = PESOS[k], fr = cuadros[tt - 1];
    for (let j = 0; j < fr.length; j++) { const i = fr[j]; if (act[i] === 0) lista.push(i); if (act[i] < w) act[i] = w; }
  }
  asegurarCapacidad(lista.length);
  let n = 0;
  for (const i of lista) {
    posAct[3 * n] = posicion[3 * i]; posAct[3 * n + 1] = posicion[3 * i + 1]; posAct[3 * n + 2] = posicion[3 * i + 2];
    valAct[n] = act[i]; tamAct[n] = tam[i]; n++;
  }
  geoAct.setDrawRange(0, n);
  atrPos.needsUpdate = atrVal.needsUpdate = atrTam.needsUpdate = true;
  actualizarPanel();
}

function actualizarPanel() {
  const texto = t > 0 ? salidaHasta[Math.min(t, salidaHasta.length - 1)] : "";
  const terminado = !corriendo && T > 0 && t === T;
  $("salida").innerHTML = escapar(texto + (terminado ? finTexto : "")) + (corriendo || t < T ? '<span class="cursor"></span>' : "");
  const lista = [];
  if (t >= 1 && t <= T) for (const i of cuadros[t - 1]) { const r = prog.get(i); if (r) lista.push(r); }
  $("ahora").innerHTML = T === 0 ? '<li class="vacio">Pulsa «Ejecutar en vivo» para empezar</li>' :
    (lista.length ? lista.map(r => `<li>${escapar(r.papel)}<small>${escapar(r.tipo)}</small></li>`).join("")
                  : '<li class="vacio">Ninguna en este paso</li>');
  const pesos = t >= 1 ? pesosHasta[t - 1] : pesos0;
  if (engramas.length) {
    const maxP = Math.max(1, ...pesosHasta.flat(), ...pesos0);
    $("memoria").innerHTML = engramas.map((e, k) => {
      const v = pesos ? pesos[k] : 0;
      return `<div class="eng"><span>${escapar(e)}</span><span class="barra"><i style="width:${(v / maxP * 100).toFixed(1)}%"></i></span><span class="valor">${v}</span></div>`;
    }).join("");
  } else $("memoria").innerHTML = "<p>Sin engramas en este programa.</p>";
  const n = t >= 1 ? cuentas[t - 1] : 0;
  $("cifras").innerHTML = `<b>${n.toLocaleString("es")}</b> neuronas naturales disparando ahora<br>` +
    `<b>${prog.size}</b> neuronas reclutadas por el programa` +
    (despertadasFin !== null ? `<br><b>${despertadasFin.toLocaleString("es")}</b> despertaron durante toda la vida` : "");
  $("reloj").innerHTML = T === 0 ? "sin ejecutar" : `paso <b>${t}</b> de ${T}${corriendo ? "…" : ""}`;
  etiquetasActivas = lista;
}

// ---------- etiquetas ----------
let etiquetasActivas = []; const reserva = []; const v3 = new THREE.Vector3();
function colocarEtiquetas() {
  const w = lienzo.clientWidth, h = lienzo.clientHeight;
  while (reserva.length < etiquetasActivas.length) { const el = document.createElement("div"); el.className = "etiqueta"; document.body.appendChild(el); reserva.push(el); }
  reserva.forEach((el, k) => {
    const r = etiquetasActivas[k];
    if (!r) { el.style.display = "none"; return; }
    v3.set(posicion[3 * r.i], posicion[3 * r.i + 1], posicion[3 * r.i + 2]).project(camara);
    if (v3.z > 1) { el.style.display = "none"; return; }
    el.style.display = "block";
    el.style.left = ((v3.x + 1) / 2 * w) + "px"; el.style.top = ((1 - v3.y) / 2 * h + k * 2) + "px";
    el.innerHTML = escapar(r.papel) + "<small>" + escapar(r.tipo) + "</small>";
  });
}

// ---------- línea de tiempo ----------
const linea = $("linea"), ctxL = linea.getContext("2d");
function dibujarLinea() {
  const r = window.devicePixelRatio || 1, W = linea.clientWidth, H = linea.clientHeight;
  if (linea.width !== Math.round(W * r) || linea.height !== Math.round(H * r)) { linea.width = Math.round(W * r); linea.height = Math.round(H * r); }
  ctxL.setTransform(r, 0, 0, r, 0, 0); ctxL.clearRect(0, 0, W, H);
  if (T === 0) return;
  const cols = Math.max(1, Math.min(T, Math.floor(W / 2))), porCol = T / cols, bw = W / cols;
  let maxC = 1; for (let k = 0; k < T; k++) if (cuentas[k] > maxC) maxC = cuentas[k];
  for (let c = 0; c < cols; c++) {
    const a = Math.floor(c * porCol), b = Math.max(a + 1, Math.floor((c + 1) * porCol));
    let m = 0; for (let k = a; k < b && k < T; k++) if (cuentas[k] > m) m = cuentas[k];
    const hh = Math.max(1, m / maxC * (H - 12));
    ctxL.fillStyle = b <= t ? "#ff8a1f" : "rgba(151,160,187,0.35)";
    ctxL.fillRect(c * bw, H - 6 - hh, Math.max(1, bw - (bw > 3 ? 1 : 0)), hh);
  }
  const x = (t - 0.5) / T * W;
  ctxL.fillStyle = "#e8e4d8"; ctxL.fillRect(x - 1, 0, 2, H);
}
function irA(clientX) {
  if (T === 0) return;
  const rc = linea.getBoundingClientRect();
  t = Math.max(1, Math.min(T, Math.floor((clientX - rc.left) / rc.width * T) + 1));
  enDirecto = t === T; marcarDirecto(); cuadroSucio = lineaSucia = sucio = true;
}
let arrastrando = false;
linea.addEventListener("pointerdown", e => { arrastrando = true; linea.setPointerCapture(e.pointerId); irA(e.clientX); });
linea.addEventListener("pointermove", e => { if (arrastrando) irA(e.clientX); });
linea.addEventListener("pointerup", () => { arrastrando = false; });
function marcarDirecto() { $("indicador").classList.toggle("vivo", enDirecto && corriendo); }
$("directo").addEventListener("click", () => { enDirecto = true; t = T; marcarDirecto(); cuadroSucio = lineaSucia = sucio = true; });

// ---------- servidor ----------
async function enviar(ruta, datos) {
  const r = await fetch(ruta, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(datos || {}) });
  return r.json();
}
function aviso(texto, esError) { const a = $("aviso"); a.textContent = texto; a.classList.toggle("error", !!esError); }
function velocidad() { return pausado ? 0 : Number($("velocidad").value); }

async function ejecutar() {
  if (fuente) { fuente.close(); fuente = null; }
  aviso("Preparando la mosca…");
  let desc;
  try {
    desc = await enviar("/ejecutar", { codigo: $("codigo").value, olor: $("olor").value, pps: Number($("velocidad").value),
                                       sin_bucles: $("sin-bucles").checked, max_pasos: 30000 });
  } catch (err) { aviso("No hay conexión con el servidor. ¿Sigue abierta la Terminal con envivo.py?", true); return; }
  if (!desc.ok) { aviso(desc.error, true); return; }
  prepararEjecucion(desc);
  pausado = false; corriendo = true;
  $("pausa").disabled = false; $("pausa").textContent = "Pausa"; $("detener").disabled = false;
  aviso(`La mosca está viva. ${desc.reclutadas.length} neuronas reales ejecutan tu programa.`);
  marcarDirecto();
  fuente = new EventSource("/flujo?id=" + desc.id);
  fuente.addEventListener("pasos", ev => {
    const lote = JSON.parse(ev.data);
    for (const p of lote) {
      cuadros.push(b64(p.idx, Int32Array)); cuentas.push(p.cuenta);
      salidaHasta.push(salidaHasta[salidaHasta.length - 1] + p.salida); pesosHasta.push(p.pesos);
    }
    T = cuadros.length;
    if (enDirecto) { t = T; cuadroSucio = true; }
    lineaSucia = sucio = true;
  });
  fuente.addEventListener("fin", ev => {
    const f = JSON.parse(ev.data);
    finTexto = f.salida || ""; despertadasFin = f.despertadas ?? null;
    corriendo = false; fuente.close(); fuente = null;
    $("pausa").disabled = true; $("detener").disabled = true;
    aviso(f.causa + (f.pasos ? ` Vivió ${f.pasos} pasos.` : ""));
    marcarDirecto(); cuadroSucio = lineaSucia = sucio = true;
  });
  fuente.onerror = () => {
    if (!corriendo) return;
    corriendo = false; if (fuente) { fuente.close(); fuente = null; }
    $("pausa").disabled = true; $("detener").disabled = true;
    aviso("Se perdió la conexión con el servidor.", true); marcarDirecto();
  };
}
$("ejecutar").addEventListener("click", ejecutar);
$("detener").addEventListener("click", () => enviar("/detener"));
$("pausa").addEventListener("click", () => {
  pausado = !pausado; $("pausa").textContent = pausado ? "Continuar" : "Pausa";
  enviar("/velocidad", { pps: velocidad() });
});
$("velocidad").addEventListener("change", () => { if (corriendo) enviar("/velocidad", { pps: velocidad() }); });

// ejemplos
const sel = $("ejemplo");
Object.keys(D.ejemplos).forEach(k => { const o = document.createElement("option"); o.value = k; o.textContent = k; sel.appendChild(o); });
if (D.ejemplos["suma.dros"]) sel.value = "suma.dros";
function cargarEjemplo() {
  $("codigo").value = D.ejemplos[sel.value];
  $("olor").value = sel.value === "suma.dros" ? "3 4" : "";
}
sel.addEventListener("change", cargarEjemplo);
cargarEjemplo();

// vista
$("ocultar-optico").addEventListener("change", e => { materialReposo.uniforms.uOcultar.value = e.target.checked ? 1 : 0; sucio = true; });
$("girar").addEventListener("change", e => { girarSolo = e.target.checked; });
$("frontal").addEventListener("click", () => { azimut = 0; elevacion = 0.12; distancia = DIST0; sucio = true; });
$("ver-programa").addEventListener("click", () => { $("programa").classList.toggle("abierto"); $("estado").classList.remove("abierto"); });
$("ver-estado").addEventListener("click", () => { $("estado").classList.toggle("abierto"); $("programa").classList.remove("abierto"); });
window.addEventListener("keydown", e => {
  const tag = e.target.tagName;
  if (tag === "TEXTAREA" || tag === "INPUT" || tag === "SELECT") return;
  if (e.code === "Space" && corriendo) { e.preventDefault(); $("pausa").click(); }
  else if (e.code === "ArrowRight" && T) { t = Math.min(T, t + 1); enDirecto = t === T; marcarDirecto(); cuadroSucio = lineaSucia = sucio = true; }
  else if (e.code === "ArrowLeft" && T) { t = Math.max(1, t - 1); enDirecto = false; marcarDirecto(); cuadroSucio = lineaSucia = sucio = true; }
});

// ---------- bucle de dibujo ----------
let ultimaLinea = 0;
function dibujar(ahora) {
  if (girarSolo && punteros.size === 0) { azimut += 0.0025; sucio = true; }
  if (cuadroSucio) { cuadroSucio = false; aplicarCuadro(); sucio = true; }
  if (lineaSucia && ahora - ultimaLinea > 80) { lineaSucia = false; ultimaLinea = ahora; dibujarLinea(); }
  if (sucio) {
    sucio = false;
    colocarCamara(); renderer.render(escena, camara); colocarEtiquetas();
    const px = lienzo.clientHeight / (2 * distancia * TAN);
    $("escala-barra").style.width = (100 * px) + "px";
  }
  requestAnimationFrame(dibujar);
}
function redimensionar() {
  const w = cont.clientWidth || window.innerWidth, h = cont.clientHeight || window.innerHeight;
  renderer.setSize(w, h, false); lienzo.style.width = w + "px"; lienzo.style.height = h + "px";
  camara.aspect = w / h; camara.updateProjectionMatrix();
  const nuevo = encuadre(w / h); if (Math.abs(distancia - DIST0) < 1e-6) distancia = nuevo; DIST0 = nuevo;
  materialReposo.uniforms.uEscala.value = materialActivo.uniforms.uEscala.value = h * renderer.getPixelRatio() / (2 * TAN);
  lineaSucia = sucio = true;
}
const redibujar = () => { sucio = true; };
function tituloImagen() { return $("ejemplo").value.replace(/\.dros$/, "") + ".dros en el cerebro de una mosca"; }
function textoSalidaActual() { return t > 0 ? salidaHasta[Math.min(t, salidaHasta.length - 1)] + (!corriendo && t === T ? finTexto : "") : ""; }

// ---------- alta resolución: calidad, imágenes 4K/8K y vídeo ----------
const selCalidad = $("calidad");
selCalidad.value = (window.devicePixelRatio || 1) >= 2 ? "2" : "1";
function avisoHD(txt) { $("aviso-hd").textContent = txt; }
function descargar(blob, nombre) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = nombre;
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 4000);
}
function aplicarCalidad() {
  renderer.setPixelRatio(Number(selCalidad.value));
  redimensionar();
  const w = lienzo.width, h = lienzo.height;
  avisoHD(`Dibujando a ${w.toLocaleString("es")} × ${h.toLocaleString("es")} píxeles.`);
}
selCalidad.addEventListener("change", aplicarCalidad);

function exportarImagen() {
  const [ancho, alto] = $("tam-imagen").value.split("x").map(Number);
  const maxTex = renderer.capabilities.maxTextureSize || 4096;
  if (ancho > maxTex || alto > maxTex) {
    avisoHD(`Tu tarjeta gráfica admite como máximo ${maxTex} píxeles por lado; elige un tamaño menor.`);
    return;
  }
  avisoHD("Generando imagen…");
  setTimeout(() => {
    try {
      const rt = new THREE.WebGLRenderTarget(ancho, alto);
      const aspecto0 = camara.aspect, e0 = materialReposo.uniforms.uEscala.value;
      camara.aspect = ancho / alto; camara.updateProjectionMatrix();
      colocarCamara();
      materialReposo.uniforms.uEscala.value = materialActivo.uniforms.uEscala.value = alto / (2 * TAN);
      // posiciones de las etiquetas en la imagen
      const marcas = etiquetasActivas.map(r => {
        const v = new THREE.Vector3(posicion[3 * r.i], posicion[3 * r.i + 1], posicion[3 * r.i + 2]).project(camara);
        return { r, x: (v.x + 1) / 2 * ancho, y: (1 - v.y) / 2 * alto, visible: v.z <= 1 };
      });
      renderer.setRenderTarget(rt);
      renderer.setClearColor(0x081127, 1); renderer.clear();
      renderer.render(escena, camara);
      const px = new Uint8Array(ancho * alto * 4);
      renderer.readRenderTargetPixels(rt, 0, 0, ancho, alto, px);
      renderer.setRenderTarget(null); renderer.setClearColor(0x000000, 0);
      rt.dispose();
      camara.aspect = aspecto0; camara.updateProjectionMatrix();
      materialReposo.uniforms.uEscala.value = materialActivo.uniforms.uEscala.value = e0;

      const c = document.createElement("canvas"); c.width = ancho; c.height = alto;
      const g = c.getContext("2d");
      const img = g.createImageData(ancho, alto), fila = ancho * 4;
      for (let y = 0; y < alto; y++) img.data.set(px.subarray((alto - 1 - y) * fila, (alto - y) * fila), y * fila);
      g.putImageData(img, 0, 0);
      const vi = g.createRadialGradient(ancho * 0.5, alto * 0.45, alto * 0.2, ancho * 0.5, alto * 0.45, alto * 0.95);
      vi.addColorStop(0, "rgba(13,26,58,0)"); vi.addColorStop(1, "rgba(4,7,18,0.55)");
      g.fillStyle = vi; g.fillRect(0, 0, ancho, alto);

      const s = alto / 1080, fam = '"Instrument Sans", system-ui, -apple-system, sans-serif';
      g.textBaseline = "middle";
      for (const m of marcas) {
        if (!m.visible) continue;
        const txt = m.r.papel, sub = m.r.tipo;
        g.font = `500 ${15 * s}px ${fam}`; const w1 = g.measureText(txt).width;
        g.font = `400 ${12 * s}px ${fam}`; const w2 = g.measureText(sub).width;
        const x0 = m.x + 12 * s, hh = 22 * s;
        g.fillStyle = "rgba(6,11,28,0.75)"; g.fillRect(x0, m.y - hh / 2, w1 + w2 + 22 * s, hh);
        g.fillStyle = "#ff8a1f"; g.fillRect(x0, m.y - hh / 2, 2.5 * s, hh);
        g.fillStyle = "#e8e4d8"; g.font = `500 ${15 * s}px ${fam}`; g.fillText(txt, x0 + 8 * s, m.y);
        g.fillStyle = "#97a0bb"; g.font = `400 ${12 * s}px ${fam}`; g.fillText(sub, x0 + 14 * s + w1, m.y);
      }
      const margen = 48 * s;
      g.textBaseline = "alphabetic";
      g.fillStyle = "#e8e4d8"; g.font = `600 ${30 * s}px ${fam}`;
      g.fillText(tituloImagen(), margen, margen + 26 * s);
      g.fillStyle = "#97a0bb"; g.font = `400 ${18 * s}px ${fam}`;
      g.fillText(`${D.cerebro}, ${N.toLocaleString("es")} neuronas. Paso ${t} de ${T}.`, margen, margen + 58 * s);
      const sal = textoSalidaActual().replace(/\n+$/, "");
      if (sal) {
        g.fillStyle = "#e8e4d8"; g.font = `500 ${24 * s}px ${fam}`;
        g.fillText("La mosca escribe: " + sal.split("\n").pop(), margen, alto - margen);
      }
      const px100 = alto / (2 * distancia * TAN) * 100;
      g.fillStyle = "#e8e4d8"; g.fillRect(ancho - margen - px100, alto - margen - 26 * s, px100, 3 * s);
      g.fillStyle = "#97a0bb"; g.font = `400 ${16 * s}px ${fam}`;
      g.textAlign = "right"; g.fillText("100 µm", ancho - margen, alto - margen); g.textAlign = "left";

      c.toBlob(b => {
        if (!b) { avisoHD("El navegador no pudo crear la imagen; prueba con 4K."); return; }
        descargar(b, `mosca_paso${t}_${ancho}x${alto}.png`);
        avisoHD(`Imagen guardada: ${ancho} × ${alto} píxeles (${(b.size / 1e6).toFixed(1)} MB).`);
      }, "image/png");
    } catch (err) {
      avisoHD("No se pudo generar la imagen (" + err.message + "). Prueba con 4K.");
    }
    redibujar();
  }, 30);
}
$("guardar-imagen").addEventListener("click", exportarImagen);
$("tam-puntos").addEventListener("input", e => {
  materialReposo.uniforms.uTam.value = materialActivo.uniforms.uTam.value = Number(e.target.value);
  redibujar();
});

let grabadora = null, trozos = [];
$("grabar").addEventListener("click", () => {
  if (grabadora) { grabadora.stop(); return; }
  if (!lienzo.captureStream || !window.MediaRecorder) { avisoHD("Este navegador no permite grabar vídeo; prueba con Chrome o Safari actualizado."); return; }
  const tipos = ["video/mp4;codecs=avc1", "video/mp4", "video/webm;codecs=vp9", "video/webm"];
  const tipo = tipos.find(x => MediaRecorder.isTypeSupported(x)) || "";
  const opciones = { videoBitsPerSecond: 40000000 };
  if (tipo) opciones.mimeType = tipo;
  grabadora = new MediaRecorder(lienzo.captureStream(60), opciones);
  trozos = [];
  grabadora.ondataavailable = e => { if (e.data && e.data.size) trozos.push(e.data); };
  grabadora.onstop = () => {
    const blob = new Blob(trozos, { type: grabadora.mimeType || "video/webm" });
    const ext = blob.type.indexOf("mp4") >= 0 ? "mp4" : "webm";
    descargar(blob, `mosca_${lienzo.width}x${lienzo.height}.${ext}`);
    avisoHD(`Vídeo guardado (${(blob.size / 1e6).toFixed(1)} MB).`);
    grabadora = null; $("grabar").textContent = "Grabar vídeo";
  };
  grabadora.start(500);
  $("grabar").textContent = "Detener grabación";
  avisoHD(`Grabando a ${lienzo.width} × ${lienzo.height} píxeles. Pulsa de nuevo para terminar.`);
  redibujar();
});

window.addEventListener("resize", redimensionar);
redimensionar(); aplicarCalidad(); actualizarPanel();
requestAnimationFrame(dibujar);
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
