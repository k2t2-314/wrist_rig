/*
  Timestamped, non-blocking trigger generator for two TMSi SAGA systems.

  New serial protocol (newline terminated, 115200 baud):
    TRIG,<event_id>,<code>
    TRIGVICON,<event_id>,<code>
    PING,<event_id>
    VICON,<event_id>,STOP

  Responses:
    ACK,<event_id>,<code>,<rx_us>,<stable_us>,<tx_us>
    PONG,<event_id>,<rx_us>,<tx_us>
    VACK,<event_id>,<rx_us>,<edge_us>,<tx_us>
    BUSY,<event_id>,TMSI
    ERR,<event_id>,<reason>

  Timing meanings:
    rx_us     = micros() when the complete command/newline was received.
    stable_us = micros() just after all eight TMSi output pins were written.
    tx_us     = micros() immediately before the ACK is queued for USB serial.

  The output is cleared later by loop(), so the ACK is not delayed by the
  20 ms pulse. uint32_t subtraction is deliberately used so micros() rollover
  is safe.

  Legacy commands TRIG:<code> and VICON:STOP are retained, but they return the
  old OK response and do not provide host-usable timing metadata.

  Wiring (unchanged from the original sketch):
    TMSi #1 and #2 shared: D2,D3,D4,D5 -> IN0,IN1,IN2,IN3
      Use a breadboard/fan-out so each D2-D5 output drives the same input bit
      on both SAGAs.
    Previous separate TMSi #2 pins D6,D7,D8,D9 are left unused for now.
    Vicon:   D11 -> trigger input
    Arduino GND -> each receiving device's trigger ground
*/

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

const uint8_t TRIG_PINS_A[4] = {2, 3, 4, 5};
// Previous separate SAGA #2 outputs. Kept here for easy rollback if needed.
// const uint8_t TRIG_PINS_B[4] = {6, 7, 8, 9};
const uint8_t VICON_PIN = 11;

const uint32_t PULSE_WIDTH_US = 20000UL;
const uint8_t LINE_CAPACITY = 63;

char lineBuffer[LINE_CAPACITY + 1];
uint8_t lineLength = 0;
bool lineOverflow = false;

bool tmsiPulseActive = false;
uint32_t tmsiPulseStartUs = 0;

bool viconPulseActive = false;
uint32_t viconPulseStartUs = 0;

void setTriggerBits(uint8_t code) {
  for (uint8_t bit = 0; bit < 4; ++bit) {
    const uint8_t state = ((code >> bit) & 0x01U) ? HIGH : LOW;
    digitalWrite(TRIG_PINS_A[bit], state);
    // digitalWrite(TRIG_PINS_B[bit], state);
  }
}

void clearTriggerBits() {
  for (uint8_t bit = 0; bit < 4; ++bit) {
    digitalWrite(TRIG_PINS_A[bit], LOW);
    // digitalWrite(TRIG_PINS_B[bit], LOW);
  }
}

bool parseU32(const char *text, uint32_t &value) {
  if (text == NULL || *text == '\0') {
    return false;
  }

  char *end = NULL;
  const unsigned long parsed = strtoul(text, &end, 10);
  if (end == text || *end != '\0') {
    return false;
  }

  value = (uint32_t)parsed;
  return true;
}

void sendError(uint32_t eventId, const __FlashStringHelper *reason) {
  Serial.print(F("ERR,"));
  Serial.print(eventId);
  Serial.print(',');
  Serial.println(reason);
}

void sendBusy(uint32_t eventId, const __FlashStringHelper *device) {
  Serial.print(F("BUSY,"));
  Serial.print(eventId);
  Serial.print(',');
  Serial.println(device);
}

void sendTriggerAck(
    uint32_t eventId,
    uint8_t code,
    uint32_t rxUs,
    uint32_t stableUs) {
  const uint32_t txUs = micros();
  Serial.print(F("ACK,"));
  Serial.print(eventId);
  Serial.print(',');
  Serial.print(code);
  Serial.print(',');
  Serial.print(rxUs);
  Serial.print(',');
  Serial.print(stableUs);
  Serial.print(',');
  Serial.println(txUs);
}

void startTimedTrigger(
    uint32_t eventId,
    uint8_t code,
    uint32_t rxUs,
    bool legacyResponse,
    bool includeVicon = false) {
  if (code == 0 || code > 15) {
    if (legacyResponse) {
      Serial.println(F("ERR invalid code"));
    } else {
      sendError(eventId, F("invalid_code"));
    }
    return;
  }

  if (tmsiPulseActive) {
    if (legacyResponse) {
      Serial.println(F("ERR busy"));
    } else {
      sendBusy(eventId, F("TMSI"));
    }
    return;
  }

  if (includeVicon && viconPulseActive) {
    if (legacyResponse) {
      Serial.println(F("ERR busy"));
    } else {
      sendBusy(eventId, F("VICON"));
    }
    return;
  }

  // Keep an interrupt from stretching the small gap between individual pin
  // writes. stableUs is taken after every pin contains the requested code.
  noInterrupts();
  setTriggerBits(code);
  if (includeVicon) {
    digitalWrite(VICON_PIN, HIGH);
  }
  interrupts();
  const uint32_t stableUs = micros();

  tmsiPulseStartUs = stableUs;
  tmsiPulseActive = true;
  if (includeVicon) {
    viconPulseStartUs = stableUs;
    viconPulseActive = true;
  }

  if (legacyResponse) {
    Serial.print(F("OK "));
    Serial.println(code);
  } else {
    sendTriggerAck(eventId, code, rxUs, stableUs);
  }
}

void sendPong(uint32_t eventId, uint32_t rxUs) {
  const uint32_t txUs = micros();
  Serial.print(F("PONG,"));
  Serial.print(eventId);
  Serial.print(',');
  Serial.print(rxUs);
  Serial.print(',');
  Serial.println(txUs);
}

void startTimedVicon(uint32_t eventId, uint32_t rxUs, bool legacyResponse) {
  if (viconPulseActive) {
    if (legacyResponse) {
      Serial.println(F("ERR busy"));
    } else {
      sendBusy(eventId, F("VICON"));
    }
    return;
  }

  digitalWrite(VICON_PIN, HIGH);
  const uint32_t edgeUs = micros();
  viconPulseStartUs = edgeUs;
  viconPulseActive = true;

  if (legacyResponse) {
    Serial.println(F("OK VICON:STOP"));
    return;
  }

  const uint32_t txUs = micros();
  Serial.print(F("VACK,"));
  Serial.print(eventId);
  Serial.print(',');
  Serial.print(rxUs);
  Serial.print(',');
  Serial.print(edgeUs);
  Serial.print(',');
  Serial.println(txUs);
}

void handleLegacyCommand(char *line, uint32_t rxUs) {
  if (strcmp(line, "VICON:STOP") == 0) {
    startTimedVicon(0, rxUs, true);
    return;
  }

  if (strncmp(line, "TRIG:", 5) != 0) {
    Serial.println(F("ERR unknown_command"));
    return;
  }

  uint32_t code = 0;
  if (!parseU32(line + 5, code) || code > 15) {
    Serial.println(F("ERR invalid code"));
    return;
  }
  startTimedTrigger(0, (uint8_t)code, rxUs, true);
}

void handleTimedCommand(char *line, uint32_t rxUs) {
  char *save = NULL;
  char *command = strtok_r(line, ",", &save);
  char *eventText = strtok_r(NULL, ",", &save);

  uint32_t eventId = 0;
  if (command == NULL || !parseU32(eventText, eventId)) {
    sendError(0, F("bad_format"));
    return;
  }

  if (strcmp(command, "PING") == 0) {
    if (strtok_r(NULL, ",", &save) != NULL) {
      sendError(eventId, F("bad_format"));
      return;
    }
    sendPong(eventId, rxUs);
    return;
  }

  if (strcmp(command, "TRIG") == 0) {
    char *codeText = strtok_r(NULL, ",", &save);
    uint32_t code = 0;
    if (!parseU32(codeText, code) || code == 0 || code > 15 ||
        strtok_r(NULL, ",", &save) != NULL) {
      sendError(eventId, F("invalid_code"));
      return;
    }
    startTimedTrigger(eventId, (uint8_t)code, rxUs, false);
    return;
  }

  if (strcmp(command, "TRIGVICON") == 0) {
    char *codeText = strtok_r(NULL, ",", &save);
    uint32_t code = 0;
    if (!parseU32(codeText, code) || code == 0 || code > 15 ||
        strtok_r(NULL, ",", &save) != NULL) {
      sendError(eventId, F("invalid_code"));
      return;
    }
    startTimedTrigger(eventId, (uint8_t)code, rxUs, false, true);
    return;
  }

  if (strcmp(command, "VICON") == 0) {
    char *action = strtok_r(NULL, ",", &save);
    if (action == NULL || strcmp(action, "STOP") != 0 ||
        strtok_r(NULL, ",", &save) != NULL) {
      sendError(eventId, F("invalid_vicon_action"));
      return;
    }
    startTimedVicon(eventId, rxUs, false);
    return;
  }

  sendError(eventId, F("unknown_command"));
}

void handleCommand(char *line, uint32_t rxUs) {
  if (strncmp(line, "TRIG:", 5) == 0 || strcmp(line, "VICON:STOP") == 0) {
    handleLegacyCommand(line, rxUs);
  } else {
    handleTimedCommand(line, rxUs);
  }
}

void updatePulseOutputs() {
  const uint32_t nowUs = micros();

  if (tmsiPulseActive &&
      (uint32_t)(nowUs - tmsiPulseStartUs) >= PULSE_WIDTH_US) {
    clearTriggerBits();
    tmsiPulseActive = false;
  }

  if (viconPulseActive &&
      (uint32_t)(nowUs - viconPulseStartUs) >= PULSE_WIDTH_US) {
    digitalWrite(VICON_PIN, LOW);
    viconPulseActive = false;
  }
}

void setup() {
  for (uint8_t bit = 0; bit < 4; ++bit) {
    pinMode(TRIG_PINS_A[bit], OUTPUT);
    // pinMode(TRIG_PINS_B[bit], OUTPUT);
  }
  pinMode(VICON_PIN, OUTPUT);

  clearTriggerBits();
  digitalWrite(VICON_PIN, LOW);

  Serial.begin(1000000);
  Serial.println(F("READY,2"));
}

void loop() {
  updatePulseOutputs();

  while (Serial.available() > 0) {
    const char incoming = (char)Serial.read();

    if (incoming == '\n') {
      const uint32_t rxUs = micros();
      if (lineOverflow) {
        sendError(0, F("line_too_long"));
      } else if (lineLength > 0) {
        lineBuffer[lineLength] = '\0';
        handleCommand(lineBuffer, rxUs);
      }
      lineLength = 0;
      lineOverflow = false;
    } else if (incoming != '\r') {
      if (lineLength < LINE_CAPACITY) {
        lineBuffer[lineLength++] = incoming;
      } else {
        lineOverflow = true;
      }
    }
  }
}
