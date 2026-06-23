# Matter Web Controller — Audit & Restructure Plan

> **Status:** Ready to execute. This document is self-contained — a fresh session
> can pick it up cold. It is the output of a multi-agent audit (9 dimensions, every
> finding adversarially re-verified) plus a 3-way restructure design contest judged
> for fit + simplicity + migration safety.
>
> **Baseline commit:** `6f90f53` (v0.27.0). Codebase ≈ 1,770 lines across `cli/`.
>
> **Golden rule for the executor:** order by *risk retired*, not by tidiness. Land
> the small concurrency/persistence fixes first (they are the only things that can
> take the server down), keep `dev/smoke.sh` green at every commit, and do **not**
> over-engineer — see [§4 Guardrails](#4-guardrails).

---

## 1. Executive summary

The top-level architecture is **sound** (core owns logic; HTTP/MCP are thin; IDs are
canonical; logical-first routing is the intent). The problems are not structural rot —
they are:

1. **Concurrency hazards that can hang or thrash the server** — synchronous `urllib`
   federation calls run directly on the asyncio event loop, and `_on_event` does a
   full fabric re-scan + disk write on *every* attribute update. These are small,
   localized, independently-shippable fixes and they go **first**.
2. **A monolithic `core.py`** that grew six near-identical "physical loop + logical
   loop" traversals, with names-resolution and federation-loop dedup drifting between
   them.
3. **A leaky `core ↔ bridge` seam** — `core.py` reaches into `bridge._private` members.
4. **Real correctness bugs** — `set_ac` writes the cooling setpoint even in Heat mode;
   `register_device` attaches the alias to the wrong device.
5. **Security/hygiene gaps** — secrets in URL query strings, mutations over GET,
   non-atomic cache writes, unpinned deps, no tests, doc drift.

**Approach (winner of the design contest — "risk-first incremental"):** drain the
event loop and make persistence crash-safe *before* any module reshuffle; then carve
exactly **two seams** (`_route()` resolver + `_iter_devices()` enumerator) and a
**bridge public facade**, one query/control family at a time, behind a test net.
**Keep the flat `cli/` package and the `cli.server:main` / `cli.mcp_server:main`
entry points.** Do not split into layered subpackages, do not introduce Protocols/DI,
do not rewrite `urllib`→`aiohttp` (use `asyncio.to_thread`).

### Severity tally (post-verification)

| Severity | Confirmed findings | Critic gaps |
|----------|-------------------:|------------:|
| High     | 6                  | 2           |
| Medium   | 30                 | 5           |
| Low      | 28                 | 1           |
| **Total**| **64**             | **8**       |

0 findings were rejected as false positives; several had their severity corrected
(noted in the catalog).

---

## 2. Target structure

Flat `cli/` package preserved. New files are **pure leaves** or **thin edges** — every
new module must either remove a duplicated copy or host a confirmed bug fix (litmus
test below). Nothing is split preemptively.

```
matter_webcontrol/
  cli/
    __init__.py          # re-exports DeviceController, MatterBridgeServer, LogicalBridgeManager
    constants.py         # NEW (pure leaf): THERMO_MODE_*, THERMO_VALID_MODES, SENSOR_KEYS,
                         #   MIRED_MIN/MAX, Thermostat cluster/attr ids (513, 17=cooling,
                         #   18=heating, 28=mode); extract_matter_pin()
    conversions.py       # NEW (pure leaf): brightness_raw<->normalized (+ off-forces-0 policy
                         #   in ONE place), mired<->kelvin clamp, centi-degree round(x*100),
                         #   clamp helpers. Zero deps. Highest-value unit-test target.
    serializers.py       # NEW: _build_light/_build_sensor/_climate_entry/_ac_entry + metadata
                         #   capability/hw-type derivation; pure (device, names) -> dict;
                         #   plus the ONE resolved_names(dev) merge policy.
    paths.py             # NEW (small): resolve_data_dir() + cache/storage path helpers (G1/G8)
    core.py              # DeviceController STAYS here (no rename). Holds the two seams
                         #   (_route, _iter_devices); queries become filters over _iter_devices;
                         #   control awaits federation via asyncio.to_thread. Split into a
                         #   cli/controller/ package ONLY if it exceeds ~450 lines afterwards.
    matter_bridge.py     # MatterBridgeServer: Matter I/O + PUBLIC facade (add_alias/remove_alias/
                         #   names_for/sync/device_ids_for_node/occupancy_last_active/
                         #   subscribe_occupancy/unsubscribe); atomic+debounced persistence;
                         #   one-shot migration; startup-failure cleanup.
    logic_bridge.py      # LogicalBridgeClient/Manager (no rename). Sync internally, but every
                         #   call invoked from core via asyncio.to_thread; ip/port SSRF
                         #   validation + self-registration reject; 0600 cache; per-bridge error
                         #   logging + refreshed/failed counts; HTTPError->status translation.
    schemas.py           # NEW: Pydantic request models moved from server.py + BridgePayload/
                         #   RegisterPayload (secrets in body) + one typed coercion path;
                         #   mode/system_mode reconciled to one canonical field.
    auth.py              # NEW: hmac.compare_digest middleware; refuse non-loopback bind w/o key.
    server.py            # FastAPI routes only + lifespan + main(). POST-only mutations;
                         #   /api/bridge key in body; SSE via controller; /health + /version.
    mcp_server.py        # FastMCP HTTP-client tools (imports NOTHING from core/bridge/logic_bridge).
                         #   fan_speed on set_ac; unregister_node + get_metadata tools; GET->POST;
                         #   HTTPError->{error}; allowed-hosts instead of disabling DNS-rebind.
  tests/                 # NEW
    __init__.py
    fakes.py             # FakeBridge/FakeMatterClient/FIXTURES lifted from dev/fake_server.py
                         #   (single source; must implement the new bridge public facade)
    conftest.py          # controller_with_fixture(); httpx ASGITransport app fixture
    test_conversions.py  test_serializers.py  test_controller.py
    test_status.py       test_api.py
  dev/
    fake_server.py       # imports tests/fakes.py (no duplicated fixtures)
    smoke.sh start_two.sh stop.sh   # keep the 2-process federation check
  .github/workflows/
    test.yml             # NEW: ruff/black --check, mypy, pytest on push+PR
    python-publish.yml   # gains `needs: test`; remove scaffold NOTE comment
  pyproject.toml         # pinned deps + [project.optional-dependencies].dev + pytest config;
                         #   entry points UNCHANGED
  README.md  CHANGELOG.md  CLAUDE.md
  # DELETE from working tree: build/, matter_web_controller.egg-info/, .DS_Store, cli/.DS_Store
  # RENAME: devices_cache.txt -> devices_cache.json (update path constant + .gitignore)
```

### Module responsibilities

- **`cli/constants.py`** — single source for every magic value currently scattered at
  the top of `core.py` or inlined (`set_ac` hardcodes `f"{ep_id}/513/17"`; `matter_bridge`
  uses bare cluster ints `6/8/768/513`). Hosts `extract_matter_pin`.
- **`cli/conversions.py`** — pure deterministic math with `254 / 100 / 1_000_000 / 153 / 500`;
  the off-forces-0 brightness decision lives here once. Unit-testable with zero fixtures.
- **`cli/serializers.py`** — all entry builders as pure `(device, names) -> dict`, plus the
  single `resolved_names(dev)` that merges local `bridge.device_names` with remote `dev['names']`.
- **`cli/paths.py`** — resolves one data directory (CLI flag / env, sensible default) and
  derives every cache path + the Matter `--storage-path` from it.
- **`cli/core.py` (DeviceController)** — transport-agnostic orchestration. Holds
  `_route(resolved_id) -> (kind, target, client)` (the single logical-first routing truth)
  and `_iter_devices() -> (device, names, origin)` (dedup-by-id applied once). Control
  methods `await asyncio.to_thread(...)` for federation. No `bridge._private` access.
- **`cli/matter_bridge.py` (MatterBridgeServer)** — owns all Matter I/O + local persistence
  policy. Public facade so `core` stops touching underscores. Atomic `_save_json`
  (`tmp` + `os.replace`, raises `OSError`); debounced flush off the `_on_event` hot path;
  one-shot ID migration at startup; cleanup on init failure.
- **`cli/logic_bridge.py`** — federation registry + client; SSRF/self-reg validation;
  `0600` cache; concurrent refresh with per-bridge status; `HTTPError`→status translation.
- **`cli/schemas.py` + `cli/auth.py`** — typed request contract (secrets in body, one
  canonical AC mode field); constant-time auth; refuse insecure bind.
- **`cli/server.py`** — thin routes; POST-only mutations; SSE through controller; `/health`+`/version`.
- **`cli/mcp_server.py`** — thin HTTP client; full parity (fan_speed, unregister, metadata);
  structured errors; allowed-hosts.
- **`tests/`** — `fakes.py` is the single fake source shared with `dev/fake_server.py`.

---

## 3. Reference: how the system works today (orientation for the executor)

- **`cli/core.py`** — `DeviceController`, the single source of business logic. Queries
  (`get_devices/lights/sensors/climate/acs/status/metadata`), control
  (`set_device/toggle/set_level/set_mired/set_ac/batch_control`), management
  (`set_name/remove_name/add_bridge/remove_bridge/register_device/unregister_node/refresh`).
- **`cli/server.py`** — FastAPI wrappers via `_wrap`/`_wrap_async`; `auth_middleware`
  checks `X-API-Key`; SSE `/api/subscribe`. `main()` parses args, runs uvicorn.
- **`cli/matter_bridge.py`** — `MatterBridgeServer`: spawns `python-matter-server` as a
  subprocess, connects over WebSocket, subscribes to `ATTRIBUTE_UPDATED`, builds
  `cached_devices`, manages `device_names`/`occupancy_history`/`occupancy_subscribers`,
  persists JSON caches. Stable ID = `dev_{md5(unique_id + endpoint)[:8]}`.
- **`cli/logic_bridge.py`** — `LogicalBridgeClient` (blocking `urllib`) + `LogicalBridgeManager`
  (registry, `bridge_cache.json`). HTTP federation to peer instances.
- **`cli/mcp_server.py`** — FastMCP tools that HTTP-call `matter-srv`. Imports nothing
  from core/bridge/logic_bridge (keep it that way).
- **`dev/fake_server.py`** — runs `cli.server.app` with a `FakeBridge` (no hardware).
  `dev/smoke.sh` + `dev/start_two.sh` exercise auth, clamping, 404s, and A→B federation.

Routing rule (CLAUDE.md): *commands check logical bridges first, then physical.* This is
violated today — see [A1](#catalog).

---

## 4. Guardrails

**Litmus test for any new module/abstraction:** *does it remove a duplicated copy or host
a confirmed bug fix, or is it just moving lines?* If the latter, do not create it.

**Do NOT:**

- ❌ Do a big-bang rewrite. The structural moves come **after** the risk is retired, each
  guarded by the Step-0 test net, with `dev/smoke.sh` green at every commit.
- ❌ Split into `domain/ views/ service/ ports/ adapters/ http/ mcp/` subpackages, or turn
  5 files into ~20. This is ~1,770 lines with four real callers — the flat `cli/` package
  is correct.
- ❌ Move the console-script entry points. Keep `cli.server:main` and `cli.mcp_server:main`
  in `pyproject.toml` (a rename is a gratuitous breaking install change).
- ❌ Introduce a DI container, repository/unit-of-work, ORM, event bus, or `MatterPort`/
  `FederationPort` ABCs. The duck-typed `FakeBridge` already proves the seam; formal
  Protocols are ceremony here.
- ❌ Add `domain/models.py` typed dataclasses replacing the raw device dicts — those dicts
  are the **federation wire format** peers consume. Keep the response shapes stable.
- ❌ Convert `urllib`→`aiohttp` as the federation fix. `asyncio.to_thread` fully retires the
  event-loop stall with a fraction of the diff. Native async is **deferred, optional, only
  if threadpool overhead is measured.**
- ❌ Eagerly split `DeviceController` into a `cli/controller/` package. Defer until `core.py`
  actually exceeds ~450 lines after the enumerator lands.
- ❌ Wire alias→ID resolution into `_resolve()`. IDs are canonical; aliases are display-only.
  Either delete `_resolve` or keep it identity with a docstring — never make it a lookup.
- ❌ Let `mcp_server.py` import `core`/`matter_bridge`/`logic_bridge`. It must stay a pure
  HTTP client.
- ❌ Add permissive CORS. Auth is a header (`X-API-Key`), not a cookie; permissive CORS would
  *undo* the CSRF protection that POST-only mutations buy.
- ❌ Treat response-model/OpenAPI-tag work or a full typed-query-param overhaul as anything
  but optional last-step polish.

---

## 5. Migration plan

Each step is independently shippable, ordered by risk retired. Run the test net after
each. Finding IDs reference [§6 Catalog](#6-findings-catalog).

### Step 0 — Safety net (no behavior change) — **do this first**
**Goal:** pin current behavior so every later refactor is regression-guarded.
- Extract `FakeBridge`/`FakeMatterClient`/`FIXTURES` from `dev/fake_server.py` into
  `tests/fakes.py`; point the dev harness at it (no duplicate fixtures).
- Add to `pyproject.toml`: `[project.optional-dependencies].dev = ["pytest",
  "pytest-asyncio", "httpx", "anyio", "ruff", "black", "isort", "mypy"]` and
  `[tool.pytest.ini_options]` with `asyncio_mode = "auto"`, `testpaths = ["tests"]`.
- Write `tests/conftest.py` (controller-with-fixture; httpx `ASGITransport` over
  `cli.server.app` with `app.state.api_key` set).
- `tests/test_api.py` — port the `dev/smoke.sh` single-instance assertions: no key→401,
  good key→200 with `lights_on`, set_mired clamp 100→153 / 999→500, unknown device→404,
  batch returns per-action `{status:error}`, `/api/metadata` `api_version=="2"`.
- `tests/test_conversions.py` — golden values for the pure functions **as they are now**.

**Verify:** `pytest` green; `dev/smoke.sh` still green (2-process). Addresses **T1, T4**.

### Step 1 — Retire the server-hang risk (smallest diff, highest impact)
**Goal:** no slow/dead peer can freeze the event loop.
- Wrap every federated client call in the async core methods with
  `await asyncio.to_thread(...)`: `core.py:309, 312, 361, 382, 502, 506`
  (`set_brightness/set_level/set_mired/set_ac/refresh`).
- Make `/api/refresh` and `/api/bridge` offload (`server.py:187, 310`) — either declare
  the route handlers `def` (Starlette threadpools them) or `await asyncio.to_thread(...)`.
- In `refresh_bridges` (`logic_bridge.py:154-162`), fan out per-bridge refreshes
  concurrently (threadpool) instead of serially, log each failure with its node id, and
  return `{refreshed, failed}` counts instead of a bare success count.

**Verify:** add a `tests/` case with a fake unreachable peer; assert other requests are
not blocked. **Addresses CC1, CC2, E5** (partial: per-bridge logging).

### Step 2 — Retire the disk-thrash risk
**Goal:** a busy fabric no longer stalls callbacks or hammers the disk.
- `matter_bridge._on_event` (`matter_bridge.py:133-134`) must stop calling `_update_cache()`
  synchronously per `ATTRIBUTE_UPDATED`. Update the in-memory cache cheaply per event;
  flush `devices_cache` on a **debounced** schedule (at most every few seconds / on
  shutdown), and skip the write when the serialized snapshot is unchanged (`:299`).
- Move ID migration out of the hot path: run `_migrate_ids` **once at startup** (inside
  `initialize`, before subscribing to events), guarded by a schema/version marker so a
  completed migration is never re-attempted (`matter_bridge.py:196-228, 297`).

**Verify:** assert `_save_json` is not called on an event that changes nothing; migration
runs once. **Addresses CC3, G4.**

### Step 3 — Crash-safe + loud persistence, and a stable data directory
**Goal:** writes are atomic and failures are visible; state lives in a known place.
- Rewrite `matter_bridge._save_json` (`matter_bridge.py:60-66`) to write `path + ".tmp"`
  then `os.replace(tmp, path)` (mirror `logic_bridge._save_cache:126-129`). Narrow the
  `except` to `OSError` and **re-raise after logging** so `set_name`/`remove_name`
  (`core.py:419, 433`) can no longer report `success` on a failed write.
- Add `cli/paths.py`: `resolve_data_dir()` from a `--data-dir` flag / env var (default to a
  stable location, not bare CWD). Build `devices_cache`, `names_cache`, `occupancy_cache`,
  `bridge_cache`, and the Matter `--storage-path` from it. Log the **absolute** resolved
  `matter_storage` path at startup (a CWD change today silently re-commissions the whole
  fabric — `matter_bridge.py:41-43, 76-77`; `logic_bridge.py:100`).

**Verify:** kill mid-write test leaves the old file intact; failed write surfaces as 500;
startup logs the absolute storage path. **Addresses E1, E2, G1.**

### Step 4 — Pure leaves (mechanical) + their tests
**Goal:** extract the zero-dependency logic and lock it under test.
- Create `cli/constants.py` (move `THERMO_*`, `THERMO_VALID_MODES`, `SENSOR_KEYS`,
  `MIRED_MIN/MAX`, Thermostat cluster/attr ids, `extract_matter_pin`) and
  `cli/conversions.py` (brightness raw↔normalized + off-forces-0, mired↔kelvin clamp,
  centi-degree scaling, clamps). Import them back into `core.py`.
- Create `cli/serializers.py`: move `_build_light/_build_sensor/_climate_entry/_ac_entry`
  + the `get_metadata` capability/hw-type derivation as pure `(device, names)` functions,
  and add the single `resolved_names(dev)` merge. Point all query methods at it — this is
  the only behavior change in this step (aliases on logical devices now appear consistently).
- Add `tests/test_conversions.py` (extended) + `tests/test_serializers.py`.

**Verify:** `pytest`; `dev/smoke.sh`. **Addresses A3 (partial), A4, C5, T2, T6.**

### Step 5 — The two seams (behavior-affecting, guarded)
**Goal:** one traversal, one routing order.
- Add `_route(resolved_id) -> (kind, target, client)` (logical-first) in `core.py`. Make
  `set_device`'s AC branch (`:297-301`), `set_ac` (`:498`), `toggle`/`_find_state`
  (`:343, :61-69`), and `get_ac` (`:474`) all consult it — today `set_device` is
  physical-first while the others are logical-first, so the same ID routes to opposite
  destinations.
- Add `_iter_devices() -> (device, names, origin)` with dedup-by-id applied once. Rewrite
  `get_devices/lights/sensors/climate/acs/status/metadata` as filters over it. Fold
  `get_status`'s `seen`-set dedup in so list endpoints and counts agree, and so a
  self-/mutual-federation loop can't double-count.
- Delete `_resolve()` (or keep identity with a docstring — see guardrails).
- `tests/test_status.py` + `tests/test_controller.py` (routing + dedup).

**Verify:** same-ID-in-both-physical-and-logical test counts once and routes logical-first.
**Addresses A1, A3, A5, A7, G2 (dedup half).**

### Step 6 — Bridge public facade
**Goal:** kill every `core → bridge._private` reach-through.
- Give `MatterBridgeServer` a public API: `add_alias(id,name)` / `remove_alias(id,name)`
  (encapsulating the conflict check now in `core.set_name`), `names_for(id)`,
  `sync()` (public `_update_cache`), `device_ids_for_node(node_id)`,
  `occupancy_last_active(id)`, `subscribe_occupancy(id)` / `unsubscribe(queue)`.
- Repoint `core.set_name/remove_name/register_device` and the SSE endpoint
  (`server.py:249-273`) at the facade. Old privates stay as thin internal callers.
- Prune `occupancy_history` and `occupancy_subscribers` on `unregister_node`/`dedupe`, and
  delete the id key in the SSE `finally` block when its subscriber list is empty
  (`matter_bridge.py:285-287`; `server.py:271-273`).
- `tests/fakes.py` must grow the same facade methods.

**Verify:** grep shows no `bridge._` access from `core.py`/`server.py`; pruning test.
**Addresses A2, G3.**

### Step 7 — Correctness fixes (behind the net)
**Goal:** the real behavior bugs.
- **`set_ac` setpoint-by-mode** (`core.py:543-550`): compute `effective_mode = target_mode
  if given else phys["states"].get("system_mode")`; write attr **18** (heating_setpoint)
  for Heat(4)/EmergencyHeat(5), attr **17** (cooling_setpoint) for Cool(3)/Precooling(6);
  for Auto(1) write both (respecting deadband) or reject a single scalar. Report the
  setpoint under **one neutral `setpoint` key** in both logical and physical branches
  (`:512` vs `:550` disagree today). Report `system_mode` on `on=True` in the logical
  branch (`:510-511`). Wrap the two physical writes so a partial failure refreshes the
  cache and returns which writes landed (`:535-553`).
- **`register_device` deterministic alias** (`core.py:589-602`): snapshot the device-id
  set **before** `commission`, or resolve via the new `bridge.device_ids_for_node(new_node_id)`,
  and attach the alias to that exact `dev_*`. Delete the tautological `existing_ids`
  check. Surface `name_not_applied` when nothing matched.
- **`fan_speed` on physical ACs** (`core.py:483-553`): raise `ValueError("fan_speed not
  supported on physical Matter ACs")` instead of returning `success` while silently
  dropping it (documented asymmetry, but a silent no-op on a mutation is wrong).
- `tests/test_controller.py`: heat-mode setpoint writes attr 18; new-device naming.

**Verify:** `pytest`. **Addresses C1, C2, C3, C4, E3, E7, API2, A6.**

### Step 8 — Security / API hardening at the edges
**Goal:** secrets out of URLs, mutations not GET, bounded SSRF, resilient startup.
- Add `cli/schemas.py`: move the Pydantic request models out of `server.py`; add
  `BridgePayload`/`RegisterPayload` carrying the peer api_key and pairing code **in the
  JSON body**; one typed-coercion path so GET/POST validate identically; reconcile
  `mode`/`system_mode` to one canonical input field with a documented alias.
- Add `cli/auth.py`: `hmac.compare_digest` constant-time compare (`server.py:126-129`,
  `import hmac`); refuse to bind on any non-loopback host without a key (is-loopback check,
  not `!= "127.0.0.1"`) — currently only warns (`server.py:334-344`).
- Make all mutating/commissioning routes **POST-only** (`/api/toggle`, `/api/name/remove`,
  `/api/bridge`, `/api/bridge/remove`, `/api/register`, `/api/unregister`, `/api/refresh`)
  and mirror the `_get`→`_post` switch in `mcp_server.py`.
- `add_bridge` SSRF guard (`logic_bridge.py:131-144`): validate port range; restrict/allow-list
  or private-range check; **reject self-registration** (compare against local bind host/port
  and the `/api/metadata` bridge id) to kill the federation loop; return a uniform error to
  reduce the port-scan oracle. (Do **not** force `ip` to be a literal IP — LAN peers may use
  mDNS hostnames.)
- `chmod 0600` on `bridge_cache.json` (both the `.tmp` and final file).
- `mcp_server.py`: configure an allowed-hosts list instead of disabling DNS-rebinding
  protection wholesale (`:230-239`).
- `HTTPError`→status translation in `logic_bridge._request` and `mcp_server._get/_post`
  (`logic_bridge.py:25-49`, `mcp_server.py:35-54`): read the body, re-raise a status-carrying
  error (remote 4xx → `ValueError` so `_wrap` yields 400, not 500); MCP returns structured
  `{"error": ...}` instead of a traceback. Narrow `load_cache`'s bare `except`
  (`logic_bridge.py:112-119`) to network errors and log config-shape errors distinctly.
- MCP parity: add `fan_speed` to `set_ac`; add `unregister_node` + `get_metadata` tools.
- Add unauthenticated `GET /health` (reflects `bridge.is_ready()`) and `/version`.
- **Startup resilience** (`matter_bridge.py:81-105`): on init failure close the aiohttp
  session and terminate the subprocess before returning; consider failing startup hard
  rather than serving a zombie controller.
- Update `dev/smoke.sh` and `tests/` for the POST changes.

**Verify:** `pytest`; `dev/smoke.sh` (updated to POST); manual `curl` that GET on a
mutation now 405s. **Addresses S1–S8, E4, E6, API1, API4, API6, API8, API11, G2 (reject half), G5.**

### Step 9 — Packaging / CI / docs / observability cleanup
**Goal:** reproducibility, gates, accurate docs.
- **Deps:** add compatible-release bounds for breakage-prone libs (e.g.
  `"fastapi>=0.110,<1"`, `"pydantic>=2,<3"`, `"mcp[cli]>=1,<2"`, bound
  `python-matter-server`); commit a lockfile if deployable.
- **CI:** add `.github/workflows/test.yml` (push/PR → install `.[dev]`, `ruff check`,
  `black --check`, `mypy`, `pytest`); make `python-publish.yml`'s publish job
  `needs: test`; remove the leftover `# NOTE: put your own distribution build steps here.`
  comment. Run `mypy --strict` once and fix or relax config to reality.
- **Hygiene:** `rm -rf build/ matter_web_controller.egg-info/`; `find . -name .DS_Store
  -delete`; rename `devices_cache.txt` → `devices_cache.json` (update the path constant and
  the `.gitignore` entry). Confirm cache files + `matter_storage/` stay untracked (they are
  now — verified via `git ls-files`) and are excluded from any sdist/wheel; document that
  `matter_storage/` holds fabric keys and must never be packaged or committed.
- **Observability:** add `--log-level` (+ env) wired into a single logging setup; add a
  request-logging middleware (method, path, status, device id) that **redacts** the api_key;
  ensure federation refresh logs the bridge/host when it swallows an error.
- **Subprocess monitoring** (optional, `matter_bridge.py:73-79`): route subprocess
  stdout/stderr through logging; add a background task awaiting `process.wait()` that
  logs/raises on unexpected exit; drop the magic `await asyncio.sleep(2.0)` in favor of the
  existing 30-attempt connect retry.
- **Docs sweep** — reconcile with shipped surface:
  - README `/api/ac`: add `fan_speed` (0–100) param + note it applies to logical-bridge ACs;
    note `system_mode` accepted as a synonym for `mode`.
  - README MCP tool list: add `get_climate`, `list_acs`, `get_ac`, `set_ac`; drop/soften
    the "one per REST endpoint" claim (`/api/unregister`, SSE `/api/subscribe`, `/api/metadata`
    are HTTP-only).
  - README: fix `/api/occupancy/stream` → `/api/subscribe`; add `--transport`/`--mcp-host`/
    `--mcp-port` to the `matter-mcp` flag table; reword the no-key warning to "any non-loopback
    bind".
  - Make the documented AC mode list match `THERMO_VALID_MODES` (add 5/6/9) across README +
    MCP docstring + validator.
  - CHANGELOG: add the SSE/HTTP MCP transport + DNS-rebinding entry.
  - CLAUDE.md: extend the "Adding a new API endpoint" checklist to cover the MCP tool list +
    `matter-mcp` CLI/transport docs (not just the REST reference).

**Verify:** `ruff`/`black`/`mypy`/`pytest` green in CI; README curl examples resolve.
**Addresses P1–P6, T3, T5, D1–D9, API5, API7, API9, G6, G7, G8.**

### DEFER (only if measured)
- Split `core.py` into a `cli/controller/{base,queries,control,management}.py` package with
  `core.py` as a re-export shim — only if `core.py` exceeds ~450 lines after Step 5.
- Convert `logic_bridge` to native `aiohttp` coroutines — only if `asyncio.to_thread`
  threadpool overhead is actually observed.
- Pydantic response models + OpenAPI tags (`API9`) and a full typed-query-param overhaul
  (`API11`) — polish, not load-bearing.

---

## 6. Findings catalog

Severities are post-verification (corrections noted). Locations are `file:line` at baseline
`6f90f53`. "Step" is where the fix lands in §5.

### Concurrency (the dangerous ones)

| ID | Sev | Where | Problem → Fix | Step |
|----|-----|-------|---------------|------|
| CC1 | High | `logic_bridge.py:47`; `core.py:309,312,361,382,502,506` | Blocking `urllib` federation calls run on the asyncio loop → one slow peer freezes the whole server (≤5s, ×2 for `set_ac`). Wrap calls in `await asyncio.to_thread(...)`. | 1 |
| CC2 | High | `server.py:187,310`; `core.py:558,620,630` | `add_bridge`/`refresh` do blocking federation HTTP on the loop via sync `_wrap`; `/api/refresh` can freeze N×5s. Offload via `to_thread`/`def` routes; concurrent `refresh_bridges`. | 1 |
| CC3 | High* | `matter_bridge.py:133,230,299` | `_on_event` does a full node re-scan + `json.dump` per `ATTRIBUTE_UPDATED`. Debounce: cheap in-memory update, coalesced flush, skip unchanged. (*verifier: one file write/event in steady state, still real.) | 2 |
| CC4 | Low | `matter_bridge.py:282-283`; `server.py:251,271` | Unbounded SSE subscriber queue (slow consumer). Use `asyncio.Queue(maxsize=N)` + `try/except QueueFull`. | 6 |

### Correctness

| ID | Sev | Where | Problem → Fix | Step |
|----|-----|-------|---------------|------|
| C1 | High | `core.py:592-602` | `register_device` alias goes to the wrong device: `existing_ids` snapshot taken **after** commission → filter is dead code, picks "first unnamed". Snapshot before commission / resolve via `device_ids_for_node`. | 7 |
| C2 | High | `core.py:543-550` | `set_ac` always writes cooling setpoint (attr 17) even in Heat mode → "set temperature" silently no-ops the active heating setpoint. Choose attr by effective mode (18 heat / 17 cool). | 7 |
| C3 | Med | `core.py:512,550` | Same `setpoint` reported under `heating_setpoint` (logical) vs `cooling_setpoint` (physical). Use one neutral `setpoint` key. | 7 |
| C4 | Low | `core.py:510-511` | Logical `set_ac` `wrote` omits `system_mode` when `on=True`. Add an `elif on is True:` branch. | 7 |
| C5 | Low | `core.py:103-106` | `_build_light` forces `brightness=0.0` when off, hiding the stored level and disagreeing with `/api/level`. Keep real normalized brightness; let `state` convey on/off. | 4 |

### Architecture

| ID | Sev | Where | Problem → Fix | Step |
|----|-----|-------|---------------|------|
| A1 | Med | `core.py:290-337,339-351,61-69,472-519` | Logical-first vs physical-first ordering inconsistent (`set_device` physical-first; `set_ac`/`toggle`/`get_ac` logical-first). Centralize in `_route()`. | 5 |
| A2 | Med | `core.py:412-435,552,592-596,617,624,121,648`; `server.py:249-273` | `core` mutates `bridge` privates + SSE pokes `bridge.occupancy_subscribers`. Give the bridge a public facade. | 6 |
| A3 | Med | `core.py:129-140,142-153,193-204,458-470,250-286,633-696` | Six near-identical phys+logical traversals. Collapse into one `_iter_devices()` enumerator. | 4–5 |
| A4 | Med | `core.py:146,150,201,211,468,476,646-650` | Names source diverges (`_names_for` vs `dev['names']` vs merge) → aliases shown by some endpoints, dropped by others. Single `resolved_names()`. | 4 |
| A5 | Med | `core.py:250-286,88-93,...` | Federation-loop dedup only in `get_status`; list endpoints double-count. Fold dedup into the enumerator. | 5 |
| A6 | Med | `core.py:589-602`; `matter_bridge.py:154-194` | `register_device` alias heuristic mixes layers + is wrong. Resolve via `device_ids_for_node`. (pairs with C1) | 7 |
| A7 | Low | `core.py:45-46` + ~7 call sites | `_resolve()` is identity but implies alias resolution that doesn't exist. Delete or docstring-as-identity. | 5 |

### Security

| ID | Sev | Where | Problem → Fix | Step |
|----|-----|-------|---------------|------|
| S1 | High | `server.py:185-187`; `mcp_server.py:149-152`; `README.md:262`; `dev/smoke.sh:44` | Peer api_key sent/logged as a URL query param. Make `/api/bridge` POST with key in body; scrub logs. | 8 |
| S2 | Med | `core.py:557-559`; `logic_bridge.py:131-144,25-49` | SSRF: `add_bridge` fetches arbitrary `ip:port` server-side; port-scan oracle. Validate port; allow-list/private-range; uniform error. (don't force literal-IP — mDNS) | 8 |
| S3 | Med | `server.py:167-202,308-310` | Mutations + commissioning over GET; pairing code/api_key in logs. POST-only; secrets in body. | 8 |
| S4 | Med | `logic_bridge.py:121-129,104-119` | Peer api keys cleartext in `bridge_cache.json` with default perms. Write `0600`. | 8 |
| S5 | Med | `mcp_server.py:230-239` | DNS-rebinding protection disabled for MCP HTTP/SSE; unauth MCP can drive SSRF/commission. Allowed-hosts list instead. | 8 |
| S6 | Low | `server.py:126-129` | API key compared with `!=` (timing). `hmac.compare_digest(...)`. | 8 |
| S7 | Low | `server.py:121-129` | No rate limit/lockout. Add 401 backoff; omit CORS (don't add permissive). | 8 (opt) |
| S8 | Low | `server.py:334-344` | Non-loopback bind without key only warns. Refuse (sys.exit) unless `--insecure`; is-loopback check. | 8 |

### Error handling & resilience

| ID | Sev | Where | Problem → Fix | Step |
|----|-----|-------|---------------|------|
| E1 | Med | `matter_bridge.py:60-66,299,302,69` | Non-atomic `_save_json` truncates on crash (loses aliases). `tmp` + `os.replace` (match `logic_bridge`). | 3 |
| E2 | Med | `matter_bridge.py:60-66`; `core.py:419,433` | `_save_json` swallows write failures → `set_name` reports success then loses alias on restart. Narrow to `OSError` + re-raise. | 3 |
| E3 | Med | `core.py:535-553` | `set_ac` second write failure leaves partial state + opaque 500. Wrap writes; report what landed. | 7 |
| E4 | Med | `logic_bridge.py:112-119` | `load_cache` `except Exception: pass` masks config bugs as "offline". Catch only network errors; log shape errors. | 8 |
| E5 | Med | `logic_bridge.py:154-162`; `core.py:630-631` | `refresh_bridges` hides per-bridge failures behind a count. Log per bridge; return refreshed+failed. | 1 |
| E6 | Med | `mcp_server.py:35-54`; `logic_bridge.py:25-49` | No `HTTPError` translation → remote 4xx becomes 500; MCP returns raw traceback. Translate + structured `{error}`. | 8 |
| E7 | Low | `core.py:593-602` | `register_device` swallows `ValueError`, returns success with `assigned=None`. Surface `name_not_applied`. | 7 |

### API design & parity

| ID | Sev | Where | Problem → Fix | Step |
|----|-----|-------|---------------|------|
| API1 | Med | `mcp_server.py:179-188` | MCP `set_ac` omits `fan_speed` (HTTP/core have it). Add the param. | 8 |
| API2 | Med | `core.py:483-553` | `fan_speed` silently dropped on physical ACs (returns success). Raise `ValueError`. | 7 |
| API4 | Med | `server.py:46-52,300`; `logic_bridge.py:88-89` | `ACPayload` dual `mode`/`system_mode`; wire uses `system_mode`, public/MCP use `mode`. Pick one canonical input; document alias. | 8 |
| API5 | Low | `mcp_server.py:182-187`; `core.py:22,500-501,526-527` | MCP/README mode list (6 modes) disagrees with `THERMO_VALID_MODES` (9). Align docs to validator. | 9 |
| API6 | Low | `mcp_server.py`; `server.py:200-202,313-317` | No MCP `unregister_node`/`get_metadata`. Add them. | 8 |
| API7 | Low | `server.py:145-159,278-281` | `/api/climate` vs `/api/sensors` vs `/api/acs` overlap. Add a "which surface" note to MCP docstrings. | 9 |
| API8 | Low | `server.py:135-318` | No `/health` or `/version`. Add unauth `/health` (+ `bridge_ready`) and `/version`. | 8 |
| API9 | Low | `server.py:135-317` | Raw dict/list responses; empty OpenAPI. Add tags + response models. | Defer |
| API11 | Low | `server.py:58-61,205-243,283-305` | `_get_params` returns strings; per-handler `int()/float()` coercion runs **outside** `_wrap` → bad GET value yields uncaught 500. Centralize typed coercion. | 8 |

### Packaging & repo hygiene

| ID | Sev | Where | Problem → Fix | Step |
|----|-----|-------|---------------|------|
| P1 | Med | `pyproject.toml:11-22` | Deps fully unpinned → non-reproducible. Add compatible-release bounds + lockfile. | 9 |
| P2 | Med | `pyproject.toml:32-45` | ruff/black/mypy configured but never installed/run; `strict=true` aspirational. Add dev extra; run mypy once. | 0/9 |
| P3 | Med | `.github/workflows/python-publish.yml` | Only publishes; no lint/test gate on push/PR. Add `test.yml`; `needs: test`. | 9 |
| P4 | Low | `build/`, `matter_web_controller.egg-info/` | Stale build artifacts out of sync (egg-info reports `0.28.1` vs `0.27.0`). Untracked — just `rm -rf`. | 9 |
| P5 | Low | `.DS_Store`, `cli/.DS_Store` | macOS cruft (gitignored). `find . -name .DS_Store -delete`. | 9 |
| P6 | Low | `devices_cache.txt` | JSON content with `.txt` extension. Rename → `.json` (+ path constant, `.gitignore`). | 9 |

### Testing

| ID | Sev | Where | Problem → Fix | Step |
|----|-----|-------|---------------|------|
| T1 | Med | `pyproject.toml`; `dev/smoke.sh`; `dev/fake_server.py` | No pytest suite/config/dep. Add `tests/` + dev extra + pytest config. | 0 |
| T2 | Med | `core.py:28-33,95-112,353-392,439-456` | Highest-value pure functions untested. `test_conversions.py` + `test_serializers.py`. | 0/4 |
| T3 | Low | `core.py:250-286,61-69,339-351` | `get_status` dedup + logical-first untested in isolation. `test_status.py`. | 5 |
| T4 | Low | `dev/smoke.sh:29-66` | Smoke assertions need 2 live procs. Port single-instance ones to httpx `ASGITransport`. | 0 |
| T5 | Low | `.github/workflows/python-publish.yml` | Releases unguarded (no test job). `needs: test`. (pairs with P3) | 9 |
| T6 | Low | `core.py:104-112,310-312,329-330,543-550` | Lossy mired↔kelvin + centi rounding untested edges. Parametrized boundary tests. | 4 |

### Documentation

| ID | Sev | Where | Problem → Fix | Step |
|----|-----|-------|---------------|------|
| D1 | High→Med | `README.md:166-197`; `mcp_server.py:179-188` | README AC docs + MCP `set_ac` omit `fan_speed` (the v0.27.0 feature). Document + expose. (pairs with API1) | 8/9 |
| D2 | Med | `README.md:245` | MCP tool list missing `get_climate`, `list_acs`, `get_ac`, `set_ac`. Add them. | 9 |
| D3 | Med→Low | `README.md:162`; `server.py:246` | README names non-existent `/api/occupancy/stream` (real: `/api/subscribe`). Fix prose. | 9 |
| D4 | Med | `README.md:91-95`; `mcp_server.py:209-214` | `matter-mcp` table missing `--transport/--mcp-host/--mcp-port`. Add rows + LAN note. | 9 |
| D5 | Med | `CHANGELOG.md:3-9` | No entry for the SSE/HTTP MCP transport (commit `7b76629`). Add it. | 9 |
| D6 | Med | `CLAUDE.md:44-50` | "Adding an endpoint" checklist ignores MCP tool list + transport docs. Extend it. | 9 |
| D7 | Low | `README.md:245` | "one per REST endpoint" is false (`/api/unregister`, SSE, `/api/metadata` HTTP-only). Soften. | 9 |
| D8 | Low | `README.md:183` | README omits `system_mode` synonym for `mode`. Note it. | 9 |
| D9 | Low | `README.md:83-84`; `server.py:334` | Warning condition described as 0.0.0.0-only; code warns for any non-loopback. Reword. | 9 |

---

## 7. Critic gaps (issues the dimensional audit did not cover)

| ID | Sev | Where | Problem → Fix | Step |
|----|-----|-------|---------------|------|
| G1 | High | `matter_bridge.py:41-43,76-77`; `logic_bridge.py:100,126-129` | All state (caches + `matter_storage` fabric keys) uses bare CWD-relative paths → running from another dir silently forks state / re-commissions the fabric. Add `cli/paths.py` + `--data-dir`; log absolute storage path. | 3 |
| G2 | High | `logic_bridge.py:131-144`; `core.py:557-559,88-93` | A server can register itself / peers can mutually federate → device lists balloon, same `dev_*` under multiple node_ids; only `get_status` dedups. Reject self-registration; dedup in the enumerator; tag origin. | 5 (dedup) + 8 (reject) |
| G3 | Med | `matter_bridge.py:285-287,38`; `core.py:613-618`; `server.py:248-252` | `occupancy_history`/`occupancy_subscribers` grow unbounded — never pruned on unregister/dedupe/disconnect. Prune on `unregister_node`/`dedupe`; delete empty SSE keys. | 6 |
| G4 | Med | `matter_bridge.py:196-228,297,133-134` | `_migrate_ids` runs on every event; legacy→stable mapping can never fire for hashed IDs, yet recomputes each event (and re-runs migration on legacy data with no one-shot guard). Run once at startup + version marker. | 2 |
| G5 | Med | `matter_bridge.py:81-91,93-105`; `server.py:96-118` | Failed Matter startup → app serves a zombie controller; aiohttp session + subprocess leak on the early-return paths; no readiness gate. Close session/terminate subprocess on failure; `/health` reflects `is_ready()`; consider hard-fail. | 8 |
| G6 | Med | `server.py:18`; `matter_bridge.py:133-134`; `logic_bridge.py:118-119,160-161` | No log-level control, no request/access logging, errors vanish into `pass`. Add `--log-level`; request-logging middleware (redact api_key); log swallowed federation errors. | 9 |
| G7 | Low | `matter_bridge.py:73-79,119-126` | Matter subprocess: stdout/stderr uncaptured, no exit detection/restart, fixed `sleep(2.0)` startup race. Route output to logging; watch `process.wait()`; drop the magic sleep. | 9 (opt) |
| G8 | Med | `bridge_cache.json`, `devices_cache.txt`, `names_cache.json`, `occupancy_cache.json`, `matter_storage/`, `build/`, `*.egg-info/` | Live runtime state + stale build artifacts sit in the working tree (all currently **untracked** — verified via `git ls-files`). Keep untracked; exclude from sdist/wheel; tie state to `--data-dir`; document that `matter_storage/` holds fabric keys. | 9 |

---

## 8. Suggested PR / commit boundaries

Ship as ~9 small PRs matching the steps (Step 0 first). Each PR: one step, tests green,
`dev/smoke.sh` green, a CHANGELOG line. Risk-retiring PRs (Steps 1–3) are tiny and should
merge first and fast. Bump version per the project's existing scheme as the surface changes
(POST-only mutations in Step 8 is the one behavior-breaking change for any GET-based caller —
note it prominently in CHANGELOG and update `dev/smoke.sh` + README in the same PR).

**Definition of done for the whole effort:** event loop never blocks on a slow peer; caches
are atomic; `set_ac` heat-mode works; `register_device` names the right device; no
`bridge._private` access from `core`/`server`; secrets never in URLs; mutations are POST;
`pytest` + lint + mypy gate CI; README/CHANGELOG/CLAUDE.md match the code.
