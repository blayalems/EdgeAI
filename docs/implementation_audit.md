# Implementation audit and manuscript traceability

Audit date: 2026-07-10

This review treats `manuscript.tex` as the intended study contract and the
repository as the implemented contract. The PR fixes software defects that can
be verified without field hardware; it deliberately fails closed where a board,
radio plan, calibration, trained model, or physical test result is still
missing.

## Resolved in this branch

| Study contract | Prior implementation risk | Resolution |
|---|---|---|
| Three target species plus a negative class | ML and firmware only treated class 1 (`weevil`) as a pest | Defaults now use the three manuscript species; every non-negative class is eligible at the confidence gate; model output must contain four classes. |
| Stratified 70/15/15 held-out design | Path hashing only approximated stratification, and new files could enter the supposedly frozen test set | Per-class deterministic allocation now freezes membership, labels, splits, and content hashes in a versioned manifest. Legacy held-out membership is preserved during migration. |
| `N_hat_pest > N_EIL` with `N_EIL = 5` | One frame produced at most one detection in a 30-minute window, making spray unreachable | Disconnected motion components are classified as bounded individual ROIs; the configured per-frame capacity exceeds the strict threshold and has a host reachability test. |
| Rolling 30-minute count | Zero-detection cycles and reads did not always expire stale buckets | Expiry runs on every add and read, including zero detections and clock rollback. |
| Soil-gated safe actuation | Low-level actuation trusted several caller decisions and reported a requested spray even if the driver refused | The relay driver independently rechecks faults, soil, battery, daily cap, and minimum gap; telemetry/logging use the post-actuation result. |
| Honest live monitoring | Live TTN mode retained fabricated camera, class, and environment values and did not age stale nodes | The dashboard distinguishes measured, simulated, reference, unavailable, fresh, and stale values; adds node-scoped CSV export and safety-invariant alerts. |
| Paired count equivalence | Analysis did not produce the manuscript's paired TOST result | Detection analysis now reports the paired TOST, 90% confidence interval, and the pre-registered +/-1 margin. |
| Reliability, false-spray, and autonomy inference | Only descriptive/projected statistics were available | Exact-binomial reliability/false-spray analysis and a one-sample nightly-slope autonomy test now report p-values and one-sided confidence bounds. |
| Literal `3 x MAD` rule | Code silently multiplied MAD by 1.4826 | Literal `3 x MAD` is now the default; normal-consistency scaling is explicit and optional. |

## Field-blocking items intentionally left open

1. **Controller target and pin map.** The manuscript specifies ESP32-C6, but
   the existing firmware is wired for classic ESP32 DevKit/WROOM-32 and uses
   pins absent on C6. The branch adds a compile-time target guard instead of
   inventing a board map. Select the exact C6 board/revision, assign every
   camera, LoRa, relay, I2C, and ADC pin, then verify boot straps, USB pins,
   ADC channels, and peak RAM on hardware. Espressif's C6 DevKitC-1 reference
   is: https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32c6/esp32-c6-devkitc-1/user_guide.html
2. **Camera choice.** The manuscript and dashboard named OV5640, while the
   implemented ArduCAM SPI/FIFO driver probes OV5642. Confirm the purchased
   module before aligning the driver, bill of materials, and manuscript.
3. **LoRaWAN plan and authorization.** `US915` remains a prototype placeholder
   and real RF initialization is blocked by `BG_LORA_PLAN_VERIFIED=0`. Confirm
   the exact gateway/operator frequency plan and Philippine authorization
   before enabling it. A nominal "915 MHz" radio is not sufficient evidence
   that the US915 channel plan is correct.
4. **Deployment assets.** `firmware/main/model_data.cc` is still a non-runnable
   placeholder; OTAA identifiers/keys are placeholders; sensor coefficients
   and EIL inputs require bench/field calibration.
5. **Manuscript asset.** `proposal_2_mockup.png` was not included with the
   supplied LaTeX, so the manuscript cannot yet compile as delivered.
6. **Firmware build verification.** ESP-IDF is not installed in the audit
   environment. Host mirrors and static contract tests cover decision,
   aggregation, reachability, and interlocks, but the firmware still requires
   an ESP-IDF build and on-board smoke test.

## Manuscript corrections before the final defense

- The IPO figure lists thermal data, soil pH, and LED lures, but those devices
  are absent from the hardware architecture, bill of materials, telemetry, and
  implemented node. Remove them from the study contract or explicitly add and
  validate the hardware; dashboard reference/simulated values are not evidence
  of sensing.
- Resolve OV5640 versus OV5642 after confirming the purchased camera module.
- Express the one-sided hypotheses with the boundary in the null (for example,
  reliability `H0: p <= 0.95` versus `Ha: p > 0.95`, and autonomy
  `H0: mean <= 7 days` versus `Ha: mean > 7 days`).
- Clarify whether `N_EIL` is instances per frame or detections in the trailing
  30-minute window. The implementation uses classified ROI detections in that
  rolling window; the EIL derivation currently describes instances per frame.
- Recalculate the false-spray sample size as described below and replace the
  remaining `% REVIEW` bibliography/agronomic placeholders with verified
  sources and field-calibrated values.

## Statistical planning implication

The manuscript's 45 saturated-soil opportunities cannot demonstrate a
false-spray rate below 5% at one-sided alpha 0.05, even with zero false sprays:
the exact-binomial p-value is `0.95^45`, about 0.099. At least **59 independent
non-spray opportunities with zero false sprays** are needed for that narrow
best-case proof; any observed false spray increases the requirement. Use
`analysis/actuation_reliability.py` during final trial planning rather than
treating an observed percentage as confirmation.

Battery autonomy inference also needs at least two independent solar-free
night slopes; additional nights are strongly preferable before a defense-level
claim.
