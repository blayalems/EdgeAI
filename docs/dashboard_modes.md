# BananaGuard dashboard modes

The dashboard has two independent choices:

- **View mode** controls presentation: `simple` or `advanced`.
- **Data mode** controls provenance: `demo` or `actual`.

Changing view mode must never change the data source. Changing data mode must
stop the previous source and start the new source without projecting nodes,
logs, history, alerts, or measurements across that boundary. The two stores stay
isolated. Actual may retain its last-confirmed cache while Demo is open so that
returning to Actual can show source-labelled historical data during reconnect.

## Demo mode

Demo mode is a local interface and training simulation. It may generate
synthetic nodes, telemetry, actions, camera overlays, model metrics, weather,
power estimates, and impact examples. Every route and event must remain visibly
labelled `DEMO` or `SYNTHETIC`; generated values must never be exported as field
evidence or promoted to Actual mode.

Demo mode does not require a backend. A backend simulator is allowed only when
the server was started with simulator support and the resulting records carry
`source_kind=simulator`.

## Actual mode

Actual mode is a read-only field-monitoring surface. It never starts the local
random walk and never falls back to Demo when the API is empty or unreachable.
Its connection states are:

1. `setup_required` -- no compatible API is configured.
2. `connecting` -- the API capability handshake is in progress.
3. `empty` -- the API is reachable but has no field uplinks.
4. `fresh` -- the selected node has a field sample inside the server-provided
   freshness window.
5. `stale` -- the API is reachable but the selected node is outside that
   window.
6. `paused` -- refresh is intentionally paused; the source identity remains
   visible.
7. `error` -- the API cannot be reached or returned an incompatible contract.

Actual mode may retain a last-confirmed sample during `stale` or `error`, but
must label it historical and preserve its server timestamp and source.

### Current payload-backed fields

The nine-byte v1 uplink supports only:

- rolling pest count, EIL and aggregation-window metadata supplied by the API;
- soil-safe, soil-fault, camera-fault, inference-ready and lockout flags;
- soil volumetric-water-content percentage when the sensor value is valid;
- battery millivolts and a clearly labelled server-derived percentage estimate;
- last reported action and sprays-today count;
- TTN receive time, device ID, frame counter, RSSI, SNR and spreading factor;
- server-derived freshness, status and safety-invariant checks.

Everything else is unavailable from telemetry until a versioned contract
provides it. This includes live images, species counts, confidence, model
metrics, temperature, humidity, EC, pH, weather, solar/load data, autonomy,
spray volume, pesticide savings, carbon impact, firmware/model identity and
physical location. Display name, block and optional coordinates may instead be
supplied as explicitly managed local node-registry metadata; they are not
uplink measurements.

`SPRAY` is a completed/reported event, not proof that the actuator is currently
energized. A pest count above EIL with an unsafe soil gate is `HELD`, even when
the v1 firmware action field is `LOG`.

## Source contract

Stored records use one of three provenance values:

- `field` -- accepted by the authenticated TTN webhook.
- `simulator` -- accepted by the opt-in, localhost-only demo endpoint.
- `legacy_unknown` -- rows created before provenance was recorded.

Actual APIs default to `field`. New simulator ingest requires an explicitly
enabled demo backend; stored simulator rows remain available only through an
explicit `source=simulator` query. `legacy_unknown` is never treated as field
evidence.

The capability endpoint is the handshake for Actual mode. It exposes the API
version, server clock, backend profile, field-ingest configuration, stale
threshold, read-only status, payload versions, declared capabilities and
field-readiness flag. Clients
must reject incompatible or write-capable contracts instead of silently
entering Demo.

History and CSV export are optional declared capabilities. Actual requests or
shows those features only when the corresponding capability is explicitly
`true`; current telemetry and source-backed logs remain usable without them.

## Deployment contract

- GitHub Pages and a directly opened standalone default to Demo.
- The local Python server and packaged desktop executable may provide Actual
  mode over the same origin.
- A standalone opened with `file://` directs the operator to the local server;
  it does not use an unauthenticated cross-origin telemetry connection.
- Mobile or non-loopback Actual deployments require HTTPS termination, viewer
  authentication and an explicit origin allowlist before they are enabled.
- Actual remains read-only. Remote actuator or configuration commands are a
  separate safety project requiring authenticated roles, idempotent command
  IDs, expiry, acknowledgement/readback, audit records and firmware interlocks.

## Field-readiness boundary

Dashboard connectivity is not evidence that the physical system is field
ready. `field_ready` remains false until the target board and camera are
verified, peak memory fits, a real four-class INT8 model is installed, the
authorized LoRaWAN plan and credentials are configured, soil/EIL calibration is
complete, actuator safety history survives resets, and physical bench and field
tests pass.
