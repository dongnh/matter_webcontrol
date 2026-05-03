#!/usr/bin/env bash
# Start two fake matter-srv instances for federation testing.
# Logs go to dev/logs/. Stop them with dev/stop.sh.

set -u
cd "$(dirname "$0")/.."
mkdir -p dev/logs

PYTHON=${PYTHON:-venv/bin/python}
A_KEY=${A_KEY:-keyA}
B_KEY=${B_KEY:-keyB}

# Clean stale state
rm -f bridge_cache_devA.json bridge_cache_devB.json

$PYTHON dev/fake_server.py --port 8080 --api-key "$A_KEY" --fixture A --cache-file bridge_cache_devA.json > dev/logs/A.log 2>&1 &
echo $! > dev/logs/A.pid

$PYTHON dev/fake_server.py --port 8090 --api-key "$B_KEY" --fixture B --cache-file bridge_cache_devB.json > dev/logs/B.log 2>&1 &
echo $! > dev/logs/B.pid

# Wait until both respond
for i in {1..30}; do
    sleep 0.3
    a=$(curl -sS -o /dev/null -w "%{http_code}" -H "X-API-Key: $A_KEY" http://127.0.0.1:8080/api/status || true)
    b=$(curl -sS -o /dev/null -w "%{http_code}" -H "X-API-Key: $B_KEY" http://127.0.0.1:8090/api/status || true)
    if [[ "$a" == "200" && "$b" == "200" ]]; then
        echo "Both instances ready (A: $A_KEY, B: $B_KEY)"
        exit 0
    fi
done
echo "Timed out waiting for instances. Check dev/logs/{A,B}.log"
exit 1
