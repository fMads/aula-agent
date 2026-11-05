#!/usr/bin/env bash
# Natkørsel uden T3 Code: en AI-agent kører i projektmappen og følger AGENTS.md.
# Køres af aula-nightly.timer (systemd), cron eller i hånden.
#   AULA_AGENT=claude (standard) eller codex
#   CLAUDE_BIN / CODEX_BIN, hvis programmerne ligger et usædvanligt sted
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
agent="${AULA_AGENT:-claude}"
prompt='Kør den natlige Aula-synkronisering. Følg instruktionerne i AGENTS.md under "Natlig kørsel" præcist.'
mkdir -p data/logs
log="data/logs/nightly-$(date +%F).log"

run() {
  case "$agent" in
    claude)
      # Må kun køre aula-agent, læse filer og skrive i data/.
      "${CLAUDE_BIN:-claude}" -p "$prompt" \
        --allowedTools "Bash(uv run aula-agent:*)" "Read" "Edit(./data/**)"
      ;;
    codex)
      # Sandkasse: skriv kun i projektmappen, Aula-sessionen og uv's cache; netværk til Aula og Google.
      mkdir -p "$HOME/.config/aula"
      "${CODEX_BIN:-codex}" exec -s workspace-write \
        -c sandbox_workspace_write.network_access=true \
        -c "sandbox_workspace_write.writable_roots=[\"$HOME/.config/aula\",\"$(uv cache dir)\"]" \
        "$prompt"
      ;;
    *) echo "Ukendt AULA_AGENT: $agent (brug claude eller codex)"; return 2 ;;
  esac
}

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %z') ($agent) ==="
  run
} >>"$log" 2>&1
