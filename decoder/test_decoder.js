/**
 * Unit tests for the TTN uplink decoder. Run: node test_decoder.js
 * Byte vectors mirror the encoding in firmware lora_encode() — if
 * lora_telemetry.h changes, both sides must change in the same commit.
 */
const assert = require('assert');
const { decodeUplink } = require('./ttn_payload_decoder.js');

function dec(bytes) { return decodeUplink({ bytes, fPort: 1 }); }

// Nominal: 7 pests, soil safe + infer ready, VWC 38 %, 3921 mV, SPRAY, 2 today
let r = dec([0x01, 0x00, 0x07, 0x09, 38, 0x0f, 0x51, 0x01, 0x02]).data;
assert.strictEqual(r.n_pest, 7);
assert.strictEqual(r.soil_safe, true);
assert.strictEqual(r.soil_fault, false);
assert.strictEqual(r.camera_fault, false);
assert.strictEqual(r.infer_ready, true);
assert.strictEqual(r.lockout_active, false);
assert.strictEqual(r.soil_vwc_pct, 38);
assert.strictEqual(r.batt_mv, 0x0f51);
assert.strictEqual(r.action, 'SPRAY');
assert.strictEqual(r.sprays_today, 2);

// uint16 big-endian boundaries
r = dec([0x01, 0xff, 0xff, 0x00, 0, 0xff, 0xff, 0x00, 0]).data;
assert.strictEqual(r.n_pest, 65535);
assert.strictEqual(r.batt_mv, 65535);

// Soil fault: VWC sentinel 0xFF -> null, FAULT action
r = dec([0x01, 0x00, 0x00, 0x02, 0xff, 0x0d, 0xac, 0x03, 0x00]).data;
assert.strictEqual(r.soil_fault, true);
assert.strictEqual(r.soil_vwc_pct, null);
assert.strictEqual(r.action, 'FAULT');

// Lockout flag (bit4)
r = dec([0x01, 0x00, 0x09, 0x19, 41, 0x0e, 0x74, 0x02, 0x04]).data;
assert.strictEqual(r.lockout_active, true);
assert.strictEqual(r.action, 'LOCKOUT');
assert.strictEqual(r.sprays_today, 4);

// Wrong length and wrong version -> errors, no data
assert.ok(dec([0x01, 0x00]).errors);
assert.ok(dec([0x02, 0, 0, 0, 0, 0, 0, 0, 0]).errors);

console.log('decoder: all tests passed');
