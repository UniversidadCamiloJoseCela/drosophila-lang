#!/usr/bin/env python3
"""
figuras.py — genera los datos de las figuras y tablas del artículo explicativo.

Escribe ficheros .dat (texto, columnas separadas por espacios) que pgfplots lee
directamente, e imprime en pantalla las filas de las tablas.

Uso (desde la raíz del proyecto, con el entorno activado):
    python scripts/figuras.py ~/Documents/repos/latex-docs/drosophila-lang/figures
"""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import drosophila as d  # noqa: E402
import minsky  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent


def grupo(papel: str) -> str:
    """Clasifica el papel de una neurona reclutada para colorearla en la figura."""
    if papel.startswith(("KC·", "MBON·")):
        return "engrama"
    if papel.startswith(("PAM·", "PPL1·", "ORN·")):
        return "dopamina"
    if papel.startswith(("bit", "estrobo", "probóscide", "muerte")):
        return "efector"
    if "retardo" in papel or "→" in papel or "retorno" in papel:
        return "compuerta"
    return "control"


def main() -> int:
    salida = Path(sys.argv[1]).expanduser()
    salida.mkdir(parents=True, exist_ok=True)
    c = d.cargar_flywire(flujo=None)
    pos = d.cargar_posiciones(c, flujo=None)
    suma = (RAIZ / "suma.dros").read_text(encoding="utf-8")

    # 1. Vista frontal del cerebro (x, -y en µm): muestra aleatoria de neuronas.
    rng = np.random.default_rng(0)
    muestra = rng.choice(c.n, size=6000, replace=False)
    np.savetxt(salida / "cerebro.dat", np.c_[pos[muestra, 0], -pos[muestra, 1]],
               fmt="%.1f", header="x y", comments="")

    # 2. Neuronas reclutadas por suma.dros, con su grupo funcional.
    vm = d.DrosophilaVM(suma, cerebro=c, flujo_info=None)
    with open(salida / "implante.dat", "w", encoding="utf-8") as f:
        f.write("x y grupo\n")
        for i in vm.implante.reclutadas:
            f.write(f"{pos[i, 0]:.1f} {-pos[i, 1]:.1f} {grupo(vm.implante.papel[int(i)])}\n")

    # 3. Actividad natural por paso para la entrada (3, 4).
    P = vm.implante
    natural = ~P.es_implante
    filas = []
    vm.ejecutar([3, 4], observador=lambda t, S, W: filas.append((t, int((S & natural).sum()))))
    np.savetxt(salida / "actividad.dat", np.array(filas), fmt="%d", header="t n", comments="")

    # 4. Tabla de ejemplos.
    print("Ejemplos: programa | entrada | salida | pasos | reclutadas | despertadas | reales/total | s")
    casos = [("efe.dros", None, None), ("hola.dros", None, None),
             ("suma.dros", [0, 0], None), ("suma.dros", [3, 4], None),
             ("suma.dros", [12, 30], None), ("suma.dros", [200, 300], None),
             ("longitud.dros", None, "mosca")]
    for nombre, olor, texto in casos:
        v = d.DrosophilaVM((RAIZ / nombre).read_text(encoding="utf-8"), cerebro=c, flujo_info=None)
        entrada = list(texto.encode()) if texto else (olor or [])
        t0 = time.perf_counter()
        r = v.ejecutar(entrada)
        dt = time.perf_counter() - t0
        reales, total = v.implante.fidelidad
        print(f"  {nombre} | {texto or olor} | {r.texto!r} | {r.pasos} | {len(v.implante.reclutadas)}"
              f" | {r.neuronas_despertadas} | {reales}/{total} | {dt:.2f}")

    # 5. Robustez frente a la temperatura: 40 semillas por valor de β.
    with open(salida / "temperatura.dat", "w", encoding="utf-8") as f:
        f.write("beta correctas\n")
        for beta in (8, 12, 16, 20, 24, 32, 64):
            ok = 0
            for s in range(40):
                v = d.DrosophilaVM(suma, cerebro=c, cepa=f"semilla{s}", temperatura=1 / beta,
                                   flujo_info=None)
                r = v.ejecutar([3, 4], max_pasos=5000)
                ok += r.texto == "suma: 7\n" and r.causa == "muerte"
            f.write(f"{beta} {ok}\n")
            print(f"β = {beta}: {ok}/40 correctas")

    # 6. Máquinas de Minsky aleatorias frente a la simulación directa.
    rnd = random.Random(1)
    probadas = coinciden = 0
    for _ in range(3000):
        n_reg = rnd.randint(1, 3)
        prog = minsky.aleatoria(rnd, rnd.randint(2, 12), n_reg)
        regs = [rnd.randint(0, 6) for _ in range(n_reg)]
        esperado = minsky.simular(prog, regs, max_pasos=2000)
        if esperado is None:
            continue
        probadas += 1
        r = d.DrosophilaVM(minsky.traducir(prog, regs), cerebro=c,
                           flujo_info=None).ejecutar(max_pasos=20_000)
        coinciden += (r.causa == "muerte"
                      and [r.engramas.get(f"r{i}", 0) for i in range(n_reg)] == esperado)
    print(f"Minsky: {coinciden}/{probadas} coinciden (3000 máquinas, semilla 1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
