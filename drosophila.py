#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
drosophila.py — Intérprete oficial de Drosophila-Lang v2.0 («Conectoma real»)
============================================================================

    «No hay memoria: hay sinapsis. No hay reloj: hay tiempo t.»

Drosophila-Lang es un lenguaje esotérico Turing-completo que se ejecuta sobre
el cerebro REAL de una mosca de la fruta: el conectoma completo FlyWire v783
(139 255 neuronas identificadas, ~2,7 millones de conexiones con ≥5 sinapsis),
con los tipos celulares y neurotransmisores anotados por el consorcio FlyWire.

Cómo funciona, en cinco pasos:

  1. LECTURA       el código (glomérulos, soplos, engramas) se analiza.
  2. RECLUTAMIENTO cada pieza del programa se asigna a una neurona REAL con el
                   papel adecuado: la neurona de proyección de DA1, células de
                   Kenyon, MBON, dopaminérgicas PAM y PPL1, neuronas locales del
                   cuerno lateral, receptores olfativos, motoneuronas, la DNa02,
                   la MN9 de la probóscide y la Fibra Gigante (DNp01). Siempre
                   que es posible se eligen siguiendo conexiones que existen de
                   verdad en FlyWire.
  3. IMPLANTE      entre esas neuronas se añade el circuito del programa (como
                   un transgén). Las neuronas reclutadas obedecen al implante;
                   su entrada natural solo les «susurra» (1/8 de su valor), de
                   modo que el cerebro no puede corromper el cómputo.
  4. VIDA          en cada paso t todas las neuronas integran y disparan; las
                   espigas del programa se propagan por las sinapsis reales y el
                   resto del cerebro reacciona (se ve en la traza).
  5. MUERTE        la Fibra Gigante dispara → la mosca escapa del tiempo.

Datos (se descargan una vez, ~135 MB, y se guardan en caché):
  · Conectividad FlyWire v783 del modelo de Shiu et al. (Nature, 2024)
  · Anotaciones de Schlegel et al. (Nature, 2024), flyconnectome/flywire_annotations
Cite a FlyWire (Dorkenwald et al., Nature 2024) si publica algo con esto.

Uso:
    python drosophila.py programa.dros [--olor "3 4"] [--traza] [--reparto]
    python drosophila.py -e "(DA1) !'F' x_x"
    python drosophila.py --puertas
    python drosophila.py programa.dros --cerebro sintetico     # sin descargas

Dependencias: numpy; pandas + pyarrow solo para la primera descarga.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, TextIO, Tuple

import numpy as np

__version__ = "2.2.0"

# ─────────────────────────────────────────────────────────────────────────────
# 0. CONSTANTES
# ─────────────────────────────────────────────────────────────────────────────

#: Glomérulos del lóbulo antenal con receptores olfativos anotados en FlyWire.
GLOMERULOS: Tuple[str, ...] = (
    "D", "DA1", "DA2", "DA3", "DA4l", "DA4m", "DC1", "DC2", "DC3", "DC4",
    "DL1", "DL2d", "DL2v", "DL3", "DL4", "DL5", "DM1", "DM2", "DM3", "DM4",
    "DM5", "DM6", "DP1l", "DP1m", "V", "VA1d", "VA1v", "VA2", "VA3", "VA4",
    "VA5", "VA6", "VA7l", "VA7m", "VC1", "VC2", "VC3", "VC4", "VC5", "VL1",
    "VL2a", "VL2p", "VM1", "VM2", "VM3", "VM4", "VM5d", "VM5v", "VM6l", "VM6m",
    "VM6v", "VM7d", "VM7v",
)

FUENTES_FLYWIRE = {
    "conectividad": "https://raw.githubusercontent.com/philshiu/Drosophila_brain_model/"
                    "main/Connectivity_783.parquet",
    "completitud": "https://raw.githubusercontent.com/philshiu/Drosophila_brain_model/"
                   "main/Completeness_783.csv",
    "anotaciones": "https://raw.githubusercontent.com/flyconnectome/flywire_annotations/"
                   "main/supplemental_files/Supplemental_file1_neuron_annotations.tsv",
}
MN9_ID = 720575940660219265          # MN9 derecha (Shiu et al. 2024)

TEMPERATURA_POR_DEFECTO = 1.0 / 64.0   # neuronas del implante: β = 64
SUSURRO = 0.125                        # peso de la entrada natural sobre el implante
UMBRAL_NATURAL = 0.08                  # fracción de la entrada que hace disparar
FUGA_NATURAL = 0.5                     # fuga de membrana por paso
REFRACTARIO = 2                        # pasos de silencio tras cada espiga
CEPA_POR_DEFECTO = "Canton-S"
ETA = 1.0                              # cuanto de plasticidad dopaminérgica

# ─────────────────────────────────────────────────────────────────────────────
# 1. PATOLOGÍAS (ERRORES)
# ─────────────────────────────────────────────────────────────────────────────

class DrosophilaError(Exception):
    """Raíz de toda patología del lenguaje."""
    tipo = "Patología"

    def __init__(self, mensaje: str, linea: Optional[int] = None,
                 col: Optional[int] = None) -> None:
        self.mensaje, self.linea, self.col = mensaje, linea, col
        donde = ((f" [línea {linea}" + (f", col {col}" if col else "") + "]")
                 if linea else "")
        super().__init__(f"{self.tipo}: {mensaje}{donde}")


class ErrorLexico(DrosophilaError):
    tipo = "Anosmia léxica"


class ErrorSintactico(DrosophilaError):
    tipo = "Malformación sintáctica"


class ErrorNeurodesarrollo(DrosophilaError):
    tipo = "Fallo de neurodesarrollo"


# ─────────────────────────────────────────────────────────────────────────────
# 2. LEXER — la antena
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Token:
    tipo: str
    valor: object
    linea: int
    col: int


# El orden importa: se prueba de arriba abajo (la primera coincidencia gana).
_PATRONES: List[Tuple[str, "re.Pattern[str]"]] = [
    ("COMENTARIO", re.compile(r";;[^\n]*")),
    ("NL",         re.compile(r"\n")),
    ("ESPACIO",    re.compile(r"[ \t\r\f\v]+")),
    ("DIRECTIVA",  re.compile(r"%([^\W\d]\w*)[ \t]+([^\s;]+)")),
    ("SOPLO",      re.compile(r"≈≈>|~~~>")),
    ("FLECHA",     re.compile(r"~>|⇝")),
    ("MUERTE",     re.compile(r"x_x(?!\w)|†")),
    ("GLOM",       re.compile(r"\([ \t]*([^\W\d]\w*)[ \t]*\)")),
    ("ENGRAMA",    re.compile(r"§|\$")),
    ("IGUAL",      re.compile(r"=")),
    ("NUM",        re.compile(r"\d+")),
    ("CADENA",     re.compile(r'"((?:\\.|[^"\\\n])*)"')),
    ("CAR",        re.compile(r"'(\\x[0-9A-Fa-f]{2}|\\.|[^'\\\n])'")),
    ("OP",         re.compile(r"[+\-<!?|*]")),
    ("IDENT",      re.compile(r"[^\W\d]\w*")),
]

_ESCAPES = {"n": 10, "t": 9, "r": 13, "0": 0, "a": 7, "e": 27,
            "\\": 92, "'": 39, '"': 34}


def _desescapar(cuerpo: str, linea: int, col: int) -> bytes:
    """Convierte el cuerpo de un literal en bytes (UTF-8 + escapes \\xHH)."""
    out = bytearray()
    i = 0
    while i < len(cuerpo):
        c = cuerpo[i]
        if c != "\\":
            out += c.encode("utf-8")
            i += 1
            continue
        if i + 1 >= len(cuerpo):
            raise ErrorLexico("secuencia de escape truncada", linea, col)
        e = cuerpo[i + 1]
        if e == "x":
            hx = cuerpo[i + 2:i + 4]
            if len(hx) != 2 or any(ch not in "0123456789abcdefABCDEF" for ch in hx):
                raise ErrorLexico("escape \\x requiere dos dígitos hexadecimales",
                                  linea, col)
            out.append(int(hx, 16))
            i += 4
        elif e in _ESCAPES:
            out.append(_ESCAPES[e])
            i += 2
        else:
            raise ErrorLexico(f"secuencia de escape desconocida \\{e}", linea, col)
    return bytes(out)


def tokenizar(fuente: str) -> List[Token]:
    """Transduce el texto fuente en una secuencia de tokens (potenciales de receptor)."""
    fuente = fuente.replace("\r\n", "\n")
    toks: List[Token] = []
    pos, linea, inicio_linea, n = 0, 1, 0, len(fuente)
    while pos < n:
        for tipo, patron in _PATRONES:
            m = patron.match(fuente, pos)
            if m:
                break
        else:
            raise ErrorLexico(f"carácter no olfateable {fuente[pos]!r}",
                              linea, pos - inicio_linea + 1)
        col = pos - inicio_linea + 1
        if tipo == "NL":
            toks.append(Token("NL", "\n", linea, col))
            linea += 1
            inicio_linea = m.end()
        elif tipo in ("ESPACIO", "COMENTARIO"):
            pass
        elif tipo == "DIRECTIVA":
            toks.append(Token(tipo, (m.group(1).lower(), m.group(2)), linea, col))
        elif tipo == "GLOM":
            toks.append(Token(tipo, m.group(1), linea, col))
        elif tipo == "NUM":
            toks.append(Token(tipo, int(m.group(0)), linea, col))
        elif tipo in ("CADENA", "CAR"):
            toks.append(Token(tipo, _desescapar(m.group(1), linea, col), linea, col))
        else:
            toks.append(Token(tipo, m.group(0), linea, col))
        pos = m.end()
    toks.append(Token("NL", "\n", linea, pos - inicio_linea + 1))
    toks.append(Token("FIN", None, linea, pos - inicio_linea + 1))
    return toks


# ─────────────────────────────────────────────────────────────────────────────
# 3. PARSER — el lóbulo antenal
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Accion:
    tipo: str          # 'inc' | 'dec' | 'olfato' | 'emitir' | 'espiga'
    arg: object        # nombre de engrama, bytes, o None
    linea: int
    col: int


@dataclass
class Desenlace:
    tipo: str          # 'ir' | 'prueba' | 'muerte'
    linea: int
    col: int
    destino: Optional[str] = None
    engrama: Optional[str] = None
    alterno: Optional[str] = None


@dataclass
class Glomerulo:
    nombre: str
    acciones: List[Accion]
    desenlace: Desenlace
    linea: int


@dataclass
class Programa:
    directivas: Dict[str, Tuple[str, int]] = field(default_factory=dict)
    engramas: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    soplos: List[Tuple[str, int]] = field(default_factory=list)
    glomerulos: Dict[str, Glomerulo] = field(default_factory=dict)


_DIRECTIVAS_VALIDAS = {"cepa", "temperatura"}
_RESERVADAS = {"x_x"}


class _Linea:
    """Cursor sobre los tokens de una única línea lógica."""

    def __init__(self, toks: List[Token]) -> None:
        self.toks, self.i = toks, 0
        self.linea = toks[0].linea

    def mirar(self) -> Optional[Token]:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def tomar(self) -> Optional[Token]:
        t = self.mirar()
        self.i += 1
        return t

    def esperar(self, tipo: str, valor: object = None, que: str = "") -> Token:
        t = self.tomar()
        if t is None or t.tipo != tipo or (valor is not None and t.valor != valor):
            hallado = "el fin de línea" if t is None else repr(t.valor)
            raise ErrorSintactico(f"se esperaba {que or tipo}, se halló {hallado}",
                                  t.linea if t else self.linea, t.col if t else None)
        return t

    def fin(self) -> None:
        t = self.mirar()
        if t is not None:
            raise ErrorSintactico(f"materia residual {t.valor!r} tras la sentencia",
                                  t.linea, t.col)


def _validar_nombre(nombre: str, tok: Token) -> None:
    if nombre in _RESERVADAS:
        raise ErrorSintactico(f"'{nombre}' es una palabra reservada (la muerte)",
                              tok.linea, tok.col)


def _analizar_glomerulo(cab: Token, L: _Linea) -> Glomerulo:
    nombre = cab.valor
    _validar_nombre(nombre, cab)
    acciones: List[Accion] = []
    while True:
        t = L.mirar()
        if t is None:
            raise ErrorSintactico(
                f"el glomérulo ({nombre}) carece de desenlace (~>, ? o x_x)", cab.linea)
        if t.tipo == "OP" and t.valor in ("+", "-", "<"):
            L.tomar()
            eng = L.esperar("IDENT", que=f"un engrama tras '{t.valor}'")
            _validar_nombre(eng.valor, eng)
            tipo = {"+": "inc", "-": "dec", "<": "olfato"}[t.valor]
            acciones.append(Accion(tipo, eng.valor, t.linea, t.col))
        elif t.tipo == "OP" and t.valor == "!":
            L.tomar()
            e = L.tomar()
            if e is None:
                raise ErrorSintactico("'!' sin emisión", t.linea, t.col)
            if e.tipo in ("CADENA", "CAR"):
                acciones.append(Accion("emitir", e.valor, t.linea, t.col))
            elif e.tipo == "NUM":
                if not 0 <= e.valor <= 255:
                    raise ErrorSintactico("un byte motor vive en [0, 255]", e.linea, e.col)
                acciones.append(Accion("emitir", bytes([e.valor]), t.linea, t.col))
            elif e.tipo == "OP" and e.valor == "*":
                acciones.append(Accion("espiga", None, t.linea, t.col))
            else:
                raise ErrorSintactico(f"emisión inválida {e.valor!r}", e.linea, e.col)
        elif t.tipo == "FLECHA":
            L.tomar()
            d = L.esperar("GLOM", que="un glomérulo destino (NOMBRE)")
            des = Desenlace("ir", t.linea, t.col, destino=d.valor)
            break
        elif t.tipo == "OP" and t.valor == "?":
            L.tomar()
            eng = L.esperar("IDENT", que="un engrama tras '?'")
            L.esperar("FLECHA", que="'~>' tras la prueba")
            a = L.esperar("GLOM", que="la rama ≠0 (NOMBRE)")
            L.esperar("OP", "|", que="'|' separando las ramas")
            b = L.esperar("GLOM", que="la rama =0 (NOMBRE)")
            des = Desenlace("prueba", t.linea, t.col, destino=a.valor,
                            engrama=eng.valor, alterno=b.valor)
            break
        elif t.tipo == "MUERTE":
            L.tomar()
            des = Desenlace("muerte", t.linea, t.col)
            break
        else:
            raise ErrorSintactico(f"token inesperado {t.valor!r} en ({nombre})",
                                  t.linea, t.col)
    L.fin()
    return Glomerulo(nombre, acciones, des, cab.linea)


def analizar(fuente: str) -> Programa:
    """Analiza el texto fuente completo y devuelve el árbol ``Programa``."""
    toks = tokenizar(fuente)
    lineas: List[List[Token]] = []
    actual: List[Token] = []
    for t in toks:
        if t.tipo == "NL":
            if actual:
                lineas.append(actual)
                actual = []
        elif t.tipo == "FIN":
            break
        else:
            actual.append(t)

    prog = Programa()
    for toks_l in lineas:
        L = _Linea(toks_l)
        cab = L.tomar()
        assert cab is not None
        if cab.tipo == "DIRECTIVA":
            clave, valor = cab.valor
            if clave not in _DIRECTIVAS_VALIDAS:
                raise ErrorSintactico(f"directiva desconocida %{clave}", cab.linea, cab.col)
            L.fin()
            prog.directivas[clave] = (valor, cab.linea)
        elif cab.tipo == "ENGRAMA":
            nom = L.esperar("IDENT", que="un nombre de engrama")
            _validar_nombre(nom.valor, nom)
            inicial = 0
            sig = L.mirar()
            if sig is not None and sig.tipo == "IGUAL":
                L.tomar()
                inicial = L.esperar("NUM", que="un peso sináptico natural").valor
            L.fin()
            if nom.valor in prog.engramas:
                raise ErrorSintactico(f"engrama '{nom.valor}' declarado dos veces",
                                      nom.linea, nom.col)
            prog.engramas[nom.valor] = (inicial, cab.linea)
        elif cab.tipo == "SOPLO":
            g = L.esperar("GLOM", que="un glomérulo (NOMBRE) tras el soplo")
            L.fin()
            prog.soplos.append((g.valor, cab.linea))
        elif cab.tipo == "GLOM":
            glom = _analizar_glomerulo(cab, L)
            if glom.nombre in prog.glomerulos:
                raise ErrorSintactico(f"el glomérulo ({glom.nombre}) ya existe",
                                      cab.linea, cab.col)
            prog.glomerulos[glom.nombre] = glom
        else:
            raise ErrorSintactico(f"una sentencia no puede comenzar con {cab.valor!r}",
                                  cab.linea, cab.col)
    if not prog.glomerulos:
        raise ErrorSintactico("programa sin glomérulos: una mosca sin cerebro no computa", 1)
    return prog


# ─────────────────────────────────────────────────────────────────────────────
# 4. EL CEREBRO — conectoma real FlyWire (o uno sintético de reserva)
# ─────────────────────────────────────────────────────────────────────────────

class Cerebro:
    """Un cerebro de mosca: neuronas identificadas y sinapsis con signo.

    Los pesos se normalizan por la entrada total de cada neurona: la neurona
    postsináptica dispara cuando una fracción ``UMBRAL_NATURAL`` de todas sus
    sinapsis de entrada (excitadoras menos inhibidoras) llega a la vez.
    """

    def __init__(self, nombre: str, ids: np.ndarray, tipo: np.ndarray,
                 superclase: np.ndarray, clase: np.ndarray, lado: np.ndarray,
                 pre: np.ndarray, post: np.ndarray, conteo: np.ndarray,
                 signo: np.ndarray) -> None:
        self.nombre = nombre
        self.ids = np.asarray(ids, dtype=np.int64)
        self.tipo = np.asarray(tipo, dtype=str)
        self.superclase = np.asarray(superclase, dtype=str)
        self.clase = np.asarray(clase, dtype=str)
        self.lado = np.asarray(lado, dtype=str)
        self.pre = np.asarray(pre, dtype=np.int64)
        self.post = np.asarray(post, dtype=np.int64)
        self.conteo = np.asarray(conteo, dtype=np.int64)
        self.signo = np.asarray(signo, dtype=np.int8)
        self.n = len(self.ids)
        w = self.signo.astype(np.float64) * self.conteo
        total = np.bincount(self.post, weights=np.abs(w), minlength=self.n)
        total[total == 0] = 1.0
        self.peso = w / total[self.post]
        orden = np.argsort(self.pre, kind="stable")
        self._pre_s, self._post_s = self.pre[orden], self.post[orden]
        self._peso_s, self._conteo_s = self.peso[orden], self.conteo[orden]
        self._ptr = np.searchsorted(self._pre_s, np.arange(self.n + 1))
        self.region = np.where(self.clase != "", self.clase, self.superclase)
        self.indice = {int(r): i for i, r in enumerate(self.ids)}

    @property
    def n_sinapsis(self) -> int:
        return int(self.conteo.sum())

    def empujar(self, activas: np.ndarray) -> np.ndarray:
        """Entrada natural que reciben todas las neuronas si ``activas`` disparan."""
        if activas.size == 0:
            return np.zeros(self.n)
        ini, fin = self._ptr[activas], self._ptr[activas + 1]
        lon = fin - ini
        total = int(lon.sum())
        if total == 0:
            return np.zeros(self.n)
        idx = np.repeat(ini - (np.cumsum(lon) - lon), lon) + np.arange(total)
        return np.bincount(self._post_s[idx], weights=self._peso_s[idx], minlength=self.n)

    def salientes(self, i: int) -> Tuple[np.ndarray, np.ndarray]:
        a, b = self._ptr[i], self._ptr[i + 1]
        return self._post_s[a:b], self._conteo_s[a:b]

    def entrantes(self, j: int) -> Tuple[np.ndarray, np.ndarray]:
        m = self.post == j
        return self.pre[m], self.conteo[m]

    def conexion(self, i: int, j: int) -> int:
        """Número de sinapsis reales i → j (0 si no existen)."""
        posts, cnt = self.salientes(i)
        hit = cnt[posts == j]
        return int(hit.sum()) if hit.size else 0

    def tipo_visible(self, i: int) -> str:
        if int(self.ids[i]) == MN9_ID:
            return "MN9"
        return self.tipo[i] or self.region[i] or "?"

    def describir(self, i: int) -> str:
        return (f"{self.tipo_visible(i)} · {self.region[i] or '—'} · "
                f"{self.lado[i] or '—'} · id {int(self.ids[i])}")


def _dir_cache() -> Path:
    d = Path(os.environ.get("DROSOPHILA_CACHE",
                            str(Path.home() / ".cache" / "drosophila-lang")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _descargar(url: str, destino: Path, flujo: Optional[TextIO]) -> None:
    if destino.exists():
        return
    tmp = destino.with_name(destino.name + ".parcial")
    if flujo:
        print(f"· descargando {destino.name} …", file=flujo)
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        total, leido = int(r.headers.get("Content-Length") or 0), 0
        while True:
            bloque = r.read(1 << 20)
            if not bloque:
                break
            f.write(bloque)
            leido += len(bloque)
            if flujo and total:
                print(f"\r  {leido / total:6.1%} de {total / 1e6:.0f} MB", end="",
                      file=flujo, flush=True)
    if flujo and total:
        print(file=flujo)
    tmp.replace(destino)


def cargar_flywire(min_sinapsis: int = 5, flujo: Optional[TextIO] = sys.stderr) -> Cerebro:
    """Carga el conectoma real FlyWire v783 (descarga y caché la primera vez)."""
    cache = _dir_cache()
    npz = cache / f"flywire783_min{min_sinapsis}.npz"
    if npz.exists():
        d = np.load(npz)
        return Cerebro("FlyWire v783", d["ids"], d["tipo"], d["superclase"], d["clase"],
                       d["lado"], d["pre"], d["post"], d["conteo"], d["signo"])
    try:
        import pandas as pd
    except ImportError:
        raise DrosophilaError("la primera carga de FlyWire requiere pandas y pyarrow "
                              "(pip install pandas pyarrow)") from None
    rutas = {k: cache / url.rsplit("/", 1)[-1] for k, url in FUENTES_FLYWIRE.items()}
    for k, url in FUENTES_FLYWIRE.items():
        _descargar(url, rutas[k], flujo)
    if flujo:
        print("· construyendo el cerebro (solo la primera vez) …", file=flujo)
    try:
        c = pd.read_parquet(rutas["conectividad"], columns=[
            "Presynaptic_Index", "Postsynaptic_Index", "Connectivity", "Excitatory"])
    except ImportError:
        raise DrosophilaError("leer el conectoma requiere pyarrow (pip install pyarrow)") from None
    c = c[c["Connectivity"] >= min_sinapsis]
    ids = pd.read_csv(rutas["completitud"]).iloc[:, 0].to_numpy(np.int64)
    a = (pd.read_csv(rutas["anotaciones"], sep="\t", low_memory=False,
                     usecols=["root_id", "super_class", "cell_class", "cell_type",
                              "hemibrain_type", "side"])
         .drop_duplicates("root_id").set_index("root_id").reindex(ids))
    tipo = a["cell_type"].fillna(a["hemibrain_type"]).fillna("").astype(str).to_numpy()
    arr = dict(
        ids=ids, tipo=tipo.astype("U"),
        superclase=a["super_class"].fillna("").astype(str).to_numpy().astype("U"),
        clase=a["cell_class"].fillna("").astype(str).to_numpy().astype("U"),
        lado=a["side"].fillna("").astype(str).to_numpy().astype("U"),
        pre=c["Presynaptic_Index"].to_numpy(np.int32),
        post=c["Postsynaptic_Index"].to_numpy(np.int32),
        conteo=c["Connectivity"].to_numpy(np.int32),
        signo=c["Excitatory"].to_numpy(np.int8),
    )
    np.savez_compressed(npz, **arr)
    return Cerebro("FlyWire v783", **arr)


def cerebro_sintetico(semilla: int = 0, n_kc: int = 600) -> Cerebro:
    """Cerebro de reserva, sin descargas, con los mismos tipos celulares que FlyWire."""
    rng = np.random.default_rng(semilla)
    ids: List[int] = []
    tipo: List[str] = []
    sup: List[str] = []
    cla: List[str] = []

    def nuevas(t: str, s: str, c: str, n: int = 1) -> List[int]:
        ini = len(ids)
        for _ in range(n):
            ids.append(720575940000000000 + len(ids))
            tipo.append(t); sup.append(s); cla.append(c)
        return list(range(ini, len(ids)))

    orn = {g: nuevas(f"ORN_{g}", "sensory", "olfactory", 5) for g in GLOMERULOS}
    pn = {g: nuevas(f"{g}_adPN", "central", "ALPN", 1) for g in GLOMERULOS}
    alln = nuevas("ALLN", "central", "ALLN", 60)
    kc = nuevas("KCg-m", "central", "Kenyon_Cell", n_kc)
    mbon = [nuevas(f"MBON{k + 1:02d}", "central", "MBON")[0] for k in range(20)]
    pam = [nuevas(f"PAM{k % 15 + 1:02d}", "central", "DAN")[0] for k in range(30)]
    ppl1 = [nuevas(f"PPL10{k + 1}", "central", "DAN")[0] for k in range(8)]
    lhln = [nuevas(f"LHPV{k}", "central", "LHLN")[0] for k in range(150)]
    motor = [nuevas(f"MNx{k:02d}", "motor", "brain_motor_neuron")[0] for k in range(10)]
    dn = nuevas("DNa02", "descending", "") + nuevas("DNp01", "descending", "")
    mn9 = nuevas("CB0701", "motor", "brain_motor_neuron")[0]
    ids[mn9] = MN9_ID

    pre: List[int] = []; post: List[int] = []; cnt: List[int] = []; sig: List[int] = []

    def conectar(A, B, p, s):
        A, B = np.asarray(A), np.asarray(B)
        m = rng.random((len(A), len(B))) < p
        ia, ib = np.nonzero(m)
        pre.extend(A[ia]); post.extend(B[ib])
        cnt.extend(rng.integers(5, 40, size=len(ia))); sig.extend([s] * len(ia))

    todas_pn = [pn[g][0] for g in GLOMERULOS]
    for g in GLOMERULOS:
        conectar(orn[g], pn[g], 1.0, 1)
    conectar(sum(orn.values(), []), alln, 0.03, 1)
    conectar(alln, todas_pn, 0.1, -1)
    for k in kc:
        for p_ in rng.choice(todas_pn, size=6, replace=False):
            pre.append(int(p_)); post.append(k); cnt.append(int(rng.integers(5, 15))); sig.append(1)
    conectar(todas_pn, lhln, 0.08, 1)
    conectar(kc, mbon, 0.05, 1)
    conectar(pam + ppl1, mbon, 0.15, 1)
    conectar(mbon, lhln, 0.1, 1)
    conectar(lhln, motor + dn + [mn9], 0.05, 1)
    lado = ["right"] * len(ids)
    return Cerebro("sintético", np.array(ids), np.array(tipo), np.array(sup), np.array(cla),
                   np.array(lado), np.array(pre), np.array(post), np.array(cnt), np.array(sig))


def cargar_posiciones(cer: Cerebro, flujo: Optional[TextIO] = sys.stderr) -> np.ndarray:
    """Posición 3D de cada neurona en micrómetros, matriz (n, 3).

    FlyWire: el punto representativo de cada neurona (``pos_x/y/z``, vóxeles de
    4×4×40 nm) de las anotaciones de Schlegel et al. Cerebro sintético: una
    disposición esquemática por regiones.
    """
    if cer.nombre != "FlyWire v783":
        rng = np.random.default_rng(7)
        centros = {"ALPN": (0, 80, 60), "olfactory": (0, 60, 90), "ALLN": (0, 70, 70),
                   "Kenyon_Cell": (0, -60, -40), "MBON": (0, -20, -10), "DAN": (0, -10, -30),
                   "LHLN": (0, 20, -60), "brain_motor_neuron": (0, 160, 0)}
        pos = np.zeros((cer.n, 3), dtype=np.float32)
        for i in range(cer.n):
            cx, cy, cz = centros.get(cer.clase[i], (0, 120, 0))
            lado = 1 if i % 2 else -1
            pos[i] = (lado * (120 + rng.normal(0, 40)) + cx, cy + rng.normal(0, 30),
                      cz + rng.normal(0, 25))
        return pos
    cache = _dir_cache()
    npz = cache / "flywire783_posiciones.npz"
    if npz.exists():
        d = np.load(npz)
        if len(d["ids"]) == cer.n and np.array_equal(d["ids"], cer.ids):
            return d["pos"]
    try:
        import pandas as pd
    except ImportError:
        raise DrosophilaError("leer las posiciones requiere pandas (pip install pandas)") from None
    ruta = cache / FUENTES_FLYWIRE["anotaciones"].rsplit("/", 1)[-1]
    _descargar(FUENTES_FLYWIRE["anotaciones"], ruta, flujo)
    if flujo:
        print("· extrayendo posiciones 3D (solo la primera vez) …", file=flujo)
    a = (pd.read_csv(ruta, sep="\t", low_memory=False, usecols=["root_id", "pos_x", "pos_y", "pos_z"])
         .drop_duplicates("root_id").set_index("root_id").reindex(cer.ids))
    vox = a[["pos_x", "pos_y", "pos_z"]].to_numpy(np.float64)
    falta = np.isnan(vox).any(axis=1)
    if falta.any():
        vox[falta] = np.nanmean(vox[~falta], axis=0)
    pos = (vox * np.array([4.0, 4.0, 40.0]) / 1000.0).astype(np.float32)   # nm → µm
    np.savez_compressed(npz, ids=cer.ids, pos=pos)
    return pos


def cargar_cerebro(tipo: str = "flywire", min_sinapsis: int = 5,
                   flujo: Optional[TextIO] = sys.stderr) -> Cerebro:
    """'flywire' (con reserva automática al sintético si falla) o 'sintetico'."""
    if tipo == "sintetico":
        return cerebro_sintetico()
    try:
        return cargar_flywire(min_sinapsis, flujo)
    except (OSError, DrosophilaError) as e:
        if flujo:
            print(f"· no se pudo cargar FlyWire ({e}); se usa el cerebro sintético", file=flujo)
        return cerebro_sintetico()


# ─────────────────────────────────────────────────────────────────────────────
# 5. RECLUTAMIENTO E IMPLANTE — del texto a neuronas reales
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Implante:
    """El circuito del programa, cableado entre neuronas reales reclutadas."""
    reclutadas: np.ndarray           # índices globales (orden de reclutamiento)
    local: Dict[int, int]            # global → posición en ``reclutadas``
    papel: Dict[int, str]            # global → papel en el programa
    sesgo: np.ndarray                # por neurona reclutada (semientero)
    pre: np.ndarray                  # global
    post_local: np.ndarray           # local
    peso: np.ndarray                 # W(0) del implante
    es_implante: np.ndarray          # máscara global
    engramas: List[str]
    arista_engrama: np.ndarray
    dan_up: np.ndarray
    dan_down: np.ndarray
    orn: np.ndarray
    mn_bits: np.ndarray
    estrobo: int
    mn9: int
    gf: int
    olfato: Dict[int, Tuple[int, int]]
    olfato_idx: np.ndarray
    I0_local: int
    entrada: Dict[str, int]
    inicio: str
    fidelidad: Tuple[int, int]       # (sinapsis del implante que existen en el conectoma, total)


def _repr_byte(b: int) -> str:
    return repr(chr(b)) if 32 <= b < 127 else f"0x{b:02X}"


def _describir(tipo: str, arg: object) -> str:
    return {
        "inc": lambda: f"+{arg}", "dec": lambda: f"-{arg}", "olfato": lambda: f"<{arg}",
        "emitir": lambda: f"!{_repr_byte(arg)}", "espiga": lambda: "!*",
        "prueba": lambda: f"?{arg}", "muerte": lambda: "x_x", "relevo": lambda: "~>",
    }[tipo]()


class _Reclutador:
    def __init__(self, cer: Cerebro) -> None:
        self.c = cer
        self.libre = np.ones(cer.n, dtype=bool)
        self.papel: Dict[int, str] = {}
        self.orden: List[int] = []
        self.sesgo: Dict[int, float] = {}
        self._lado = np.where(cer.lado == "right", 0, 1)
        self.m_kc = cer.clase == "Kenyon_Cell"
        self.m_mbon = cer.clase == "MBON"
        self.m_pam = np.char.startswith(cer.tipo, "PAM")
        self.m_ppl1 = np.char.startswith(cer.tipo, "PPL1")
        self.m_lhln = cer.clase == "LHLN"
        self.m_alln = cer.clase == "ALLN"
        self.m_motor = (cer.superclase == "motor") & (cer.ids != MN9_ID)
        self.pn: Dict[str, List[int]] = defaultdict(list)
        for i in np.flatnonzero(cer.clase == "ALPN"):
            self.pn[cer.tipo[i].split("_")[0]].append(int(i))

    def _ordenar(self, cand: np.ndarray) -> np.ndarray:
        return cand[np.lexsort((self.c.ids[cand], self._lado[cand]))]

    def tomar(self, mascara: np.ndarray, papel: str, que: str,
              preferidas: Sequence[int] = (), sesgo: float = -0.5) -> int:
        pref = [int(p) for p in preferidas if mascara[p] and self.libre[p]]
        if pref:
            i = pref[0]
        else:
            cand = np.flatnonzero(mascara & self.libre)
            if cand.size == 0:
                raise ErrorNeurodesarrollo(
                    f"el cerebro no tiene más neuronas libres de tipo {que} para «{papel}»")
            i = int(self._ordenar(cand)[0])
        self.libre[i] = False
        self.papel[i] = papel
        self.orden.append(i)
        self.sesgo[i] = sesgo
        return i

    def tomar_id(self, root_id: int, papel: str) -> Optional[int]:
        i = self.c.indice.get(root_id)
        if i is None or not self.libre[i]:
            return None
        m = np.zeros(self.c.n, dtype=bool)
        m[i] = True
        return self.tomar(m, papel, "")

    def objetivos(self, i: int, mascara: np.ndarray) -> List[int]:
        """Dianas reales de i dentro de ``mascara``, de más a menos sinapsis."""
        posts, cnt = self.c.salientes(i)
        sel = mascara[posts]
        posts, cnt = posts[sel], cnt[sel]
        return [int(p) for p in posts[np.argsort(-cnt, kind="stable")]]

    def fuentes(self, j: int, mascara: np.ndarray) -> List[int]:
        pres, cnt = self.c.entrantes(j)
        sel = mascara[pres]
        pres, cnt = pres[sel], cnt[sel]
        return [int(p) for p in pres[np.argsort(-cnt, kind="stable")]]

    def tipo_de(self, prefijo: str) -> np.ndarray:
        return self.c.tipo == prefijo


def implantar(prog: Programa, cer: Cerebro) -> Implante:
    """Recluta neuronas reales para cada papel y cablea entre ellas el programa."""
    R = _Reclutador(cer)
    sin_pre: List[int] = []
    sin_post: List[int] = []
    sin_peso: List[float] = []

    def sinapsis(a: int, b: int, w: float = 1.0) -> int:
        sin_pre.append(a); sin_post.append(b); sin_peso.append(float(w))
        return len(sin_pre) - 1

    # I. Engramas: una sinapsis real KC → MBON reescrita por dopamina real.
    nombres_eng = list(prog.engramas)
    for g in prog.glomerulos.values():
        usados = [a.arg for a in g.acciones if a.tipo in ("inc", "dec", "olfato")]
        if g.desenlace.tipo == "prueba":
            usados.append(g.desenlace.engrama)
        for u in usados:
            if u not in nombres_eng:
                nombres_eng.append(u)
    idx_eng = {n: i for i, n in enumerate(nombres_eng)}
    kc_mbon = np.flatnonzero(R.m_kc[cer.pre] & R.m_mbon[cer.post])
    kc_mbon = kc_mbon[np.argsort(-cer.conteo[kc_mbon], kind="stable")]
    kc_e, mbon, up, down, orn, arista = [], [], [], [], [], []
    for nom in nombres_eng:
        par = next(((int(cer.pre[e]), int(cer.post[e])) for e in kc_mbon
                    if R.libre[cer.pre[e]] and R.libre[cer.post[e]]), None)
        k = R.tomar(R.m_kc, f"KC·{nom}", "célula de Kenyon", [par[0]] if par else ())
        m = R.tomar(R.m_mbon, f"MBON·{nom}", "MBON", [par[1]] if par else ())
        p = R.tomar(R.m_pam, f"PAM·{nom}", "dopaminérgica PAM", R.fuentes(m, R.m_pam))
        q = R.tomar(R.m_ppl1, f"PPL1·{nom}", "dopaminérgica PPL1", R.fuentes(m, R.m_ppl1))
        gl = nom if nom in GLOMERULOS else GLOMERULOS[
            int(hashlib.sha256(nom.encode()).hexdigest(), 16) % len(GLOMERULOS)]
        o = R.tomar(R.tipo_de(f"ORN_{gl}"), f"ORN·{nom}", f"receptor olfativo ORN_{gl}")
        w0 = prog.engramas.get(nom, (0, 0))[0]
        arista.append(sinapsis(k, m, float(w0)))
        sinapsis(o, p)
        kc_e.append(k); mbon.append(m); up.append(p); down.append(q); orn.append(o)

    # II. Efectores: motoneuronas, DNa02, MN9, Fibra Gigante.
    mn_bits = [R.tomar(R.m_motor, f"bit{j}", "motoneurona") for j in range(8)]
    estrobo = R.tomar(R.tipo_de("DNa02"), "estrobo", "DNa02")
    mn9 = R.tomar_id(MN9_ID, "probóscide")
    if mn9 is None:
        mn9 = R.tomar(R.m_motor, "probóscide", "motoneurona")
    gf = R.tomar(R.tipo_de("DNp01"), "muerte", "DNp01 (Fibra Gigante)")

    # III. Control: cada glomérulo arranca en su neurona de proyección real.
    micro: Dict[str, List[tuple]] = {}
    neur: Dict[str, List[int]] = {}
    for nombre, g in prog.glomerulos.items():
        ops: List[tuple] = []
        for a in g.acciones:
            if a.tipo == "emitir":
                ops.extend(("emitir", b, a) for b in a.arg)
            else:
                ops.append((a.tipo, a.arg, a))
        d = g.desenlace
        if d.tipo == "prueba":
            ops.append(("prueba", d.engrama, d))
        elif d.tipo == "muerte":
            ops.append(("muerte", None, d))
        elif not ops:
            ops.append(("relevo", d.destino, d))
        micro[nombre] = ops
        ns: List[int] = []
        for k, (t, a, _) in enumerate(ops):
            papel = f"{nombre}·{k}[{_describir(t, a)}]"
            if k == 0:
                libres_pn = [i for i in R.pn.get(nombre, []) if R.libre[i]]
                if libres_pn:
                    m = np.zeros(cer.n, dtype=bool)
                    m[libres_pn] = True
                    ns.append(R.tomar(m, papel, "neurona de proyección"))
                    continue
                ns.append(R.tomar(R.m_kc, papel, "célula de Kenyon"))
            else:
                ns.append(R.tomar(R.m_kc, papel, "célula de Kenyon",
                                  R.objetivos(ns[0], R.m_kc)))
        neur[nombre] = ns
    entrada = {n: v[0] for n, v in neur.items()}

    def destino(nombre: str, nodo) -> int:
        if nombre not in entrada:
            raise ErrorNeurodesarrollo(f"el glomérulo ({nombre}) no existe en este programa",
                                       nodo.linea, nodo.col)
        return entrada[nombre]

    olfato: Dict[int, Tuple[int, int]] = {}
    for nombre, g in prog.glomerulos.items():
        ops, ns = micro[nombre], neur[nombre]
        for k, (tipo, arg, nodo) in enumerate(ops):
            c = ns[k]
            if k + 1 < len(ops):
                sig: Optional[int] = ns[k + 1]
            elif g.desenlace.tipo == "ir":
                sig = destino(g.desenlace.destino, g.desenlace)
            else:
                sig = None
            if tipo == "inc":
                sinapsis(c, up[idx_eng[arg]]); sinapsis(c, sig)
            elif tipo == "dec":
                sinapsis(c, down[idx_eng[arg]]); sinapsis(c, sig)
            elif tipo == "emitir":
                for j in range(8):
                    if (arg >> j) & 1:
                        sinapsis(c, mn_bits[j])
                sinapsis(c, estrobo); sinapsis(c, sig)
            elif tipo == "espiga":
                sinapsis(c, mn9); sinapsis(c, sig)
            elif tipo == "olfato":
                r = idx_eng[arg]
                ret = R.tomar(R.m_alln, f"{nombre}·{k}·retorno", "neurona local del lóbulo antenal",
                              R.objetivos(orn[r], R.m_alln))
                olfato[c] = (r, ret)
                sinapsis(ret, sig)
            elif tipo == "relevo":
                sinapsis(c, destino(arg, nodo))
            elif tipo == "muerte":
                sinapsis(c, gf)
            elif tipo == "prueba":
                r = idx_eng[arg]
                a1 = R.tomar(R.m_lhln, f"{nombre}·retardo¹", "neurona local del cuerno lateral",
                             R.objetivos(c, R.m_lhln))
                a2 = R.tomar(R.m_lhln, f"{nombre}·retardo²", "neurona local del cuerno lateral",
                             R.objetivos(a1, R.m_lhln))
                gnz = R.tomar(R.m_lhln, f"{nombre}·sí→({nodo.destino})",
                              "neurona local del cuerno lateral", R.objetivos(mbon[r], R.m_lhln), -1.5)
                gz = R.tomar(R.m_lhln, f"{nombre}·no→({nodo.alterno})",
                             "neurona local del cuerno lateral", R.objetivos(mbon[r], R.m_lhln), -0.5)
                sinapsis(c, kc_e[r]); sinapsis(c, a1); sinapsis(a1, a2)
                sinapsis(a2, gnz); sinapsis(a2, gz)
                sinapsis(mbon[r], gnz, 1.0); sinapsis(mbon[r], gz, -1.0)
                sinapsis(gnz, down[r]); sinapsis(gnz, destino(nodo.destino, nodo))
                sinapsis(gz, destino(nodo.alterno, nodo))

    # IV. El soplo inicial I(0).
    if len(prog.soplos) > 1:
        raise ErrorNeurodesarrollo("más de un soplo inicial: el foco atencional debe ser único",
                                   prog.soplos[1][1])
    inicio = prog.soplos[0][0] if prog.soplos else next(iter(prog.glomerulos))
    if inicio not in entrada:
        raise ErrorNeurodesarrollo(f"el soplo apunta a un glomérulo inexistente ({inicio})",
                                   prog.soplos[0][1] if prog.soplos else None)

    rec = np.array(R.orden, dtype=np.int64)
    local = {int(g): i for i, g in enumerate(rec)}
    es_imp = np.zeros(cer.n, dtype=bool)
    es_imp[rec] = True
    pares = set(zip(sin_pre, sin_post))
    reales = sum(1 for a, b in pares if cer.conexion(a, b) > 0)
    return Implante(
        reclutadas=rec, local=local, papel=R.papel,
        sesgo=np.array([R.sesgo[int(i)] for i in rec]),
        pre=np.array(sin_pre, dtype=np.int64),
        post_local=np.array([local[b] for b in sin_post], dtype=np.int64),
        peso=np.array(sin_peso), es_implante=es_imp,
        engramas=nombres_eng, arista_engrama=np.array(arista, dtype=np.int64),
        dan_up=np.array(up, dtype=np.int64), dan_down=np.array(down, dtype=np.int64),
        orn=np.array(orn, dtype=np.int64), mn_bits=np.array(mn_bits, dtype=np.int64),
        estrobo=estrobo, mn9=mn9, gf=gf, olfato=olfato,
        olfato_idx=np.array(sorted(olfato), dtype=np.int64),
        I0_local=local[entrada[inicio]], entrada=entrada, inicio=inicio,
        fidelidad=(reales, len(pares)),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. LA MÁQUINA VIRTUAL — la vida de la mosca
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Resultado:
    salida: bytes
    pasos: int
    causa: str                     # 'muerte' | 'coma' | 'ciclo' | 'agotamiento'
    engramas: Dict[str, int]
    espigas_mn9: int
    neuronas_despertadas: int      # neuronas naturales que dispararon al menos una vez
    periodo: Optional[int] = None

    @property
    def texto(self) -> str:
        return self.salida.decode("utf-8", errors="replace")


class DrosophilaVM:
    """Máquina virtual de Drosophila-Lang sobre un cerebro de mosca real.

    Ejemplo::

        vm = DrosophilaVM("(DA1) !'F' x_x")          # carga FlyWire (caché)
        print(vm.ejecutar().texto)                   # → F

    Parámetros
    ----------
    fuente : str               código Drosophila-Lang.
    cerebro : Cerebro | str    un ``Cerebro`` ya cargado, 'flywire' o 'sintetico'.
    cepa : str                 semilla del ruido neuronal. Anula ``%cepa``.
    temperatura : float        T = 1/β de las neuronas del implante (0 = exactas).
    """

    def __init__(self, fuente: str, *, cerebro: "Cerebro | str" = "flywire",
                 cepa: Optional[str] = None, temperatura: Optional[float] = None,
                 eta: float = ETA, min_sinapsis: int = 5,
                 flujo_info: Optional[TextIO] = sys.stderr) -> None:
        self.fuente = fuente
        self.programa = analizar(fuente)
        d = self.programa.directivas
        self.cepa = cepa if cepa is not None else d.get("cepa", (CEPA_POR_DEFECTO, 0))[0]
        if temperatura is None:
            if "temperatura" in d:
                v, ln = d["temperatura"]
                try:
                    temperatura = float(v)
                except ValueError:
                    raise ErrorSintactico(f"temperatura no numérica {v!r}", ln) from None
            else:
                temperatura = TEMPERATURA_POR_DEFECTO
        if not math.isfinite(temperatura) or temperatura < 0:
            raise DrosophilaError("la temperatura debe ser un real finito ≥ 0")
        self.temperatura = temperatura
        self.beta = math.inf if temperatura == 0 else 1.0 / temperatura
        self.eta = eta
        self.semilla = int.from_bytes(hashlib.sha256(self.cepa.encode()).digest()[:8], "little")
        self.cerebro = (cerebro if isinstance(cerebro, Cerebro)
                        else cargar_cerebro(cerebro, min_sinapsis, flujo_info))
        self.implante = implantar(self.programa, self.cerebro)

    @classmethod
    def desde_archivo(cls, ruta: str, **kw) -> "DrosophilaVM":
        with open(ruta, encoding="utf-8") as f:
            return cls(f.read(), **kw)

    # -- utilidades -----------------------------------------------------------
    def etiqueta(self, i: int) -> str:
        return f"{self.implante.papel[i]}⟨{self.cerebro.tipo_visible(i)}⟩"

    def reparto(self) -> List[Tuple[str, str]]:
        """Qué neurona real interpreta cada papel del programa."""
        return [(self.implante.papel[int(i)], self.cerebro.describir(int(i)))
                for i in self.implante.reclutadas]

    def _disparar(self, h: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        if math.isinf(self.beta):
            return h > 0.0
        p = 0.5 * (1.0 + np.tanh(0.5 * self.beta * h))
        return rng.random(h.shape[0]) < p

    def _trazar(self, flujo: TextIO, t: int, S: np.ndarray, W: np.ndarray) -> None:
        P, C = self.implante, self.cerebro
        act = [self.etiqueta(int(i)) for i in P.reclutadas if S[i]]
        pesos = " ".join(f"{n}={int(W[e])}" for n, e in zip(P.engramas, P.arista_engrama))
        nat = np.flatnonzero(S & ~P.es_implante)
        top = Counter(C.region[nat]).most_common(3)
        linea = f"t={t:>5} │ {' · '.join(act) if act else '∅'}"
        if pesos:
            linea += f" │ W: {pesos}"
        linea += f" │ cerebro: {len(nat)}"
        if top:
            linea += " (" + ", ".join(f"{r or '?'} {k}" for r, k in top) + ")"
        print(linea, file=flujo)

    # -- ejecución ------------------------------------------------------------
    def ejecutar(self, olor: Sequence[int] = (), *, max_pasos: int = 100_000,
                 detectar_atractores: bool = True, traza: Optional[TextIO] = None,
                 flujo_salida: Optional[Callable[[bytes], None]] = None,
                 observador: Optional[Callable[[int, np.ndarray, np.ndarray], None]] = None) -> Resultado:
        """Da vida a la mosca desde el silencio, con el soplo I(0), hasta su muerte.

        ``observador(t, S, pesos)`` se llama tras cada paso con el vector de
        disparo completo (todas las neuronas del cerebro) y los pesos actuales
        de los engramas; lo usan el visor 3D y el modo en vivo. Si el
        observador lanza una excepción, la ejecución se interrumpe.
        """
        C, P = self.cerebro, self.implante
        N = C.n
        olor = [int(x) for x in olor]
        if any(x < 0 for x in olor):
            raise DrosophilaError("una concentración odorante no puede ser negativa")
        rng = np.random.default_rng(self.semilla ^ 0x9E3779B97F4A7C15)
        rec = P.reclutadas
        natural = ~P.es_implante
        S = np.zeros(N, dtype=bool)
        v = np.zeros(N)
        refr = np.zeros(N, dtype=np.int16)
        alguna_vez = np.zeros(N, dtype=bool)
        W = P.peso.copy()
        ptr = 0
        agenda: Dict[int, List[int]] = defaultdict(list)
        salida = bytearray()
        pendiente, total_mn9 = 0, 0
        vistos: Dict[tuple, int] = {}
        t, causa, periodo = 0, "agotamiento", None

        def emitir(bs: bytes) -> None:
            salida.extend(bs)
            if flujo_salida is not None:
                flujo_salida(bs)

        if traza is not None:
            print(f"t={0:>5} │ I(0): soplo sobre ({P.inicio}) = "
                  f"{C.describir(P.entrada[P.inicio])} │ cerebro {C.nombre}: {N} neuronas, "
                  f"{len(C.pre)} conexiones │ implante: {len(rec)} neuronas reclutadas", file=traza)

        while t < max_pasos:
            activas = np.flatnonzero(S)
            entrada_natural = C.empujar(activas)
            # (a) Neuronas del implante: obedecen al programa; el cerebro les susurra.
            I = np.zeros(len(rec))
            if t == 0:
                I[P.I0_local] += 1.0
            for g in agenda.pop(t, ()):
                I[P.local[g]] += 1.0
            h = (np.bincount(P.post_local, weights=W * S[P.pre], minlength=len(rec))
                 + P.sesgo + I + SUSURRO * entrada_natural[rec])
            S_rec = self._disparar(h, rng)
            # (b) Resto del cerebro: integrar y disparar, con fuga y refractario.
            v = FUGA_NATURAL * v + entrada_natural
            bloqueadas = refr > 0
            v[bloqueadas] = 0.0
            refr[bloqueadas] -= 1
            S_sig = (v >= UMBRAL_NATURAL) & natural
            v[S_sig] = 0.0
            refr[S_sig] = REFRACTARIO
            v[rec] = 0.0
            S_sig[rec] = S_rec
            # (c) Dopamina: PAM escribe (+1) y PPL1 borra (−1) en la sinapsis del engrama.
            if len(P.arista_engrama):
                delta = S[P.dan_up].astype(np.float64) - S[P.dan_down].astype(np.float64)
                if delta.any():
                    W[P.arista_engrama] = np.maximum(0.0, W[P.arista_engrama] + self.eta * delta)
            S = S_sig
            t += 1
            alguna_vez |= S

            # Lectura motora: bits + estrobo DNa02 → byte;  MN9 → conteo.
            if S[P.estrobo]:
                if pendiente:
                    emitir(str(pendiente).encode("ascii"))
                    pendiente = 0
                byte = 0
                for j, i in enumerate(P.mn_bits):
                    if S[i]:
                        byte |= 1 << j
                emitir(bytes([byte]))
            if S[P.mn9]:
                pendiente += 1
                total_mn9 += 1

            # Percepción: la antena entrega una ráfaga de c espigas al ORN.
            if len(P.olfato_idx):
                for c in P.olfato_idx[S[P.olfato_idx]]:
                    r, ret = P.olfato[int(c)]
                    n = olor[ptr] if ptr < len(olor) else 0
                    ptr = min(ptr + 1, len(olor))
                    for k in range(1, n + 1):
                        agenda[t + k].append(int(P.orn[r]))
                    agenda[t + n + 2].append(ret)

            if traza is not None:
                self._trazar(traza, t, S, W)
            if observador is not None:
                observador(t, S, W[P.arista_engrama].copy())

            if S[P.gf]:
                causa = "muerte"
                break
            if detectar_atractores:
                estado = S[rec]
                if not estado.any() and not agenda:
                    causa = "coma"
                    break
                clave = (np.packbits(estado).tobytes(), W[P.arista_engrama].tobytes(), ptr,
                         tuple(sorted((dt - t, tuple(x)) for dt, x in agenda.items())))
                if clave in vistos:
                    causa, periodo = "ciclo", t - vistos[clave]
                    break
                vistos[clave] = t

        if pendiente:
            emitir(str(pendiente).encode("ascii"))
        engramas = {n: int(W[e]) for n, e in zip(P.engramas, P.arista_engrama)}
        despertadas = int((alguna_vez & natural).sum())
        return Resultado(bytes(salida), t, causa, engramas, total_mn9, despertadas, periodo)


# ─────────────────────────────────────────────────────────────────────────────
# 7. HERRAMIENTAS: puertas lógicas y anatomía
# ─────────────────────────────────────────────────────────────────────────────

def demostrar_puertas(temperatura: float = TEMPERATURA_POR_DEFECTO, ensayos: int = 10_000,
                      semilla: int = 1118, flujo: TextIO = sys.stdout) -> int:
    """Verifica las puertas de umbral estocásticas. Devuelve el nº de errores."""
    rng = np.random.default_rng(semilla)
    beta = math.inf if temperatura == 0 else 1.0 / temperatura

    def neurona(h: np.ndarray) -> np.ndarray:
        if math.isinf(beta):
            return h > 0
        return rng.random(h.shape) < 0.5 * (1 + np.tanh(0.5 * beta * h))

    def xor(x, y):
        o, n = neurona(x + y - 0.5), neurona(-x - y + 1.5)
        return neurona(o.astype(float) + n - 1.5)

    puertas = [
        ("Y    · célula de Kenyon (coincidencia)", lambda x, y: neurona(x + y - 1.5), lambda x, y: x & y),
        ("O    · MBON excitada",                   lambda x, y: neurona(x + y - 0.5), lambda x, y: x | y),
        ("NO   · inhibición GABA (APL)",           lambda x, y: neurona(-x + 0.5),    lambda x, y: 1 - x),
        ("NO-Y · MBON tónica inhibida",            lambda x, y: neurona(-x - y + 1.5), lambda x, y: 1 - (x & y)),
        ("XOR  · dos capas",                       xor,                                lambda x, y: x ^ y),
    ]
    print(f"Puertas lógicas del cuerpo en champiñón · T = {temperatura:g} · "
          f"{ensayos} ensayos por fila\n", file=flujo)
    print(f"{'puerta':<40} 00 01 10 11   errores", file=flujo)
    total = 0
    for nombre, f, verdad in puertas:
        fila, err = [], 0
        for x in (0, 1):
            for y in (0, 1):
                out = f(np.full(ensayos, float(x)), np.full(ensayos, float(y)))
                err += int((out != bool(verdad(x, y))).sum())
                fila.append(str(int(round(out.mean()))))
        total += err
        print(f"{nombre:<40}  {'  '.join(fila)}   {err}", file=flujo)
    print("\nNO-Y es universal: con ella se construye cualquier circuito lógico.", file=flujo)
    return total


def informe_anatomico(vm: DrosophilaVM, flujo: TextIO = sys.stdout) -> None:
    C, P = vm.cerebro, vm.implante
    print(f"Cerebro {C.nombre}: {C.n} neuronas · {len(C.pre)} conexiones · "
          f"{C.n_sinapsis} sinapsis", file=flujo)
    for reg, n in Counter(C.superclase).most_common():
        print(f"  {n:>7}  {reg or '(sin anotar)'}", file=flujo)
    reales, total = P.fidelidad
    print(f"\nImplante: {len(P.reclutadas)} neuronas reclutadas · {total} sinapsis del programa, "
          f"de las cuales {reales} ya existían en el conectoma real "
          f"({reales / max(total, 1):.0%} de fidelidad anatómica)", file=flujo)


# ─────────────────────────────────────────────────────────────────────────────
# 8. INTERFAZ DE LÍNEA DE ÓRDENES
# ─────────────────────────────────────────────────────────────────────────────

_CAUSAS = {
    "muerte": "disparó la Fibra Gigante — muerte temporal",
    "coma": "coma: el programa cayó en silencio",
    "ciclo": "ciclo: el programa repite para siempre un bucle de {p} pasos",
    "agotamiento": "agotamiento: se alcanzó el máximo de pasos",
}


def _epitafio(vm: DrosophilaVM, res: Resultado) -> str:
    causa = _CAUSAS[res.causa].format(p=res.periodo)
    eng = ", ".join(f"{k}={v}" for k, v in res.engramas.items()) or "∅"
    sep = "\n" if res.salida and not res.salida.endswith(b"\n") else ""
    return (f"{sep}✝ {vm.cerebro.nombre} · t = {res.pasos} · {causa} · engramas: {eng} · "
            f"{res.neuronas_despertadas} neuronas del cerebro despertaron en su vida")


def _leer_olor(texto: Optional[str], texto_bytes: Optional[str]) -> List[int]:
    vals: List[int] = []
    if texto:
        try:
            vals += [int(x) for x in re.split(r"[\s,;]+", texto.strip()) if x]
        except ValueError:
            raise DrosophilaError(f"--olor admite solo naturales, se recibió {texto!r}") from None
    if texto_bytes is not None:
        vals += list(texto_bytes.encode("utf-8"))
    return vals


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        sys.stderr.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(
        prog="drosophila",
        description="Drosophila-Lang: programas que se ejecutan en el cerebro real de una mosca.")
    ap.add_argument("programa", nargs="?", help="archivo fuente .dros")
    ap.add_argument("-e", "--evaluar", metavar="CÓDIGO", help="ejecuta código en línea")
    ap.add_argument("--olor", help="concentraciones de entrada, p. ej. \"3 4\"")
    ap.add_argument("--olor-texto", help="entrada como texto (cada byte es una concentración)")
    ap.add_argument("--cerebro", choices=["flywire", "sintetico"], default="flywire")
    ap.add_argument("--sinapsis-min", type=int, default=5,
                    help="conexiones con al menos este nº de sinapsis (FlyWire)")
    ap.add_argument("--cepa", help="semilla del ruido neuronal")
    ap.add_argument("--temperatura", type=float, help="T = 1/β (0 = neuronas exactas)")
    ap.add_argument("--max-pasos", type=int, default=100_000)
    ap.add_argument("--traza", action="store_true", help="actividad neuronal paso a paso (stderr)")
    ap.add_argument("--reparto", action="store_true", help="qué neurona real hace cada papel")
    ap.add_argument("--sin-atractores", action="store_true", help="no detener en bucles")
    ap.add_argument("--puertas", action="store_true", help="demuestra las puertas lógicas")
    ap.add_argument("--anatomia", action="store_true", help="informe del cerebro y termina")
    ap.add_argument("-q", "--silencio", action="store_true", help="omite el epitafio")
    ap.add_argument("--version", action="version", version=f"Drosophila-Lang {__version__}")
    a = ap.parse_args(argv)

    if a.puertas:
        t = TEMPERATURA_POR_DEFECTO if a.temperatura is None else a.temperatura
        return 0 if demostrar_puertas(t) == 0 else 2
    if a.evaluar is None and a.programa is None:
        ap.error("indique un archivo .dros o use -e CÓDIGO")

    try:
        if a.evaluar is not None:
            fuente = a.evaluar
        else:
            with open(a.programa, encoding="utf-8") as f:
                fuente = f.read()
        vm = DrosophilaVM(fuente, cerebro=a.cerebro, cepa=a.cepa, temperatura=a.temperatura,
                          min_sinapsis=a.sinapsis_min)
        if a.reparto:
            ancho = max(len(p) for p, _ in vm.reparto())
            print("Reparto (papel → neurona real):", file=sys.stderr)
            for papel, quien in vm.reparto():
                print(f"  {papel:<{ancho}}  →  {quien}", file=sys.stderr)
        if a.anatomia:
            informe_anatomico(vm)
            return 0
        olor = _leer_olor(a.olor, a.olor_texto)
        out = sys.stdout.buffer

        def flujo(b: bytes) -> None:
            out.write(b)
            out.flush()

        res = vm.ejecutar(olor, max_pasos=a.max_pasos,
                          detectar_atractores=not a.sin_atractores,
                          traza=sys.stderr if a.traza else None, flujo_salida=flujo)
    except DrosophilaError as e:
        print(f"drosophila: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"drosophila: {e}", file=sys.stderr)
        return 1
    if not a.silencio:
        print(_epitafio(vm, res), file=sys.stderr)
    return 3 if res.causa == "agotamiento" else 0


if __name__ == "__main__":
    sys.exit(main())
