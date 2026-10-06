#include <AccelStepper.h>

// Pinos dos Motores
#define X_PUL 12
#define X_DIR 14
#define Y_PUL 26
#define Y_DIR 25
#define Z_PUL 32
#define Z_DIR 13

// Pino Auxiliar (D22)
#define PIN_D22 22

// Pinos dos Fins de Curso (D16, D17 e D19)
#define X_ENDSTOP 16
#define Z_ENDSTOP 17
#define Y_ENDSTOP 19

// Parâmetros Mecânicos: 800 pulsos/rev / 8mm de passo = 100 pulsos/mm
const float STEPS_PER_MM = 100.0;

// Limites Virtuais (Soft Limits) em mm
const float MIN_X = 0.0,   MAX_X = 320.0;
const float MIN_Y = -15.0, MAX_Y = 65.0;
const float MIN_Z = 0.0,   MAX_Z = 225.0;

AccelStepper motorX(AccelStepper::DRIVER, X_PUL, X_DIR);
AccelStepper motorY(AccelStepper::DRIVER, Y_PUL, Y_DIR);
AccelStepper motorZ(AccelStepper::DRIVER, Z_PUL, Z_DIR);

bool homedX = false;
bool homedY = false;
bool homedZ = false;
bool emergencyStop = false;

float extractVal(String cmd, char key) {
  int pos = cmd.indexOf(key);
  if (pos == -1) return 0;
  return cmd.substring(pos + 1).toFloat();
}

bool isTriggered(int pin) {
  return digitalRead(pin) == HIGH;
}

void checkEmergency() {
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toUpperCase();
    if (cmd == "ESTOP") {
      emergencyStop = true;
      motorX.setSpeed(0); motorY.setSpeed(0); motorZ.setSpeed(0);
      motorX.moveTo(motorX.currentPosition());
      motorY.moveTo(motorY.currentPosition());
      motorZ.moveTo(motorZ.currentPosition());
      Serial.println("ALARM: PARADA DE EMERGENCIA ACIONADA!");
    } else if (cmd == "ERESET") {
      emergencyStop = false;
      Serial.println("OK");
    }
  }
}

void homeAxis(char axis) {
  float fastSpeed = 20.0 * STEPS_PER_MM;
  float slowSpeed = 12 * STEPS_PER_MM;
  float retractDist = 10.0 * STEPS_PER_MM;

  emergencyStop = false;

  // --- ZERAMENTO DO EIXO Y ---
  if (axis == 'Y') {
    if (!homedX) homeAxis('X');
    if (emergencyStop) return;
    if (!homedZ) homeAxis('Z');
    if (emergencyStop) return;

    long yTargetRel = motorY.currentPosition() + (long)(15.0 * STEPS_PER_MM);
    motorY.setMaxSpeed(50.0 * STEPS_PER_MM);
    motorY.setAcceleration(100.0 * STEPS_PER_MM);
    motorY.moveTo(yTargetRel);
    while (motorY.distanceToGo() != 0) { checkEmergency(); if (emergencyStop) return; motorY.run(); yield(); }

    motorX.setMaxSpeed(50.0 * STEPS_PER_MM); motorZ.setMaxSpeed(50.0 * STEPS_PER_MM);
    motorX.setAcceleration(100.0 * STEPS_PER_MM); motorZ.setAcceleration(100.0 * STEPS_PER_MM);
    motorX.moveTo(255.0 * STEPS_PER_MM); motorZ.moveTo(175.0 * STEPS_PER_MM);
    while (motorX.distanceToGo() != 0 || motorZ.distanceToGo() != 0) {
      checkEmergency(); if (emergencyStop) return; motorX.run(); motorZ.run(); yield();
    }

    if (isTriggered(Y_ENDSTOP)) { Serial.println("ERRO: Fim de curso do eixo Y ja acionado!"); return; }
    motorY.setSpeed(-fastSpeed);
    while (!isTriggered(Y_ENDSTOP)) { checkEmergency(); if (emergencyStop) return; motorY.runSpeed(); yield(); }

    long targetPos = motorY.currentPosition() + (long)retractDist;
    motorY.moveTo(targetPos);
    motorY.setMaxSpeed(fastSpeed);
    while (motorY.distanceToGo() != 0) { checkEmergency(); if (emergencyStop) return; motorY.run(); yield(); }

    motorY.setSpeed(-slowSpeed);
    while (!isTriggered(Y_ENDSTOP)) { checkEmergency(); if (emergencyStop) return; motorY.runSpeed(); yield(); }

    motorY.setCurrentPosition(-1.0 * STEPS_PER_MM);
    motorY.moveTo(10.0 * STEPS_PER_MM);
    while (motorY.distanceToGo() != 0) { checkEmergency(); if (emergencyStop) return; motorY.run(); yield(); }
    homedY = true;
    Serial.println("MSG: Eixo Y Zerado");
    return;
  }

  // --- ZERAMENTO DOS EIXOS X e Z ---
  AccelStepper* motor; int pin;
  if (axis == 'X') { motor = &motorX; pin = X_ENDSTOP; }
  else if (axis == 'Z') { motor = &motorZ; pin = Z_ENDSTOP; }
  else return;

  if (isTriggered(pin)) { Serial.print("ERRO: Fim de curso "); Serial.println(axis); return; }

  motor->setSpeed(-fastSpeed);
  while (!isTriggered(pin)) { checkEmergency(); if (emergencyStop) return; motor->runSpeed(); yield(); }

  long targetPos = motor->currentPosition() + (long)retractDist;
  motor->moveTo(targetPos);
  motor->setMaxSpeed(fastSpeed);
  motor->setAcceleration(100.0 * STEPS_PER_MM);
  while (motor->distanceToGo() != 0) { checkEmergency(); if (emergencyStop) return; motor->run(); yield(); }

  motor->setSpeed(-slowSpeed);
  while (!isTriggered(pin)) { checkEmergency(); if (emergencyStop) return; motor->runSpeed(); yield(); }

  motor->setCurrentPosition(-1.0 * STEPS_PER_MM);
  motor->moveTo(0);
  while (motor->distanceToGo() != 0) { checkEmergency(); if (emergencyStop) return; motor->run(); yield(); }

  if (axis == 'X') homedX = true;
  if (axis == 'Z') homedZ = true;
  Serial.print("MSG: Eixo "); Serial.println(axis);
}

void parseCommand(String cmd) {
  cmd.toUpperCase();

  if (cmd == "ESTOP") {
    emergencyStop = true;
    motorX.setSpeed(0); motorY.setSpeed(0); motorZ.setSpeed(0);
    motorX.moveTo(motorX.currentPosition()); motorY.moveTo(motorY.currentPosition()); motorZ.moveTo(motorZ.currentPosition());
    Serial.println("ALARM: PARADA DE EMERGENCIA ACIONADA!");
    return;
  }

  if (cmd == "ERESET") {
    emergencyStop = false;
    Serial.println("OK");
    return;
  }

  if (cmd == "TOGGLE_D22") {
    bool estadoAtual = digitalRead(PIN_D22);
    digitalWrite(PIN_D22, !estadoAtual);
    Serial.println("OK");
    return;
  }

  if (emergencyStop) {
    Serial.println("AVISO: Sistema em Emergencia.");
    return;
  }

  if (cmd.startsWith("G0")) {
    bool moving = false;
    if (cmd.indexOf("X") != -1) {
      float posX = extractVal(cmd, 'X');
      if (homedX) { posX = max(MIN_X, min(posX, MAX_X)); }
      motorX.moveTo(posX * STEPS_PER_MM); moving = true;
    }
    if (cmd.indexOf("Y") != -1) {
      float posY = extractVal(cmd, 'Y');
      if (homedY) { posY = max(MIN_Y, min(posY, MAX_Y)); }
      motorY.moveTo(posY * STEPS_PER_MM); moving = true;
    }
    if (cmd.indexOf("Z") != -1) {
      float posZ = extractVal(cmd, 'Z');
      if (homedZ) { posZ = max(MIN_Z, min(posZ, MAX_Z)); }
      motorZ.moveTo(posZ * STEPS_PER_MM); moving = true;
    }

    if (moving) {
      // Bloqueia e executa o movimento até o final
      while (motorX.distanceToGo() != 0 || motorY.distanceToGo() != 0 || motorZ.distanceToGo() != 0) {
        checkEmergency();
        if (emergencyStop) { Serial.println("ALARM"); return; }
        motorX.run(); motorY.run(); motorZ.run();
        yield();
      }
      Serial.println("OK");
    }
  }
  else if (cmd.startsWith("G28")) {
    if (cmd.indexOf("X") != -1 || cmd == "G28") homeAxis('X');
    if (!emergencyStop && (cmd.indexOf("Z") != -1 || cmd == "G28")) homeAxis('Z');
    if (!emergencyStop && (cmd.indexOf("Y") != -1 || cmd == "G28")) homeAxis('Y');
    if (!emergencyStop) Serial.println("OK");
  }
  else if (cmd.startsWith("G92")) {
    if (cmd.indexOf("X") != -1) { motorX.setCurrentPosition(extractVal(cmd, 'X') * STEPS_PER_MM); homedX = true; }
    if (cmd.indexOf("Y") != -1) { motorY.setCurrentPosition(extractVal(cmd, 'Y') * STEPS_PER_MM); homedY = true; }
    if (cmd.indexOf("Z") != -1) { motorZ.setCurrentPosition(extractVal(cmd, 'Z') * STEPS_PER_MM); homedZ = true; }
    Serial.println("OK");
  }
  else if (cmd.startsWith("M203")) {
    if (cmd.indexOf("X") != -1) motorX.setMaxSpeed(extractVal(cmd, 'X') * STEPS_PER_MM);
    if (cmd.indexOf("Y") != -1) motorY.setMaxSpeed(extractVal(cmd, 'Y') * STEPS_PER_MM);
    if (cmd.indexOf("Z") != -1) motorZ.setMaxSpeed(extractVal(cmd, 'Z') * STEPS_PER_MM);
    Serial.println("OK");
  }
  else if (cmd.startsWith("M201")) {
    if (cmd.indexOf("X") != -1) motorX.setAcceleration(extractVal(cmd, 'X') * STEPS_PER_MM);
    if (cmd.indexOf("Y") != -1) motorY.setAcceleration(extractVal(cmd, 'Y') * STEPS_PER_MM);
    if (cmd.indexOf("Z") != -1) motorZ.setAcceleration(extractVal(cmd, 'Z') * STEPS_PER_MM);
    Serial.println("OK");
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(X_ENDSTOP, INPUT_PULLUP); pinMode(Z_ENDSTOP, INPUT_PULLUP); pinMode(Y_ENDSTOP, INPUT_PULLUP);
  pinMode(PIN_D22, OUTPUT); digitalWrite(PIN_D22, LOW);

  motorX.setPinsInverted(true, false, false);
  motorY.setPinsInverted(false, false, false);
  motorZ.setPinsInverted(true, false, false);

  motorX.setMaxSpeed(50.0 * STEPS_PER_MM); motorX.setAcceleration(100.0 * STEPS_PER_MM);
  motorY.setMaxSpeed(50.0 * STEPS_PER_MM); motorY.setAcceleration(100.0 * STEPS_PER_MM);
  motorZ.setMaxSpeed(25.0 * STEPS_PER_MM); motorZ.setAcceleration(50.0 * STEPS_PER_MM);
}

void loop() {
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    parseCommand(cmd);
  }
  if (!emergencyStop) { motorX.run(); motorY.run(); motorZ.run(); }
}
