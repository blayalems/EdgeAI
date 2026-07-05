/**
 * BananaGuard — TTN webhook → Google Sheets (lowest-code cloud log).
 *
 * Setup (once, ~5 minutes, zero infrastructure):
 *  1. sheets.new → name the spreadsheet "BananaGuard log".
 *  2. Extensions → Apps Script → paste this file → Deploy → New deployment
 *     → type "Web app" → execute as Me, access "Anyone" → copy the URL.
 *  3. TTN console → Application → Integrations → Webhooks → Custom:
 *     base URL = the deployment URL, format JSON, enabled event: uplink.
 *  4. (Optional) add ?token=YOURSECRET to the webhook URL and set the same
 *     value in TOKEN below.
 *
 * One row per uplink. Uses decoded_payload from decoder/
 * ttn_payload_decoder.js when present, else decodes frm_payload here
 * (same 9-byte v1 contract as firmware/main/lora_telemetry.h).
 */
var TOKEN = '';                 // '' disables the token check
var SHEET_NAME = 'uplinks';
var ACTIONS = ['LOG', 'SPRAY', 'LOCKOUT', 'FAULT'];

var HEADER = ['received_at', 'device_id', 'f_cnt', 'n_pest', 'soil_safe',
              'soil_fault', 'camera_fault', 'infer_ready', 'lockout_active',
              'soil_vwc_pct', 'batt_mv', 'action', 'sprays_today',
              'rssi', 'snr', 'sf', 'raw_hex'];

function decodeRaw(bytes) {
  if (bytes.length !== 9 || bytes[0] !== 1) return null;
  var f = bytes[3];
  return {
    n_pest: (bytes[1] << 8) | bytes[2],
    soil_safe: !!(f & 1), soil_fault: !!(f & 2), camera_fault: !!(f & 4),
    infer_ready: !!(f & 8), lockout_active: !!(f & 16),
    soil_vwc_pct: bytes[4] === 255 ? '' : bytes[4],
    batt_mv: (bytes[5] << 8) | bytes[6],
    action: ACTIONS[bytes[7]] || 'UNKNOWN',
    sprays_today: bytes[8]
  };
}

function doPost(e) {
  if (TOKEN && (!e.parameter || e.parameter.token !== TOKEN)) {
    return ContentService.createTextOutput('forbidden');
  }
  var msg = JSON.parse(e.postData.contents);
  var up = msg.uplink_message || {};
  var d = up.decoded_payload;
  var raw = up.frm_payload
    ? Utilities.base64Decode(up.frm_payload).map(function (b) { return b & 255; })
    : null;
  if (!d || d.n_pest === undefined) d = raw ? decodeRaw(raw) : null;
  if (!d) return ContentService.createTextOutput('ignored');

  var rx = (up.rx_metadata || [{}])[0];
  var lora = ((up.settings || {}).data_rate || {}).lora || {};
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME)
      || SpreadsheetApp.getActiveSpreadsheet().insertSheet(SHEET_NAME);
  if (sheet.getLastRow() === 0) sheet.appendRow(HEADER);

  sheet.appendRow([
    up.received_at || new Date().toISOString(),
    (msg.end_device_ids || {}).device_id || 'unknown',
    up.f_cnt || '',
    d.n_pest, d.soil_safe, d.soil_fault, d.camera_fault, d.infer_ready,
    d.lockout_active,
    d.soil_vwc_pct === null ? '' : d.soil_vwc_pct,
    d.batt_mv, d.action, d.sprays_today,
    rx.rssi || '', rx.snr || '', lora.spreading_factor || '',
    raw ? raw.map(function (b) {
      return ('0' + b.toString(16)).slice(-2);
    }).join('') : ''
  ]);
  return ContentService.createTextOutput('ok');
}
