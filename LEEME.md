# Drosophila-Lang

Lenguaje de programación esotérico que se ejecuta sobre el cerebro real de una
mosca de la fruta (conectoma FlyWire v783, 138 639 neuronas).

## Instalación (macOS, una sola vez)

    cd ~/Documents/repos/drosophila-lang
    bash instalar.sh

El script crea un entorno virtual (`.venv`), instala numpy, pandas y pyarrow,
descarga el cerebro (~135 MB, se guarda en `~/.cache/drosophila-lang`) y hace
dos pruebas.

## Uso

    ./mosca.sh efe.dros                          # → F
    ./mosca.sh hola.dros                         # → ¡Hola, Mundo!
    ./mosca.sh suma.dros --olor "3 4"            # → suma: 7
    ./mosca.sh longitud.dros --olor-texto "mosca" # → bytes: 5
    ./mosca.sh suma.dros --olor "2 1" --traza    # actividad paso a paso
    ./mosca.sh suma.dros --reparto               # qué neurona real hace cada papel
    ./mosca.sh suma.dros --anatomia              # informe del cerebro
    ./mosca.sh --puertas                         # puertas lógicas
    ./mosca.sh -e "(DA1) !'F' x_x"               # código en línea

## Comprobar la completitud de Turing

    source .venv/bin/activate
    python minsky.py                             # máquinas de Minsky aleatorias

Traduce máquinas de Minsky aleatorias a Drosophila-Lang, las ejecuta en el
cerebro real y compara los registros finales con una simulación directa en
Python. Debe terminar con «N/N máquinas … coinciden».

## Visor 3D

    source .venv/bin/activate
    python visor3d.py suma.dros --olor "3 4"     # crea suma_3d.html y lo abre

Muestra las 138 639 neuronas en su posición real y cómo se encienden en cada
paso. Arrastra para girar, rueda o pellizco para acercar, barra espaciadora
para reproducir y flechas para avanzar paso a paso.

## En vivo (tiempo real)

    source .venv/bin/activate
    python envivo.py                             # abre http://127.0.0.1:8765

Escribe o elige un programa en el navegador y pulsa «Ejecutar en vivo»: la
simulación corre mientras la miras. Puedes cambiar la velocidad (hasta ~1 ms
por paso, «tiempo biológico»), pausar, detener y revisar pasos anteriores en la
línea de tiempo. Para cerrar el servidor: Ctrl + C en la Terminal.

## Alta resolución (en los dos visores, panel «Alta resolución»)

- Calidad en pantalla: Estándar, Retina (2×) o Supermuestreo (3×).
- Tamaño de los puntos: más pequeños para ver cada neurona por separado.
- Guardar imagen: PNG en 4K (3840 × 2160) u 8K (7680 × 4320), con título,
  paso, etiquetas de las neuronas activas y barra de escala.
- Grabar vídeo: MP4 o WebM a la resolución de dibujo actual.

## Contenido

- `drosophila.py` — intérprete
- `visor3d.py` — visor 3D de la actividad neuronal
- `envivo.py` — simulación en tiempo real en el navegador
- `efe.dros`, `hola.dros`, `suma.dros`, `longitud.dros` — ejemplos
- `minsky.py` — comprobación empírica de la traducción de máquinas de Minsky
- `instalar.sh`, `mosca.sh` — instalación y ejecución
- `articulo/` — artículo en LaTeX y PDF

## Desinstalar

    rm -rf .venv ~/.cache/drosophila-lang

Datos: FlyWire (Dorkenwald et al., Nature 2024; Schlegel et al., Nature 2024)
y Shiu et al. (Nature 2024).
