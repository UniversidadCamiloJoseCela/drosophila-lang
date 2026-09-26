#!/usr/bin/env bash
# ============================================================================
#  instalar.sh — prepara Drosophila-Lang en macOS (Apple Silicon)
#  y descarga el cerebro real de la mosca (conectoma FlyWire v783).
#
#  Uso:   bash instalar.sh
#  Se puede ejecutar varias veces sin problema: lo ya hecho no se repite.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo "== Drosophila-Lang · instalación =="

# 1. Buscar un Python 3 adecuado (el más reciente disponible) -----------------
PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "✗ No encuentro Python 3. Instálalo con Homebrew (brew install python) o desde python.org."
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "✗ Se necesita Python 3.9 o superior; tienes $("$PY" --version)."
  exit 1
fi
VER=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
ARCH=$("$PY" -c 'import platform; print(platform.machine())')
echo "· Python: $("$PY" --version) ($ARCH)"
if [ "$(uname -s)" = "Darwin" ] && [ "$ARCH" != "arm64" ]; then
  echo "  ⚠ Este Python es de Intel (Rosetta). Funcionará, pero es más lento;"
  echo "    lo ideal es un Python arm64 (brew install python)."
fi

# 2. Certificados SSL del Python de python.org (si existe su instalador) -------
CERT="/Applications/Python $VER/Install Certificates.command"
if [ -f "$CERT" ]; then
  echo "· Instalando certificados SSL de Python $VER"
  sh "$CERT" >/dev/null 2>&1 || echo "  (no se pudieron instalar; se usará certifi)"
fi

# 3. Entorno virtual y dependencias --------------------------------------------
if [ ! -d .venv ]; then
  echo "· Creando entorno virtual en .venv"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "· Instalando numpy, pandas, pyarrow y certifi (puede tardar un minuto)"
python -m pip install --upgrade pip -q
python -m pip install -q numpy pandas pyarrow certifi
SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
export SSL_CERT_FILE

# 4. Descargar y construir el cerebro ------------------------------------------
echo "· Descargando y construyendo el cerebro de la mosca (≈135 MB, solo la primera vez)"
python - << 'PYEOF'
import sys
import drosophila as d
try:
    c = d.cargar_flywire()
except Exception as e:
    print(f"✗ No se pudo obtener el conectoma: {e}")
    print("  Revisa tu conexión a internet y vuelve a ejecutar: bash instalar.sh")
    sys.exit(1)
print(f"· Cerebro {c.nombre}: {c.n} neuronas · {len(c.pre)} conexiones · {c.n_sinapsis} sinapsis")
print(f"  Guardado en {d._dir_cache()}")
PYEOF

# 5. Pruebas ---------------------------------------------------------------------
echo "· Prueba 1 (debe imprimir F):"
python drosophila.py efe.dros -q
echo
echo "· Prueba 2 (debe imprimir suma: 7):"
python drosophila.py suma.dros --olor "3 4" -q

chmod +x mosca.sh 2>/dev/null || true
echo
echo "== Listo. Para ejecutar programas: =="
echo "   ./mosca.sh efe.dros"
echo "   ./mosca.sh suma.dros --olor \"3 4\" --traza"
echo "   ./mosca.sh hola.dros --reparto"
