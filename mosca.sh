#!/usr/bin/env bash
# ============================================================================
#  mosca.sh — ejecuta un programa Drosophila-Lang en el cerebro real.
#
#  Ejemplos:
#     ./mosca.sh efe.dros
#     ./mosca.sh suma.dros --olor "3 4"
#     ./mosca.sh suma.dros --olor "2 1" --traza --reparto
#     ./mosca.sh -e "(DA1) !\"Hola\n\" x_x"
#     ./mosca.sh --puertas
#     ./mosca.sh --help
# ============================================================================
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
if [ ! -d "$DIR/.venv" ]; then
  echo "Primero hay que instalar: bash \"$DIR/instalar.sh\""
  exit 1
fi
# shellcheck disable=SC1091
source "$DIR/.venv/bin/activate"
export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
exec python "$DIR/drosophila.py" "$@"
