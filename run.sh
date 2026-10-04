#!/bin/sh
# Native launcher. Do not compile or make network requests while opening a pane.
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$root/bin/herdr-agent-grid" ]; then
    exec "$root/bin/herdr-agent-grid" "$@"
fi
if [ -x "$root/target/release/herdr-agent-grid" ]; then
    exec "$root/target/release/herdr-agent-grid" "$@"
fi
printf '%s\n' 'herdr-agent-grid: native binary missing. Run ./install.sh in the plugin directory.' >&2
if [ -t 0 ] && [ -t 1 ]; then
    printf 'Press Enter to close. '
    read -r ignored
fi
exit 1
