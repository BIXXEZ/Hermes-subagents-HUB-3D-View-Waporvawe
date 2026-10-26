#!/usr/bin/env bash
# office_hermes — установка. Windows (git-bash/MSYS/Cygwin) → вызывает install.ps1,
# Linux/macOS → раскладывает файлы сам и печатает команду запуска.
#
#   bash scripts/install.sh [--port 8797] [--home ~/.hermes] [--no-start] [--start]
#
# На Windows: bash scripts/install.sh            (то же, что install.ps1)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"
port="${OFFICE_PORT:-8797}"
home="${HERMES_HOME:-}"
start=0
pass=()

while [ $# -gt 0 ]; do
  case "$1" in
    --port) port="$2"; pass+=(-Port "$2"); shift 2 ;;
    --home) home="$2"; pass+=(-HermesHome "$2"); shift 2 ;;
    --no-start) start=0; pass+=(-NoStart); shift ;;
    --start) start=1; shift ;;
    --no-autostart) pass+=(-NoAutostart); shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "неизвестный аргумент: $1"; exit 2 ;;
  esac
done

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    command -v powershell.exe >/dev/null || { echo "нет powershell.exe в PATH"; exit 1; }
    win_repo="$(cygpath -w "$repo" 2>/dev/null || echo "$repo")"
    args=()
    for a in ${pass[@]+"${pass[@]}"}; do
      case "$a" in
        -*) args+=("$a") ;;
        *)  args+=("$(cygpath -w "$a" 2>/dev/null || echo "$a")") ;;
      esac
    done
    exec powershell.exe -NoProfile -ExecutionPolicy Bypass \
      -File "$win_repo\\scripts\\install.ps1" ${args[@]+"${args[@]}"}
    ;;
esac

# ---------- Linux / macOS ----------
[ -n "$home" ] || home="$HOME/.hermes"
[ -f "$repo/web/office.html" ] || { echo "нет web/office.html"; exit 1; }

live="$home/office-live"
plug="$home/desktop-plugins/office-hermes"
mkdir -p "$live" "$plug"
cp "$repo/server/office_live.py" "$live/office_live.py"
cp "$repo/web/office.html" "$live/office.html"
sed -E "s#http://127\.0\.0\.1:[0-9]+#http://127.0.0.1:$port#g" "$repo/plugin.js" > "$plug/plugin.js"

echo "офис   : $live"
echo "панель : $plug/plugin.js"
echo "порт   : $port"

if [ "$start" = 1 ]; then
  pkill -f 'office_live.py' 2>/dev/null || true
  nohup python3 "$live/office_live.py" --port "$port" --home "$home" >"$live/feed.log" 2>&1 &
  for _ in $(seq 1 40); do
    sleep 0.35
    if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
      echo "фид    : OK http://127.0.0.1:$port/"
      break
    fi
  done
else
  echo "запуск : python3 \"$live/office_live.py\" --port $port --home \"$home\""
fi

echo ""
echo "Дальше в Hermes: Ctrl+K → «Reload desktop plugins»."
echo "Автозапуск (Linux): systemd --user unit или ~/.config/autostart/*.desktop →"
echo "  Exec=python3 $live/office_live.py --port $port"
