/*
  Dedicated TMSi trigger Arduino

  Serial protocol from GUI:
    TRIG:<code>
    VICON:START
    VICON:MARKER
    VICON:STOP

  Examples:
    TRIG:1
    TRIG:2
    TRIG:3
    TRIG:5
    VICON:START
    VICON:MARKER
    VICON:STOP

  Logic:
    - Only the effective marker code is sent from the GUI.
    - Repeated markers are valid and should generate another pulse.
    - Code 0 is ignored on the Arduino side because idle is all-lines-low.
    - Marker code is encoded as a 4-bit value on TMSi IN0..IN3.
    - Pulse width is short, then all lines return LOW.
    - Vicon uses a dedicated pulse output on D11.
    - VICON:START, VICON:MARKER, and VICON:STOP generate a pulse on D11.
    - D13 stays HIGH between VICON:START and VICON:STOP.

  Wiring:
    TMSi #1
      Arduino GND -> TMSi GND (pin 19/20/21/22)
      Arduino D2  -> TMSi TRIGGER IN0
      Arduino D3  -> TMSi TRIGGER IN1
      Arduino D4  -> TMSi TRIGGER IN2
      Arduino D5  -> TMSi TRIGGER IN3

    TMSi #2
      Arduino GND -> TMSi GND (pin 19/20/21/22)
      Arduino D6  -> TMSi TRIGGER IN0
      Arduino D7  -> TMSi TRIGGER IN1
      Arduino D8  -> TMSi TRIGGER IN2
      Arduino D9  -> TMSi TRIGGER IN3

    Vicon
      Arduino D11 -> Vicon trigger input signal
      Arduino GND -> Vicon trigger ground

    Experiment state output
      Arduino D12 -> second Arduino digital input
      Arduino GND -> second Arduino GND
*/

const int trigPinsA[4] = {2, 3, 4, 5};  // TMSi #1: IN0, IN1, IN2, IN3
const int trigPinsB[4] = {6, 7, 8, 9};  // TMSi #2: IN0, IN1, IN2, IN3
const int viconPin = 11;                // Vicon trigger pulse output
const int experimentStatePin = 12;      // HIGH while experiment recording is active
const unsigned long pulseWidthMs = 20;

String inputLine = "";

void setTriggerBits(uint8_t code) {
  for (int i = 0; i < 4; i++) {
    int bitState = ((code >> i) & 0x01) ? HIGH : LOW;
    digitalWrite(trigPinsA[i], bitState);
    digitalWrite(trigPinsB[i], bitState);
  }
}

void clearTriggerBits() {
  for (int i = 0; i < 4; i++) {
    digitalWrite(trigPinsA[i], LOW);
    digitalWrite(trigPinsB[i], LOW);
  }
}

void pulseVicon() {
  digitalWrite(viconPin, HIGH);
  delay(pulseWidthMs);
  digitalWrite(viconPin, LOW);
}

void pulseCode(uint8_t code) {
  if (code == 0) {
    return;
  }

  setTriggerBits(code);
  delay(pulseWidthMs);
  clearTriggerBits();
}

void handleCommand(const String& line) {
  if (line == "VICON:START") {
    digitalWrite(experimentStatePin, HIGH);
    pulseVicon();
    Serial.print("OK ");
    Serial.println(line);
    return;
  }

  if (line == "VICON:MARKER") {
    pulseVicon();
    Serial.print("OK ");
    Serial.println(line);
    return;
  }

  if (line == "VICON:STOP") {
    digitalWrite(experimentStatePin, LOW);
    pulseVicon();
    Serial.print("OK ");
    Serial.println(line);
    return;
  }

  if (!line.startsWith("TRIG:")) {
    return;
  }

  int code = line.substring(5).toInt();
  if (code < 0 || code > 15) {
    Serial.print("ERR invalid code: ");
    Serial.println(code);
    return;
  }

  pulseCode((uint8_t)code);
  Serial.print("OK ");
  Serial.println(code);
}

void setup() {
  for (int i = 0; i < 4; i++) {
    pinMode(trigPinsA[i], OUTPUT);
    pinMode(trigPinsB[i], OUTPUT);
  }
  pinMode(viconPin, OUTPUT);
  pinMode(experimentStatePin, OUTPUT);
  clearTriggerBits();
  digitalWrite(viconPin, LOW);
  digitalWrite(experimentStatePin, LOW);

  Serial.begin(115200);
  Serial.setTimeout(10);
  Serial.println("Trigger Arduino ready");
}

void loop() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\n') {
      inputLine.trim();
      if (inputLine.length() > 0) {
        handleCommand(inputLine);
      }
      inputLine = "";
    } else if (c != '\r') {
      inputLine += c;
    }
  }
}
