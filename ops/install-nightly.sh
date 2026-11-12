#!/bin/zsh
# Install or remove the nightly launchd job of the SEO engine for one site.
# Usage: ops/install-nightly.sh install|remove <site> [hour] [minute]
# The job only reads sources and writes into the site's data directory. It changes nothing on the site.
set -euo pipefail
action="${1:?install|remove}"; site="${2:?site name}"; hour="${3:-5}"; minute="${4:-40}"
repo="$(cd "$(dirname "$0")/.." && pwd)"
label="ru.seo-engine.${site}.nightly"
plist="$HOME/Library/LaunchAgents/${label}.plist"
data="$(cd "$repo" && python3 -c "from engine.config import load; print(load('${site}')['data'])")"
domain="gui/$(id -u)"
if [[ "$action" == "remove" ]]; then
  launchctl bootout "$domain/$label" 2>/dev/null || true
  rm -f "$plist"
  echo "removed $label"
  exit 0
fi
sed -e "s|__PYTHON3__|$(command -v python3)|" -e "s|__REPOSITORY__|$repo|" -e "s|__DATA_DIRECTORY__|$data|g" \
    -e "s|__LABEL__|$label|" -e "s|__SITE__|${site}|" \
    -e "s|<key>Hour</key><integer>4</integer>|<key>Hour</key><integer>${hour}</integer>|" \
    -e "s|<key>Minute</key><integer>10</integer>|<key>Minute</key><integer>${minute}</integer>|" \
    "$repo/docs/nightly.plist" > "$plist"
plutil -lint "$plist"
launchctl bootout "$domain/$label" 2>/dev/null || true
launchctl bootstrap "$domain" "$plist"
launchctl print "$domain/$label" | grep -E "state|program|working directory" | head -5
echo "installed $label at ${hour}:${minute} data=$data"
