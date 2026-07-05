/**
 * BananaGuard servo test rig — presents specimen cards at known densities
 * in front of a node under test (Phase 1 controlled trials, Weeks 13-15).
 *
 * Hardware: any Arduino (Uno/Nano) + SG90/MG996R servo on D9, carousel
 * disk with POSITIONS card slots. Cards hold pinned specimens / printed
 * decoys at densities you record in DENSITIES below.
 *
 * The rig is intentionally on Arduino while the node is ESP-IDF: zero
 * shared code, so this choice does not preempt the Week-2 IDF-vs-Arduino
 * trade-off for the node itself.
 *
 * Serial protocol @115200 (also usable by hand in the Serial Monitor):
 *   g<n>   go to position n (0-based), e.g. g3
 *   r      run one full randomized trial sequence
 *   a      run ARDUINO_TRIALS back-to-back randomized sequences
 *   h      home to position 0
 *   ?      print state
 *
 * Every presentation prints a CSV line — capture the port with
 * ../ground_truth_logger.py to build the trial ground-truth file:
 *   RIG,<millis>,<trial>,<position>,<density>
 */
#include <Servo.h>

const uint8_t SERVO_PIN = 9;
const uint8_t POSITIONS = 6;
// Specimens per card, index = carousel position. EDIT to match your disk.
const uint8_t DENSITIES[POSITIONS] = {0, 1, 2, 4, 6, 9};
// Angle for each slot (SG90: 0-180 deg usable).
const uint8_t ANGLES[POSITIONS] = {5, 35, 65, 95, 125, 155};

const unsigned long PRESENT_MS = 65000; // > 2 node capture intervals
const unsigned long SETTLE_MS = 1200;   // servo travel + wobble damp-out
const uint8_t ARDUINO_TRIALS = 5;

Servo carousel;
uint8_t pos = 0;
unsigned trial = 0;

void goTo(uint8_t p) {
  pos = p % POSITIONS;
  carousel.write(ANGLES[pos]);
  delay(SETTLE_MS);
}

void present(uint8_t p) {
  goTo(p);
  Serial.print(F("RIG,"));
  Serial.print(millis());
  Serial.print(',');
  Serial.print(trial);
  Serial.print(',');
  Serial.print(pos);
  Serial.print(',');
  Serial.println(DENSITIES[pos]);
  delay(PRESENT_MS);
}

// Fisher-Yates over position indices -> each trial presents every density
// once, in random order (order effects average out across trials).
void runTrial() {
  uint8_t order[POSITIONS];
  for (uint8_t i = 0; i < POSITIONS; i++) order[i] = i;
  for (int8_t i = POSITIONS - 1; i > 0; i--) {
    uint8_t j = random(i + 1);
    uint8_t t = order[i]; order[i] = order[j]; order[j] = t;
  }
  trial++;
  Serial.print(F("# trial "));
  Serial.println(trial);
  for (uint8_t i = 0; i < POSITIONS; i++) present(order[i]);
  goTo(0);
  Serial.println(F("# trial done"));
}

void setup() {
  Serial.begin(115200);
  randomSeed(analogRead(A0));
  carousel.attach(SERVO_PIN);
  goTo(0);
  Serial.println(F("# BananaGuard servo rig ready. g<n>/r/a/h/?"));
}

void loop() {
  if (!Serial.available()) return;
  char c = Serial.read();
  if (c == 'g') {
    long n = Serial.parseInt();
    present((uint8_t)n);
  } else if (c == 'r') {
    runTrial();
  } else if (c == 'a') {
    for (uint8_t i = 0; i < ARDUINO_TRIALS; i++) runTrial();
  } else if (c == 'h') {
    goTo(0);
  } else if (c == '?') {
    Serial.print(F("# pos="));
    Serial.print(pos);
    Serial.print(F(" density="));
    Serial.print(DENSITIES[pos]);
    Serial.print(F(" trial="));
    Serial.println(trial);
  }
}
