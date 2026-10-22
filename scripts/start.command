#!/bin/bash
# Avvia Signal Desk con un doppio click dal Finder.
# Prepara automaticamente l'ambiente Python al primo avvio, poi apre il server
# locale e il browser. Se il server è già attivo, apre solo il browser.

set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR" || exit 1

BASE_PORT=8000
PORT_ATTEMPTS=20
VENV_DIR="$DIR/.venv"
PYTHON="$VENV_DIR/bin/python"
REQUIREMENTS="$DIR/requirements.txt"
REQUIREMENTS_MARKER="$VENV_DIR/.requirements-installed"

# Cerca una nostra istanza già attiva. La porta 8000 è affollata (Django,
# python -m http.server, vari tool), quindi non basta che "qualcuno" risponda:
# controlliamo che la risposta sia davvero di Signal Desk, altrimenti
# aprireremmo il browser sull'applicazione di qualcun altro.
find_running_url() {
  local port
  for ((port = BASE_PORT; port < BASE_PORT + PORT_ATTEMPTS; port++)); do
    if curl -s --max-time 1 "http://localhost:$port/api/status" 2>/dev/null \
        | grep -q '"app": *"signal-desk"'; then
      echo "http://localhost:$port"
      return 0
    fi
  done
  return 1
}

if RUNNING_URL="$(find_running_url)"; then
  echo "Signal Desk è già attivo. Apro il browser…"
  open "$RUNNING_URL"
  exit 0
fi

# Il doppio clic dal Finder non eredita un virtual environment attivo. Crealo e
# popolalo qui, così anche chi non usa il terminale può avviare l'app.
if [ ! -x "$PYTHON" ]; then
  echo "Preparo Signal Desk per il primo avvio…"
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 non è installato. Installalo da https://www.python.org/downloads/ e riapri Signal Desk."
    read -r -p "Premi Invio per chiudere…"
    exit 1
  fi
  python3 -m venv "$VENV_DIR"
fi

if [ ! -f "$REQUIREMENTS_MARKER" ] || [ "$REQUIREMENTS" -nt "$REQUIREMENTS_MARKER" ]; then
  echo "Installo o aggiorno i componenti necessari…"
  "$PYTHON" -m pip install --disable-pip-version-check -r "$REQUIREMENTS"
  touch "$REQUIREMENTS_MARKER"
fi

# Apri il browser appena il server è pronto (in background). Il server sceglie
# da solo la prima porta libera, quindi qui la cerchiamo invece di darla per
# scontata — e sempre riconoscendo Signal Desk, non un'app qualunque.
( for _ in $(seq 1 40); do
    if URL="$(find_running_url)"; then
      open "$URL"; break
    fi
    sleep 0.5
  done ) &

echo "Avvio Signal Desk…  (chiudi questa finestra o premi Ctrl+C per fermare)"
exec "$PYTHON" -m src.main serve
