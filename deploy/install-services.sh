#!/usr/bin/env bash
# Installerer aula-agents systemd-brugertjenester.
#   ./deploy/install-services.sh nightly        # natkørsel uden T3 Code (Claude Code)
#   AULA_AGENT=codex ./deploy/install-services.sh nightly   # ... med Codex i stedet
#   ./deploy/install-services.sh web            # web-siden på 127.0.0.1:8787
#   ./deploy/install-services.sh web tunnel     # web-siden + Cloudflare-tunnel
set -euo pipefail
[ $# -gt 0 ] || { sed -n '2,7p' "$0"; exit 1; }
agent="${AULA_AGENT:-claude}"
repo="$(cd "$(dirname "$0")/.." && pwd)"
unit_dir="$HOME/.config/systemd/user"
mkdir -p "$unit_dir"

need() { command -v "$1" >/dev/null 2>&1 || PATH="$HOME/.local/bin:$PATH" command -v "$1" >/dev/null 2>&1 || { echo "Mangler '$1' – se README." >&2; exit 1; }; }
which_() { PATH="$HOME/.local/bin:$PATH" command -v "$1"; }

install_unit() {  # $1 = filnavn uden .in
  sed -e "s|@REPO@|$repo|g" -e "s|@UV@|${uv:-}|g" -e "s|@CLOUDFLARED@|${cloudflared:-}|g" -e "s|@AGENT@|$agent|g" \
    "$repo/deploy/$1.in" >"$unit_dir/$1"
  echo "Installeret: $unit_dir/$1"
}

enable=()
for what in "$@"; do
  case "$what" in
    web)
      need uv; uv="$(which_ uv)"
      install_unit aula-web.service; enable+=(aula-web.service) ;;
    tunnel)
      need cloudflared; cloudflared="$(which_ cloudflared)"
      [ -s "$HOME/.config/aula-agent/tunnel-token" ] || { echo "Gem først tunnel-tokenet i ~/.config/aula-agent/tunnel-token (chmod 600)." >&2; exit 1; }
      install_unit aula-tunnel.service; enable+=(aula-tunnel.service) ;;
    nightly)
      case "$agent" in claude|codex) need "$agent" ;; *) echo "AULA_AGENT skal være claude eller codex" >&2; exit 1 ;; esac
      need uv
      install_unit aula-nightly.service; install_unit aula-nightly.timer; enable+=(aula-nightly.timer) ;;
    *) echo "Ukendt: $what (brug web, tunnel eller nightly)" >&2; exit 1 ;;
  esac
done

systemctl --user daemon-reload
systemctl --user enable --now "${enable[@]}"
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
  echo "Tip: kør 'sudo loginctl enable-linger $USER', så tjenesterne også kører, når du ikke er logget ind."
fi
