#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
visor3d.py — Visor 3D de la actividad neuronal de Drosophila-Lang.

Ejecuta un programa sobre el cerebro real (FlyWire v783), registra qué
neuronas disparan en cada paso y genera una página HTML autocontenida donde
se ve el cerebro completo en 3D (las 138 639 neuronas en su posición real) y
cómo se encienden mientras corre el programa.

Uso:
    python visor3d.py suma.dros --olor "3 4"          # crea suma_3d.html y lo abre
    python visor3d.py efe.dros --salida mi_visor.html
    python visor3d.py hola.dros --no-abrir
    python visor3d.py -e "(DA1) !'F' x_x"

Controles en el visor: arrastrar para girar, rueda o pellizco para acercar,
barra espaciadora para reproducir o pausar, flechas para avanzar paso a paso.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

import drosophila as d

THREE_URL = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"


def _b64(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode("ascii")


def registrar(vm: d.DrosophilaVM, olor: Sequence[int], max_pasos: int):
    """Ejecuta el programa y devuelve (resultado, cuadros, salidas)."""
    cuadros: List[np.ndarray] = []
    salidas: List[List] = []
    pendiente = bytearray()

    def al_emitir(b: bytes) -> None:
        pendiente.extend(b)

    def observar(t: int, S: np.ndarray, pesos: Optional[np.ndarray] = None) -> None:
        cuadros.append(np.flatnonzero(S).astype(np.int32))
        if pendiente:
            salidas.append([t, bytes(pendiente)])
            pendiente.clear()

    res = vm.ejecutar(olor, max_pasos=max_pasos, flujo_salida=al_emitir, observador=observar)
    if pendiente:                                     # espigas de MN9 volcadas al morir
        salidas.append([res.pasos, bytes(pendiente)])
    return res, cuadros, salidas


def empaquetar(vm: d.DrosophilaVM, res: d.Resultado, cuadros: List[np.ndarray],
               salidas: List[List], pos: np.ndarray, titulo: str,
               entrada: Sequence[int]) -> Dict:
    C, P = vm.cerebro, vm.implante
    lo, hi = pos.min(axis=0), pos.max(axis=0)
    span = np.where(hi - lo > 0, hi - lo, 1.0)
    q = np.round((pos - lo) / span * 65535).astype("<u2")
    nombres = sorted(set(C.superclase.tolist()))
    codigo = {n: i for i, n in enumerate(nombres)}
    clase = np.array([codigo[s] for s in C.superclase], dtype=np.uint8)
    off = np.zeros(len(cuadros) + 1, dtype="<u4")
    off[1:] = np.cumsum([len(c) for c in cuadros])
    idx = np.concatenate(cuadros).astype("<i4") if cuadros else np.zeros(0, "<i4")
    natural = ~P.es_implante
    cuenta = [int(natural[c].sum()) for c in cuadros]
    reclutadas = [{"i": int(i), "papel": P.papel[int(i)], "tipo": C.tipo_visible(int(i))}
                  for i in P.reclutadas]
    causas = {"muerte": "La Fibra Gigante disparó: la mosca ha muerto.",
              "coma": "El programa cayó en silencio.",
              "ciclo": "El programa entró en un bucle infinito.",
              "agotamiento": "Se alcanzó el máximo de pasos."}
    return {
        "titulo": titulo, "cerebro": C.nombre, "n": int(C.n),
        "entrada": list(map(int, entrada)),
        "lo": lo.tolist(), "hi": hi.tolist(),
        "pos": _b64(q), "clase": _b64(clase), "clases": nombres,
        "off": _b64(off), "idx": _b64(idx), "cuenta": cuenta,
        "reclutadas": reclutadas, "gf": int(P.gf),
        "salidas": [[t, b.decode("utf-8", errors="replace")] for t, b in salidas],
        "pasos": int(res.pasos), "causa": causas[res.causa],
        "despertadas": int(res.neuronas_despertadas),
        "engramas": res.engramas,
    }


def generar_html(datos: Dict) -> str:
    js = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (PLANTILLA.replace("__THREE__", THREE_URL)
                     .replace("__TITULO__", datos["titulo"])
                     .replace("/*__DATOS__*/", js))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="visor3d",
                                 description="Visor 3D de la actividad neuronal de un programa Drosophila-Lang.")
    ap.add_argument("programa", nargs="?", help="archivo .dros")
    ap.add_argument("-e", "--evaluar", metavar="CÓDIGO", help="código en línea")
    ap.add_argument("--olor", help="concentraciones de entrada, p. ej. \"3 4\"")
    ap.add_argument("--salida", help="archivo HTML a crear (por defecto <programa>_3d.html)")
    ap.add_argument("--cerebro", choices=["flywire", "sintetico"], default="flywire")
    ap.add_argument("--max-pasos", type=int, default=5000,
                    help="límite de pasos registrados (por defecto 5000)")
    ap.add_argument("--no-abrir", action="store_true", help="no abrir el navegador")
    a = ap.parse_args(argv)
    if a.evaluar is None and a.programa is None:
        ap.error("indique un archivo .dros o use -e CÓDIGO")
    try:
        if a.evaluar is not None:
            fuente, nombre = a.evaluar, "codigo"
        else:
            fuente = Path(a.programa).read_text(encoding="utf-8")
            nombre = Path(a.programa).stem
        olor = d._leer_olor(a.olor, None)
        vm = d.DrosophilaVM(fuente, cerebro=a.cerebro)
        print("· cargando posiciones 3D de las neuronas …", file=sys.stderr)
        pos = d.cargar_posiciones(vm.cerebro)
        print("· ejecutando y registrando la actividad …", file=sys.stderr)
        res, cuadros, salidas = registrar(vm, olor, a.max_pasos)
    except d.DrosophilaError as e:
        print(f"visor3d: {e}", file=sys.stderr)
        return 1
    titulo = f"{nombre}.dros" if a.programa else "código en línea"
    html = generar_html(empaquetar(vm, res, cuadros, salidas, pos, titulo, olor))
    destino = Path(a.salida or f"{nombre}_3d.html").resolve()
    destino.write_text(html, encoding="utf-8")
    print(f"· salida del programa: {res.texto!r}", file=sys.stderr)
    print(f"· visor creado: {destino} ({destino.stat().st_size / 1e6:.1f} MB, {res.pasos} pasos)",
          file=sys.stderr)
    if not a.no_abrir:
        webbrowser.open(destino.as_uri())
    return 0


PLANTILLA = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>__TITULO__ en el cerebro de la mosca</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {
  box-sizing: border-box;
  padding-top: env(safe-area-inset-top, 0px);
  padding-bottom: env(safe-area-inset-bottom, 0px);
  --campo: #060b1c;
  --campo-centro: #0d1a3a;
  --tinta: #e8e4d8;
  --tinta-suave: #97a0bb;
  --linea: rgba(151, 160, 187, 0.22);
  --fuego: #ff8a1f;
  --panel: rgba(8, 14, 34, 0.78);
  font-family: "Instrument Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  color: var(--tinta);
}
*, *::before, *::after { box-sizing: inherit; }
html, body { height: 100%; margin: 0; }
body {
  background: radial-gradient(ellipse at 45% 42%, var(--campo-centro), var(--campo) 70%);
  overflow: hidden;
  font-feature-settings: "tnum" 1;
}
#escena { position: fixed; inset: 0; }
#escena canvas { display: block; touch-action: none; cursor: grab; }
#escena canvas:active { cursor: grabbing; }

.etiqueta {
  position: fixed; pointer-events: none; font-size: 12px; line-height: 1.25;
  padding: 2px 7px; border-left: 2px solid var(--fuego);
  background: rgba(6, 11, 28, 0.72); white-space: nowrap;
  transform: translate(10px, -50%);
}
.etiqueta small { color: var(--tinta-suave); margin-left: 4px; }

#rotulo {
  position: fixed; left: 24px; top: calc(20px + env(safe-area-inset-top, 0px));
  max-width: min(620px, 60vw);
}
#rotulo h1 { font-size: 22px; font-weight: 600; margin: 0 0 4px; letter-spacing: -0.01em; }
#rotulo p { margin: 0; color: var(--tinta-suave); font-size: 14px; line-height: 1.45; }

#escala {
  position: fixed; left: 24px; bottom: 112px; font-size: 12px; color: var(--tinta-suave);
}
#escala div { height: 2px; background: var(--tinta); margin-bottom: 5px; }

#panel {
  position: fixed; right: 16px; top: calc(16px + env(safe-area-inset-top, 0px));
  width: 300px; max-height: calc(100% - 140px); overflow-y: auto;
  background: var(--panel); backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);
  border: 1px solid var(--linea); border-radius: 10px; padding: 16px 18px;
}
#panel h2 { font-size: 13px; font-weight: 600; margin: 18px 0 8px; color: var(--tinta-suave); }
#panel h2:first-child { margin-top: 0; }
#salida {
  font-size: 20px; font-weight: 500; min-height: 28px; white-space: pre-wrap;
  word-break: break-word; color: var(--tinta);
}
#salida .cursor { display: inline-block; width: 9px; height: 20px; vertical-align: -3px;
  background: var(--fuego); margin-left: 2px; }
#ahora { list-style: none; margin: 0; padding: 0; font-size: 13px; min-height: 22px; }
#ahora li { padding: 3px 0; border-bottom: 1px solid var(--linea); }
#ahora li small { color: var(--tinta-suave); margin-left: 6px; }
#ahora .vacio { color: var(--tinta-suave); border: 0; }
#cifras { font-size: 13px; color: var(--tinta-suave); line-height: 1.6; }
#cifras b { color: var(--tinta); font-weight: 500; }
.leyenda { display: grid; grid-template-columns: 12px 1fr; gap: 5px 8px; font-size: 12px;
  color: var(--tinta-suave); align-items: center; }
.leyenda i { width: 10px; height: 10px; border-radius: 50%; display: block; }
.escala-fuego { height: 8px; border-radius: 4px; margin: 4px 0 3px;
  background: linear-gradient(90deg, #3b0073, #d9143f, #ff8c0d, #fffbd9); }
.escala-fuego-rotulos { display: flex; justify-content: space-between; font-size: 11px;
  color: var(--tinta-suave); }
.opciones { display: grid; gap: 8px; font-size: 13px; }
.opciones label { display: flex; gap: 8px; align-items: center; cursor: pointer; }
.opciones input { accent-color: var(--fuego); }

#barra {
  position: fixed; left: 16px; right: 16px; bottom: calc(16px + env(safe-area-inset-bottom, 0px));
  display: flex; gap: 14px; align-items: center;
  background: var(--panel); backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);
  border: 1px solid var(--linea); border-radius: 10px; padding: 10px 14px;
}
button, select {
  font: inherit; color: var(--tinta); background: transparent;
  border: 1px solid var(--linea); border-radius: 7px; padding: 7px 12px; cursor: pointer;
}
button:hover, select:hover { border-color: var(--tinta-suave); }
button:focus-visible, select:focus-visible, input:focus-visible, #linea:focus-visible {
  outline: 2px solid var(--fuego); outline-offset: 2px;
}
#reproducir { min-width: 116px; font-weight: 600; }
#linea { flex: 1; height: 46px; cursor: pointer; display: block; min-width: 80px; }
#reloj { font-size: 14px; min-width: 112px; text-align: right; color: var(--tinta-suave); }
#reloj b { color: var(--tinta); font-weight: 600; }
#muerte {
  position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%);
  font-size: 15px; padding: 10px 16px; border-radius: 8px; pointer-events: none;
  background: rgba(6, 11, 28, 0.85); border: 1px solid var(--linea);
  opacity: 0; transition: opacity 0.4s;
}
#muerte.visible { opacity: 1; }
#error { position: fixed; inset: 0; display: none; place-items: center; text-align: center;
  padding: 24px; font-size: 16px; line-height: 1.5; }
#alternar-panel { display: none; }

@media (max-width: 760px) {
  #panel { top: auto; bottom: 88px; left: 12px; right: 12px; width: auto; max-height: 42%;
    display: none; }
  #panel.abierto { display: block; }
  #alternar-panel { display: inline-block; }
  #rotulo { left: 16px; max-width: calc(100% - 32px); }
  #rotulo h1 { font-size: 18px; }
  #escala { left: 16px; bottom: 96px; }
  #barra { left: 8px; right: 8px; gap: 8px; padding: 8px; flex-wrap: wrap; }
  #linea { order: 5; flex-basis: 100%; }
  #reloj { min-width: 0; }
}
@media (prefers-reduced-motion: reduce) { #muerte { transition: none; } }

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

<div id="rotulo">
  <h1 id="titulo"></h1>
  <p id="subtitulo"></p>
</div>

<div id="escala"><div id="escala-barra"></div><span>100 µm</span></div>

<aside id="panel" aria-label="Estado del programa">
  <h2>Lo que escribe la mosca</h2>
  <div id="salida" aria-live="polite"></div>
  <h2>Neuronas del programa activas</h2>
  <ul id="ahora"></ul>
  <h2>Cerebro</h2>
  <div id="cifras"></div>
  <h2>Intensidad de disparo</h2>
  <div class="escala-fuego"></div>
  <div class="escala-fuego-rotulos"><span>hace 3 pasos</span><span>ahora</span></div>
  <h2>Tipos de neurona en reposo</h2>
  <div class="leyenda" id="leyenda"></div>
  <h2>Vista</h2>
  <div class="opciones">
    <label><input type="checkbox" id="ocultar-optico"> Ocultar los lóbulos ópticos</label>
    <label><input type="checkbox" id="girar"> Girar el cerebro solo</label>
    <button id="frontal" type="button">Volver a la vista frontal</button>
  </div>
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

<div id="muerte" role="status"></div>

<div id="barra">
  <button id="reproducir" type="button">Reproducir</button>
  <select id="velocidad" aria-label="Velocidad">
    <option value="3">Lento</option>
    <option value="8" selected>Normal</option>
    <option value="24">Rápido</option>
    <option value="80">Muy rápido</option>
  </select>
  <canvas id="linea" tabindex="0" aria-label="Línea de tiempo: arrastra para ir a un paso"></canvas>
  <div id="reloj"></div>
  <button id="alternar-panel" type="button">Panel</button>
</div>

<div id="error"></div>

<script id="datos" type="application/json">/*__DATOS__*/</script>
<script src="__THREE__"></script>
<script>
(function () {
"use strict";
const D = JSON.parse(document.getElementById("datos").textContent);
if (!window.THREE) {
  const e = document.getElementById("error");
  e.style.display = "grid";
  e.textContent = "No se pudo cargar la librería 3D (three.js). Abre este archivo con conexión a internet.";
  return;
}

// ---------- datos ----------
function b64(s, T) {
  const bin = atob(s), u = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
  return new T(u.buffer);
}
const N = D.n;
const q = b64(D.pos, Uint16Array), clase = b64(D.clase, Uint8Array);
const off = b64(D.off, Uint32Array), idx = b64(D.idx, Int32Array);
const T = off.length - 1;                     // cuadros: t = 1 … T
const lo = D.lo, hi = D.hi;
const centro = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];

const COLORES = {
  optic: "#34507f", visual_projection: "#40619a", visual_centrifugal: "#40619a",
  central: "#6d7ca3", sensory: "#3b8a7b", sensory_ascending: "#3b8a7b",
  ascending: "#8a78a6", descending: "#8a78a6", motor: "#a89d69", endocrine: "#9a6f7d", "": "#555c70"
};
const NOMBRES = {
  optic: "Lóbulos ópticos", visual_projection: "Proyección visual", visual_centrifugal: "Centrífuga visual",
  central: "Cerebro central", sensory: "Sensoriales", sensory_ascending: "Sensoriales ascendentes",
  ascending: "Ascendentes", descending: "Descendentes", motor: "Motoneuronas", endocrine: "Endocrinas",
  "": "Sin anotar"
};
const prog = new Map(D.reclutadas.map(r => [r.i, r]));

const posicion = new Float32Array(N * 3), base = new Float32Array(N * 3);
const tam = new Float32Array(N), act = new Float32Array(N), optico = new Float32Array(N);
const col = new THREE.Color();
for (let i = 0; i < N; i++) {
  const x = lo[0] + q[3 * i] / 65535 * (hi[0] - lo[0]);
  const y = lo[1] + q[3 * i + 1] / 65535 * (hi[1] - lo[1]);
  const z = lo[2] + q[3 * i + 2] / 65535 * (hi[2] - lo[2]);
  posicion[3 * i] = x - centro[0];
  posicion[3 * i + 1] = -(y - centro[1]);
  posicion[3 * i + 2] = -(z - centro[2]);
  const sc = D.clases[clase[i]];
  col.set(prog.has(i) ? "#e8e4d8" : (COLORES[sc] || "#555c70"));
  base[3 * i] = col.r; base[3 * i + 1] = col.g; base[3 * i + 2] = col.b;
  const esOptico = sc.indexOf("optic") >= 0 || sc.indexOf("visual") >= 0;
  optico[i] = esOptico ? 1 : 0;
  tam[i] = prog.has(i) ? 8.0 : (esOptico ? 3.2 : 4.2);
}

// ---------- escena ----------
const cont = document.getElementById("escena");
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
cont.appendChild(renderer.domElement);
const escena = new THREE.Scene();
const camara = new THREE.PerspectiveCamera(35, 1, 1, 20000);

// Capa 1: anatomía en reposo (estática, mezcla normal: nunca se satura).
const geo = new THREE.BufferGeometry();
geo.setAttribute("position", new THREE.BufferAttribute(posicion, 3));
geo.setAttribute("aBase", new THREE.BufferAttribute(base, 3));
geo.setAttribute("aTam", new THREE.BufferAttribute(tam, 1));
geo.setAttribute("aOptico", new THREE.BufferAttribute(optico, 1));
const materialReposo = new THREE.ShaderMaterial({
  uniforms: { uEscala: { value: 1 }, uOcultar: { value: 0 }, uReposo: { value: 0.34 }, uTam: { value: 1 } },
  vertexShader: `
    attribute vec3 aBase; attribute float aTam; attribute float aOptico;
    uniform float uEscala; uniform float uOcultar; uniform float uTam;
    varying vec3 vBase;
    void main() {
      vec4 mv = modelViewMatrix * vec4(position, 1.0);
      gl_PointSize = uOcultar * aOptico > 0.5 ? 0.0 : max(1.0, aTam * uTam * uEscala / -mv.z);
      gl_Position = projectionMatrix * mv;
      vBase = aBase;
    }`,
  fragmentShader: `
    uniform float uReposo; varying vec3 vBase;
    void main() {
      float r = length(gl_PointCoord - 0.5);
      if (r > 0.5) discard;
      gl_FragColor = vec4(vBase, uReposo * smoothstep(0.5, 0.1, r));
    }`,
  transparent: true, depthWrite: false, blending: THREE.NormalBlending
});
const nube = new THREE.Points(geo, materialReposo);
nube.renderOrder = 0;
escena.add(nube);

// Capa 2: neuronas que disparan (se reconstruye en cada paso, brillo aditivo).
let maxVentana = 1;
for (let k = 1; k <= T; k++) {
  let n = 0;
  for (let j = Math.max(1, k - 3); j <= k; j++) n += off[j] - off[j - 1];
  maxVentana = Math.max(maxVentana, n);
}
const posAct = new Float32Array(maxVentana * 3), valAct = new Float32Array(maxVentana), tamAct = new Float32Array(maxVentana);
const geoAct = new THREE.BufferGeometry();
const atrPos = new THREE.BufferAttribute(posAct, 3), atrVal = new THREE.BufferAttribute(valAct, 1), atrTam = new THREE.BufferAttribute(tamAct, 1);
[atrPos, atrVal, atrTam].forEach(a => a.setUsage(THREE.DynamicDrawUsage));
geoAct.setAttribute("position", atrPos);
geoAct.setAttribute("aAct", atrVal);
geoAct.setAttribute("aTam", atrTam);
geoAct.setDrawRange(0, 0);
const materialActivo = new THREE.ShaderMaterial({
  uniforms: { uEscala: { value: 1 }, uTam: { value: 1 } },
  vertexShader: `
    attribute float aAct; attribute float aTam; uniform float uEscala; uniform float uTam; varying float vAct;
    void main() {
      vec4 mv = modelViewMatrix * vec4(position, 1.0);
      gl_PointSize = max(2.0, (aTam * 1.3 + aAct * 6.5) * uTam * uEscala / -mv.z);
      gl_Position = projectionMatrix * mv;
      vAct = aAct;
    }`,
  fragmentShader: `
    varying float vAct;
    vec3 fuego(float x) {
      vec3 a = vec3(0.23, 0.0, 0.45), b = vec3(0.85, 0.08, 0.25);
      vec3 c = vec3(1.0, 0.55, 0.05), d = vec3(1.0, 0.98, 0.85);
      if (x < 0.33) return mix(a, b, x / 0.33);
      if (x < 0.66) return mix(b, c, (x - 0.33) / 0.33);
      return mix(c, d, (x - 0.66) / 0.34);
    }
    void main() {
      float r = length(gl_PointCoord - 0.5);
      if (r > 0.5) discard;
      float a = smoothstep(0.5, 0.0, r) * (0.38 + 0.45 * vAct);
      gl_FragColor = vec4(fuego(vAct) * a, a);
    }`,
  transparent: true, depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending
});
const chispas = new THREE.Points(geoAct, materialActivo);
chispas.renderOrder = 1;
chispas.frustumCulled = false;
escena.add(chispas);
const material = materialReposo;   // alias para los controles de vista

// ---------- cámara orbital ----------
const TAN = Math.tan(THREE.MathUtils.degToRad(35) / 2);
const radioX = (hi[0] - lo[0]) / 2, radioY = (hi[1] - lo[1]) / 2;
function encuadre(aspecto) { return Math.max(radioY / TAN, radioX / (TAN * aspecto)) * 1.3; }
let DIST0 = encuadre(window.innerWidth / Math.max(1, window.innerHeight));
let azimut = 0, elevacion = 0.12, distancia = DIST0, girarSolo = false;
function colocarCamara() {
  camara.position.set(
    distancia * Math.sin(azimut) * Math.cos(elevacion),
    distancia * Math.sin(elevacion),
    distancia * Math.cos(azimut) * Math.cos(elevacion));
  camara.lookAt(0, 0, 0);
}
const lienzo = renderer.domElement;
const punteros = new Map();
let pellizco0 = 0, dist0 = DIST0;
lienzo.addEventListener("pointerdown", e => {
  lienzo.setPointerCapture(e.pointerId);
  punteros.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (punteros.size === 2) {
    const [a, b] = [...punteros.values()];
    pellizco0 = Math.hypot(a.x - b.x, a.y - b.y); dist0 = distancia;
  }
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
    const [a, b] = [...punteros.values()];
    const dd = Math.hypot(a.x - b.x, a.y - b.y);
    if (pellizco0 > 0) distancia = Math.max(DIST0 * 0.04, Math.min(DIST0 * 3, dist0 * pellizco0 / dd));
  }
  pedirDibujo();
});
const soltar = e => { punteros.delete(e.pointerId); pellizco0 = 0; };
lienzo.addEventListener("pointerup", soltar);
lienzo.addEventListener("pointercancel", soltar);
lienzo.addEventListener("wheel", e => {
  e.preventDefault();
  distancia = Math.max(DIST0 * 0.04, Math.min(DIST0 * 3, distancia * Math.exp(e.deltaY * 0.0012)));
  pedirDibujo();
}, { passive: false });

// ---------- estado temporal ----------
let t = 1, reproduciendo = false, acumulado = 0, ultimo = performance.now();
const PESOS = [1.0, 0.62, 0.36, 0.18];
function aplicarCuadro() {
  act.fill(0);
  const lista = [];
  for (let k = PESOS.length - 1; k >= 0; k--) {
    const tt = t - k;
    if (tt < 1 || tt > T) continue;
    const w = PESOS[k];
    for (let j = off[tt - 1]; j < off[tt]; j++) {
      const i = idx[j];
      if (act[i] === 0) lista.push(i);
      if (act[i] < w) act[i] = w;
    }
  }
  const ocultar = material.uniforms.uOcultar.value > 0.5;
  let n = 0;
  for (const i of lista) {
    posAct[3 * n] = posicion[3 * i]; posAct[3 * n + 1] = posicion[3 * i + 1]; posAct[3 * n + 2] = posicion[3 * i + 2];
    valAct[n] = act[i]; tamAct[n] = tam[i]; n++;
  }
  geoAct.setDrawRange(0, n);
  atrPos.needsUpdate = atrVal.needsUpdate = atrTam.needsUpdate = true;
  actualizarPanel();
  dibujarLinea();
  pedirDibujo();
}

// ---------- panel ----------
const $ = id => document.getElementById(id);
$("titulo").textContent = D.titulo + " en el cerebro de una mosca";
$("subtitulo").textContent = D.cerebro + ": " + N.toLocaleString("es") +
  " neuronas en su posición real." + (D.entrada.length ? " Entrada: " + D.entrada.join(", ") + "." : "");
const leyenda = $("leyenda");
const presentes = new Set(D.clases);
Object.keys(COLORES).filter(k => presentes.has(k) && k !== "visual_centrifugal" && k !== "sensory_ascending")
  .forEach(k => {
    leyenda.insertAdjacentHTML("beforeend", `<i style="background:${COLORES[k]}"></i><span>${NOMBRES[k]}</span>`);
  });
leyenda.insertAdjacentHTML("beforeend", `<i style="background:#e8e4d8"></i><span>Neuronas del programa</span>`);

function escapar(s) { return s.replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function actualizarPanel() {
  let texto = "";
  for (const [ts, s] of D.salidas) if (ts <= t) texto += s;
  $("salida").innerHTML = escapar(texto) + (t < T ? '<span class="cursor"></span>' : "");
  const lista = [];
  if (t >= 1 && t <= T) for (let j = off[t - 1]; j < off[t]; j++) { const r = prog.get(idx[j]); if (r) lista.push(r); }
  $("ahora").innerHTML = lista.length
    ? lista.map(r => `<li>${escapar(r.papel)}<small>${escapar(r.tipo)}</small></li>`).join("")
    : '<li class="vacio">Ninguna en este paso</li>';
  const n = D.cuenta[t - 1] || 0;
  const eng = Object.entries(D.engramas).map(([k, v]) => k + " = " + v).join(", ");
  $("cifras").innerHTML =
    `<b>${n.toLocaleString("es")}</b> neuronas naturales disparando ahora<br>` +
    `<b>${D.despertadas.toLocaleString("es")}</b> despertaron durante toda la vida<br>` +
    `<b>${D.reclutadas.length}</b> neuronas reclutadas por el programa` +
    (eng ? `<br>Engramas al morir: ${escapar(eng)}` : "");
  $("reloj").innerHTML = `paso <b>${t}</b> de ${T}`;
  const m = $("muerte");
  if (t === T) { m.textContent = D.causa + " Paso " + T + "."; m.classList.add("visible"); }
  else m.classList.remove("visible");
  etiquetasActivas = lista;
}

// ---------- etiquetas flotantes ----------
let etiquetasActivas = [];
const reserva = [];
const v3 = new THREE.Vector3();
function colocarEtiquetas() {
  const w = lienzo.clientWidth, h = lienzo.clientHeight;
  while (reserva.length < etiquetasActivas.length) {
    const el = document.createElement("div"); el.className = "etiqueta"; document.body.appendChild(el); reserva.push(el);
  }
  reserva.forEach((el, k) => {
    const r = etiquetasActivas[k];
    if (!r) { el.style.display = "none"; return; }
    v3.set(posicion[3 * r.i], posicion[3 * r.i + 1], posicion[3 * r.i + 2]).project(camara);
    if (v3.z > 1) { el.style.display = "none"; return; }
    el.style.display = "block";
    el.style.left = ((v3.x + 1) / 2 * w) + "px";
    el.style.top = ((1 - v3.y) / 2 * h + k * 2) + "px";
    el.innerHTML = escapar(r.papel) + "<small>" + escapar(r.tipo) + "</small>";
  });
}

// ---------- línea de tiempo ----------
const linea = $("linea"), ctxL = linea.getContext("2d");
const maxCuenta = Math.max(1, ...D.cuenta);
function dibujarLinea() {
  const r = window.devicePixelRatio || 1, W = linea.clientWidth, H = linea.clientHeight;
  if (linea.width !== Math.round(W * r)) { linea.width = Math.round(W * r); linea.height = Math.round(H * r); }
  ctxL.setTransform(r, 0, 0, r, 0, 0);
  ctxL.clearRect(0, 0, W, H);
  const bw = W / T;
  for (let k = 0; k < T; k++) {
    const hh = Math.max(1, (D.cuenta[k] / maxCuenta) * (H - 14));
    ctxL.fillStyle = k < t ? "#ff8a1f" : "rgba(151,160,187,0.35)";
    ctxL.fillRect(k * bw, H - 6 - hh, Math.max(1, bw - (bw > 3 ? 1 : 0)), hh);
  }
  ctxL.fillStyle = "#e8e4d8";
  for (const [ts] of D.salidas) ctxL.fillRect((ts - 1) * bw, H - 4, Math.max(2, bw * 0.6), 3);
  const x = (t - 0.5) * bw;
  ctxL.fillStyle = "#e8e4d8"; ctxL.fillRect(x - 1, 0, 2, H);
}
function irA(clientX) {
  const rc = linea.getBoundingClientRect();
  t = Math.max(1, Math.min(T, Math.floor((clientX - rc.left) / rc.width * T) + 1));
  aplicarCuadro();
}
let arrastrandoLinea = false;
linea.addEventListener("pointerdown", e => { arrastrandoLinea = true; linea.setPointerCapture(e.pointerId); pausar(); irA(e.clientX); });
linea.addEventListener("pointermove", e => { if (arrastrandoLinea) irA(e.clientX); });
linea.addEventListener("pointerup", () => { arrastrandoLinea = false; });

// ---------- controles ----------
function pausar() { reproduciendo = false; $("reproducir").textContent = "Reproducir"; }
function reproducir() {
  if (t >= T) t = 1;
  reproduciendo = true; ultimo = performance.now(); acumulado = 0;
  $("reproducir").textContent = "Pausa"; pedirDibujo();
}
$("reproducir").addEventListener("click", () => reproduciendo ? pausar() : reproducir());
$("ocultar-optico").addEventListener("change", e => { material.uniforms.uOcultar.value = e.target.checked ? 1 : 0; pedirDibujo(); });
$("girar").addEventListener("change", e => { girarSolo = e.target.checked; pedirDibujo(); });
$("frontal").addEventListener("click", () => { azimut = 0; elevacion = 0.12; distancia = DIST0; pedirDibujo(); });
$("alternar-panel").addEventListener("click", () => $("panel").classList.toggle("abierto"));
window.addEventListener("keydown", e => {
  if (e.target.tagName === "SELECT") return;
  if (e.code === "Space") { e.preventDefault(); reproduciendo ? pausar() : reproducir(); }
  else if (e.code === "ArrowRight") { pausar(); t = Math.min(T, t + 1); aplicarCuadro(); }
  else if (e.code === "ArrowLeft") { pausar(); t = Math.max(1, t - 1); aplicarCuadro(); }
});

// ---------- bucle de dibujo ----------
let pendienteDibujo = false;
function pedirDibujo() { if (!pendienteDibujo) { pendienteDibujo = true; requestAnimationFrame(dibujar); } }
function dibujar(ahora) {
  pendienteDibujo = false;
  if (reproduciendo) {
    acumulado += (ahora - ultimo) / 1000 * Number($("velocidad").value);
    ultimo = ahora;
    if (acumulado >= 1) {
      t = Math.min(T, t + Math.floor(acumulado)); acumulado %= 1;
      if (t >= T) pausar();
      aplicarCuadro();
    }
  }
  if (girarSolo && punteros.size === 0) azimut += 0.0025;
  colocarCamara();
  renderer.render(escena, camara);
  colocarEtiquetas();
  // barra de escala: 100 µm a la distancia del centro
  const pxPorMicra = lienzo.clientHeight / (2 * distancia * Math.tan(THREE.MathUtils.degToRad(35) / 2));
  $("escala-barra").style.width = (100 * pxPorMicra) + "px";
  if (reproduciendo || girarSolo) pedirDibujo();
}
function redimensionar() {
  const w = cont.clientWidth || window.innerWidth, h = cont.clientHeight || window.innerHeight;
  renderer.setSize(w, h, false);
  lienzo.style.width = w + "px"; lienzo.style.height = h + "px";
  camara.aspect = w / h; camara.updateProjectionMatrix();
  const nuevo = encuadre(w / h);
  if (Math.abs(distancia - DIST0) < 1e-6) distancia = nuevo;   // sin zoom del usuario: reencuadrar
  DIST0 = nuevo;
  materialReposo.uniforms.uEscala.value = materialActivo.uniforms.uEscala.value = h * renderer.getPixelRatio() / (2 * Math.tan(THREE.MathUtils.degToRad(35) / 2));
  dibujarLinea(); pedirDibujo();
}
const redibujar = () => { pedirDibujo(); };
function tituloImagen() { return D.titulo + " en el cerebro de una mosca"; }
function textoSalidaActual() { let s = ""; for (const [ts, x] of D.salidas) if (ts <= t) s += x; return s; }

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
redimensionar();
aplicarCalidad();
aplicarCuadro();
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
