#!/usr/bin/env bash
# Smoke tests against two fake matter-srv instances.
# Run dev/start_two.sh first, then ./dev/smoke.sh.

set -u

A_URL=http://127.0.0.1:8080
B_URL=http://127.0.0.1:8090
A_KEY=${A_KEY:-keyA}
B_KEY=${B_KEY:-keyB}

pass=0; fail=0
check() {
    local name=$1 expected=$2 actual=$3
    if [[ "$actual" == *"$expected"* ]]; then
        echo "  PASS  $name"
        pass=$((pass+1))
    else
        echo "  FAIL  $name"
        echo "        expected substring: $expected"
        echo "        got: $actual"
        fail=$((fail+1))
    fi
}

curl_a() { curl -sS -H "X-API-Key: $A_KEY" "$@"; }
curl_b() { curl -sS -H "X-API-Key: $B_KEY" "$@"; }

echo "== auth =="
check "no key → 401" '"unauthorized"' "$(curl -sS $A_URL/api/status)"
check "wrong key → 401" '"unauthorized"' "$(curl -sS -H 'X-API-Key: nope' $A_URL/api/status)"
check "good key → 200" '"lights_on"' "$(curl_a $A_URL/api/status)"

echo "== A standalone =="
check "A devices contain dev_aaaa0001" 'dev_aaaa0001' "$(curl_a $A_URL/api/devices)"
check "A status: 1 light on, 1 off" '"lights_on":1' "$(curl_a $A_URL/api/status)"
check "A toggle dev_aaaa0001 → off" '"success"' "$(curl_a "$A_URL/api/toggle?id=dev_aaaa0001")"
check "A status: 0 lights on" '"lights_on":0' "$(curl_a $A_URL/api/status)"
check "A set_mired clamp 100 → 153" '"mireds":153' "$(curl_a -X POST -H 'Content-Type: application/json' -d '{"id":"dev_aaaa0002","mireds":100}' $A_URL/api/mired)"
check "A set_mired clamp 999 → 500" '"mireds":500' "$(curl_a -X POST -H 'Content-Type: application/json' -d '{"id":"dev_aaaa0002","mireds":999}' $A_URL/api/mired)"
check "A unknown device → 404" 'not found' "$(curl_a "$A_URL/api/level?id=dev_zzzz")"

echo "== federation A → B =="
check "A registers B as logical bridge" '"success"' "$(curl_a "$A_URL/api/bridge?ip=127.0.0.1&port=8090&api_key=$B_KEY")"
check "A now sees B's dev_bbbb0001" 'dev_bbbb0001' "$(curl_a $A_URL/api/devices)"
check "A controls B's light via /api/set" '"type":"logical"' "$(curl_a -X POST -H 'Content-Type: application/json' -d '{"id":"dev_bbbb0001","brightness":0.5}' $A_URL/api/set)"

before=$(curl_b $B_URL/api/lights)
check "B reflects level change from A" '"brightness":0.5' "$(curl_b $B_URL/api/refresh; curl_a "$A_URL/api/refresh"; curl_b $B_URL/api/lights)"

check "A status dedups (federation loop safe)" '"total_devices":' "$(curl_a $A_URL/api/status)"

echo "== batch parallel =="
check "A batch returns 3 results" '"id":"dev_aaaa0002"' "$(curl_a -X POST -H 'Content-Type: application/json' -d '{"actions":[{"id":"dev_aaaa0001","brightness":1.0},{"id":"dev_aaaa0002","brightness":0.7},{"id":"dev_zzzz","brightness":1.0}]}' $A_URL/api/batch)"

echo "== /api/metadata declarative =="
meta=$(curl_a $A_URL/api/metadata)
check "metadata has capabilities, no events.script" '"capabilities"' "$meta"
if [[ "$meta" == *'"script"'* ]]; then
    echo "  FAIL  metadata still contains script blobs"
    fail=$((fail+1))
else
    echo "  PASS  metadata has no script blobs"
    pass=$((pass+1))
fi
check "metadata api_version=2" '"api_version":"2"' "$meta"

echo
echo "Results: $pass passed, $fail failed"
exit $fail
