/**
 * TTN (The Things Stack v3) uplink payload formatter — BananaGuard v1.
 *
 * Paste into: Application → Payload formatters → Uplink → Custom Javascript.
 * Decodes the 9-byte big-endian payload defined in
 * firmware/main/lora_telemetry.h (the single payload contract).
 *
 *   offset size field
 *   0      1    payload version (0x01)
 *   1      2    N̂_pest rolling-window count (uint16)
 *   3      1    flags: bit0 soil_safe, bit1 soil_fault, bit2 camera_fault,
 *                      bit3 infer_ready, bit4 lockout_active
 *   4      1    soil VWC % (0-100, 0xFF = fault/invalid)
 *   5      2    battery millivolts (uint16)
 *   7      1    action code (0 LOG, 1 SPRAY, 2 LOCKOUT, 3 FAULT)
 *   8      1    sprays_today
 */
var ACTIONS = ['LOG', 'SPRAY', 'LOCKOUT', 'FAULT'];

function decodeUplink(input) {
  var b = input.bytes;
  if (b.length !== 9) {
    return { errors: ['expected 9 bytes, got ' + b.length] };
  }
  if (b[0] !== 0x01) {
    return { errors: ['unknown payload version 0x' + b[0].toString(16)] };
  }
  var flags = b[3];
  return {
    data: {
      version: b[0],
      n_pest: (b[1] << 8) | b[2],
      soil_safe: (flags & 0x01) !== 0,
      soil_fault: (flags & 0x02) !== 0,
      camera_fault: (flags & 0x04) !== 0,
      infer_ready: (flags & 0x08) !== 0,
      lockout_active: (flags & 0x10) !== 0,
      soil_vwc_pct: b[4] === 0xff ? null : b[4],
      batt_mv: (b[5] << 8) | b[6],
      action: ACTIONS[b[7]] || 'UNKNOWN',
      sprays_today: b[8]
    }
  };
}

/* Node/tests only — TTN ignores module.exports. */
if (typeof module !== 'undefined') module.exports = { decodeUplink: decodeUplink };
