#!/usr/bin/env bash
cd "$(dirname "$0")/.."
for tag in A B; do
    pid_file=dev/logs/$tag.pid
    if [[ -f $pid_file ]]; then
        pid=$(cat "$pid_file")
        if kill "$pid" 2>/dev/null; then
            echo "Stopped $tag (pid $pid)"
        fi
        rm -f "$pid_file"
    fi
done
rm -f bridge_cache_devA.json bridge_cache_devB.json
