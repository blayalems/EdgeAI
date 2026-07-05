# decoder/ — TTN uplink payload formatter

`ttn_payload_decoder.js` decodes the 9-byte binary uplink (spec:
`firmware/main/lora_telemetry.h`) into named fields. Install it in The
Things Stack v3 console: **Application → Payload formatters → Uplink →
Custom Javascript formatter**, paste the whole file.

Decoded fields: `n_pest`, `soil_safe`, `soil_fault`, `camera_fault`,
`infer_ready`, `lockout_active`, `soil_vwc_pct` (`null` on sensor fault),
`batt_mv`, `action` (`LOG|SPRAY|LOCKOUT|FAULT`), `sprays_today`.

Test locally (no TTN account needed):

```sh
node test_decoder.js
```

The backend (`backend/decode_payload.py`) carries a Python port of the
same logic with the same test vectors, so a webhook that delivers only raw
`frm_payload` still gets decoded identically.
