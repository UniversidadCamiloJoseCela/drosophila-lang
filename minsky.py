#!/usr/bin/env python3
"""
minsky.py — comprobación empírica de la traducción de máquinas de Minsky a Drosophila-Lang.

Genera máquinas de Minsky aleatorias, las traduce a Drosophila-Lang según la
Definición «Traducción» del artículo, las ejecuta sobre el conectoma FlyWire y
compara el resultado con una simulación directa en Python. Si el teorema de
completitud de Turing es correcto, ambos deben coincidir en cada máquina que se
detiene: mismos registros finales y disparo de la Fibra Gigante.

Uso:
    python minsky.py                    # 200 máquinas aleatorias
    python minsky.py --maquinas 50 --semilla 7
"""
from __future__ import annotations

import argparse
import random
import sys
from typing import Dict, List, Optional, Tuple

import drosophila as d

# Instrucción: ("inc", r, q') | ("jzdec", r, q', q'') | ("halt",)
Instr = Tuple


def aleatoria(rng: random.Random, n_estados: int, n_registros: int) -> List[Instr]:
    """Máquina de Minsky aleatoria; el último estado es HALT."""
    prog: List[Instr] = []
    for q in range(n_estados - 1):
        r = rng.randrange(n_registros)
        if rng.random() < 0.5:
            prog.append(("inc", r, rng.randrange(n_estados)))
        else:
            prog.append(("jzdec", r, rng.randrange(n_estados), rng.randrange(n_estados)))
    prog.append(("halt",))
    return prog


def simular(prog: List[Instr], regs: List[int], max_pasos: int) -> Optional[List[int]]:
    """Referencia: ejecuta la máquina directamente. None si no se detiene a tiempo."""
    regs, q = list(regs), 0
    for _ in range(max_pasos):
        ins = prog[q]
        if ins[0] == "halt":
            return regs
        if ins[0] == "inc":
            regs[ins[1]] += 1
            q = ins[2]
        else:
            _, r, si, no = ins
            if regs[r] > 0:
                regs[r] -= 1
                q = si
            else:
                q = no
    return None


def traducir(prog: List[Instr], regs: List[int]) -> str:
    """Programa Drosophila-Lang equivalente (Definición «Traducción» del artículo)."""
    lineas = [f"§ r{i} = {v}" for i, v in enumerate(regs)]
    lineas.append("~~~> (Q0)")
    for q, ins in enumerate(prog):
        if ins[0] == "halt":
            lineas.append(f"(Q{q}) x_x")
        elif ins[0] == "inc":
            lineas.append(f"(Q{q}) +r{ins[1]} ~> (Q{ins[2]})")
        else:
            lineas.append(f"(Q{q}) ?r{ins[1]} ~> (Q{ins[2]}) | (Q{ins[3]})")
    return "\n".join(lineas) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--maquinas", type=int, default=200)
    ap.add_argument("--semilla", type=int, default=1)
    ap.add_argument("--cerebro", choices=["flywire", "sintetico"], default="flywire")
    a = ap.parse_args()

    rng = random.Random(a.semilla)
    cerebro = d.cargar_cerebro(a.cerebro, flujo=sys.stderr)
    probadas = coinciden = 0
    for k in range(a.maquinas):
        n_reg = rng.randint(1, 3)
        prog = aleatoria(rng, rng.randint(2, 12), n_reg)
        regs = [rng.randint(0, 6) for _ in range(n_reg)]
        esperado = simular(prog, regs, max_pasos=2000)
        if esperado is None:
            continue                      # no se detiene: nada que comparar
        probadas += 1
        vm = d.DrosophilaVM(traducir(prog, regs), cerebro=cerebro, flujo_info=None)
        res = vm.ejecutar(max_pasos=20_000)
        obtenido = [res.engramas.get(f"r{i}", 0) for i in range(n_reg)]
        if res.causa == "muerte" and obtenido == esperado:
            coinciden += 1
        else:
            print(f"✗ máquina {k}: esperado {esperado}, obtenido {obtenido} ({res.causa})\n"
                  f"{traducir(prog, regs)}", file=sys.stderr)
    print(f"{coinciden}/{probadas} máquinas que se detienen coinciden con la referencia "
          f"(cerebro {cerebro.nombre})")
    return 0 if coinciden == probadas else 1


if __name__ == "__main__":
    sys.exit(main())
