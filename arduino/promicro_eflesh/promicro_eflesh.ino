/*
  eFlesh Pro Micro 5-Sensor Multi-Bus Streamer (No-Solder Jumper Edition)
  Supports up to 5 MLX90393 sensors at the factory default address (0x0C)
  using separate SoftwareWire I2C pins — no solder jumpers on SH1/A0 required!

  Wiring:
  - VCC: All VCC pins of all five sensors connected to Pro Micro VCC (3.3V / 5V)
  - GND: All GND pins of all five sensors connected to Pro Micro GND
  - I2C Signal per Finger:
    * 1. INDEX  : SDA = Pin 2,  SCL = Pin 3  (Active)
    * 2. PINKY  : SDA = Pin 4,  SCL = Pin 5  (Active)
    * 3. MIDDLE : SDA = Pin 6,  SCL = Pin 7  (Available)
    * 4. RING   : SDA = Pin 8,  SCL = Pin 9  (Available)
    * 5. THUMB  : SDA = Pin 10, SCL = Pin 16 (Available)
*/

#include <Arduino.h>
#include <SoftwareWire.h>

static const uint8_t MLX_ADDR = 0x0C;
static const uint8_t NUM_SENSORS = 5;

struct SensorConfig {
  const char* name;
  uint8_t sda;
  uint8_t scl;
};

const SensorConfig SENSORS[NUM_SENSORS] = {
  {"INDEX",  2,  3},   // Sensor 0: Index finger
  {"PINKY",  4,  5},   // Sensor 1: Pinky finger
  {"MIDDLE", 6,  7},   // Sensor 2: Middle finger
  {"RING",   8,  9},   // Sensor 3: Ring finger
  {"THUMB", 10, 16}    // Sensor 4: Thumb
};

SoftwareWire wires[NUM_SENSORS] = {
  SoftwareWire(SENSORS[0].sda, SENSORS[0].scl),
  SoftwareWire(SENSORS[1].sda, SENSORS[1].scl),
  SoftwareWire(SENSORS[2].sda, SENSORS[2].scl),
  SoftwareWire(SENSORS[3].sda, SENSORS[3].scl),
  SoftwareWire(SENSORS[4].sda, SENSORS[4].scl)
};

bool sensorReady[NUM_SENSORS] = {false, false, false, false, false};

struct Reading {
  float x;
  float y;
  float z;
  float t;
} readings[NUM_SENSORS];

// Send command and read 1-byte status
uint8_t mlxCommand(SoftwareWire &w, uint8_t cmd) {
  w.beginTransmission(MLX_ADDR);
  w.write(cmd);
  w.endTransmission();
  w.requestFrom((uint8_t)MLX_ADDR, (uint8_t)1);
  return w.read();
}

// Write 16-bit register
uint8_t mlxWriteRegister(SoftwareWire &w, uint8_t regAddr, uint16_t data) {
  w.beginTransmission(MLX_ADDR);
  w.write(0x60); // CMD_WRITE_REGISTER
  w.write((uint8_t)((data >> 8) & 0xFF));
  w.write((uint8_t)(data & 0xFF));
  w.write((uint8_t)((regAddr & 0x3F) << 2));
  w.endTransmission();
  w.requestFrom((uint8_t)MLX_ADDR, (uint8_t)1);
  return w.read();
}

// Read 16-bit register
uint8_t mlxReadRegister(SoftwareWire &w, uint8_t regAddr, uint16_t &data) {
  w.beginTransmission(MLX_ADDR);
  w.write(0x50); // CMD_READ_REGISTER
  w.write((uint8_t)((regAddr & 0x3F) << 2));
  w.endTransmission();
  w.requestFrom((uint8_t)MLX_ADDR, (uint8_t)3);
  uint8_t status = w.read();
  uint8_t msb = w.read();
  uint8_t lsb = w.read();
  data = ((uint16_t)msb << 8) | lsb;
  return status;
}

bool initSensor(SoftwareWire &w, const char* name, bool verbose = true) {
  w.begin();
  delay(5);

  // Ping test
  w.beginTransmission(MLX_ADDR);
  if (w.endTransmission() != 0) {
    if (verbose) {
      Serial.print(F("[OFFLINE] ")); Serial.print(name);
      Serial.println(F(": Not connected (NACK)."));
    }
    return false;
  }

  // Exit mode
  mlxCommand(w, 0x80);
  delay(5);

  // Reset
  mlxCommand(w, 0xF0);
  delay(20);

  // Exit mode again after reset
  mlxCommand(w, 0x80);
  delay(5);

  // Set Register 0: HallConf = 0x0C, Gain = 0x1 -> 0x001C
  mlxWriteRegister(w, 0x00, 0x001C);
  delay(2);

  // Set Register 2: Res_xyz = 2 (0x0540), DigFilter = 4 (0x0010) -> 0x0550
  mlxWriteRegister(w, 0x02, 0x0550);
  delay(2);

  // Start Burst Mode (T, X, Y, Z = 0x0F)
  mlxCommand(w, 0x1F);

  if (verbose) {
    Serial.print(F("[ONLINE]  ")); Serial.print(name);
    Serial.println(F(": Successfully initialized and streaming! ✅"));
  }

  return true;
}

bool readSensorData(SoftwareWire &w, float &x, float &y, float &z, float &t) {
  w.beginTransmission(MLX_ADDR);
  w.write(0x4F); // CMD_READ_MEASUREMENT (T, X, Y, Z)
  if (w.endTransmission() != 0) return false;

  if (w.requestFrom((uint8_t)MLX_ADDR, (uint8_t)9) != 9) return false;

  uint8_t status = w.read();
  if (status == 0xFF) return false; // Bus disconnect or NACK

  uint16_t raw_t = ((uint16_t)w.read() << 8) | w.read();
  uint16_t raw_x = ((uint16_t)w.read() << 8) | w.read();
  uint16_t raw_y = ((uint16_t)w.read() << 8) | w.read();
  uint16_t raw_z = ((uint16_t)w.read() << 8) | w.read();

  // Conversion to uT (microTesla) according to eFlesh / AnySkin config:
  // XY: 0.150 * 4.0 * 4 = 2.40 uT / LSB
  // Z : 0.242 * 4.0 * 4 = 3.872 uT / LSB
  x = ((float)((int32_t)raw_x - 32768)) * 2.40f;
  y = ((float)((int32_t)raw_y - 32768)) * 2.40f;
  z = ((float)((int32_t)raw_z - 32768)) * 3.872f;
  t = 25.0f + ((float)raw_t - 46244.0f) / 45.2f;

  return true;
}

unsigned long lastRetryTime = 0;

void setup() {
  Serial.begin(115200);
  while (!Serial) { delay(10); }
  delay(600);

  Serial.println(F("\n=============================================="));
  Serial.println(F("    eFlesh 5-Fingers Multi-Bus Streamer       "));
  Serial.println(F("=============================================="));

  for (uint8_t i = 0; i < NUM_SENSORS; i++) {
    sensorReady[i] = initSensor(wires[i], SENSORS[i].name, true);
  }

  Serial.println(F("Starting data stream...\n"));
}

void loop() {
  // Retry scan for inactive sensors every 3 seconds (Hot-Plug Support!)
  unsigned long now = millis();
  if (now - lastRetryTime > 3000) {
    lastRetryTime = now;
    for (uint8_t i = 0; i < NUM_SENSORS; i++) {
      if (!sensorReady[i]) {
        sensorReady[i] = initSensor(wires[i], SENSORS[i].name, false);
      }
    }
  }

  // Read all active sensors
  for (uint8_t i = 0; i < NUM_SENSORS; i++) {
    if (sensorReady[i]) {
      float x, y, z, t;
      if (readSensorData(wires[i], x, y, z, t)) {
        readings[i] = {x, y, z, t};
      }
    }
  }

  // Print per-finger data stream
  for (uint8_t i = 0; i < NUM_SENSORS; i++) {
    if (sensorReady[i]) {
      Serial.print(SENSORS[i].name);
      Serial.print(F(" X:")); Serial.print(readings[i].x, 2);
      Serial.print(F(" Y:")); Serial.print(readings[i].y, 2);
      Serial.print(F(" Z:")); Serial.print(readings[i].z, 2);
      Serial.print(F(" T:")); Serial.println(readings[i].t, 1);
    }
  }

  delay(10); // ~100 Hz refresh
}
