#include <Arduino.h>
#include <SoftwareWire.h>

struct Channel {
  const char* name;
  uint8_t sda;
  uint8_t scl;
};

const Channel CHANNELS[5] = {
  {"INDEX",  2,  3},
  {"PINKY",  4,  5},
  {"MIDDLE", 6,  7},
  {"RING",   8,  9},
  {"THUMB", 10, 16}
};

SoftwareWire wires[5] = {
  SoftwareWire(2, 3),
  SoftwareWire(4, 5),
  SoftwareWire(6, 7),
  SoftwareWire(8, 9),
  SoftwareWire(10, 16)
};

static const uint8_t MLX_ADDR = 0x0C;
bool ready[5] = {false, false, false, false, false};

uint8_t sendCmd(SoftwareWire &w, uint8_t cmd) {
  w.beginTransmission(MLX_ADDR);
  w.write(cmd);
  if (w.endTransmission() != 0) return 0xFF;
  if (w.requestFrom(MLX_ADDR, (uint8_t)1) != 1) return 0xFF;
  return w.read();
}

uint8_t writeReg(SoftwareWire &w, uint8_t reg, uint16_t val) {
  w.beginTransmission(MLX_ADDR);
  w.write(0x60); // CMD_WRITE_REGISTER
  w.write((uint8_t)((val >> 8) & 0xFF));
  w.write((uint8_t)(val & 0xFF));
  w.write((uint8_t)((reg & 0x3F) << 2));
  if (w.endTransmission() != 0) return 0xFF;
  if (w.requestFrom(MLX_ADDR, (uint8_t)1) != 1) return 0xFF;
  return w.read();
}

uint8_t readReg(SoftwareWire &w, uint8_t reg, uint16_t &val) {
  w.beginTransmission(MLX_ADDR);
  w.write(0x50); // CMD_READ_REGISTER
  w.write((uint8_t)((reg & 0x3F) << 2));
  if (w.endTransmission() != 0) return 0xFF;
  if (w.requestFrom(MLX_ADDR, (uint8_t)3) != 3) return 0xFF;
  uint8_t stat = w.read();
  val = ((uint16_t)w.read() << 8) | w.read();
  return stat;
}

void setup() {
  Serial.begin(115200);
  while (!Serial) { delay(10); }
  delay(1000);

  Serial.println(F("\n=============================================="));
  Serial.println(F("   eFlesh Hardware Pin & Sensor Diagnostic    "));
  Serial.println(F("=============================================="));

  for (uint8_t i = 0; i < 5; i++) {
    wires[i].begin();
    delay(10);

    Serial.print(F("Testing ")); Serial.print(CHANNELS[i].name);
    Serial.print(F(" (SDA=")); Serial.print(CHANNELS[i].sda);
    Serial.print(F(", SCL=")); Serial.print(CHANNELS[i].scl);
    Serial.print(F(")... "));

    // Ping test
    wires[i].beginTransmission(MLX_ADDR);
    uint8_t pingErr = wires[i].endTransmission();

    if (pingErr == 0) {
      Serial.println(F("✅ ACK"));

      // 1. Exit mode
      uint8_t s_ex = sendCmd(wires[i], 0x80);
      delay(5);

      // 2. Reset (RT = 0xF0)
      uint8_t s_rt = sendCmd(wires[i], 0xF0);
      delay(20);

      // 3. Exit again after reset
      sendCmd(wires[i], 0x80);
      delay(5);

      // 4. Config Reg 0: HallConf=0x0C, Gain=0x1 -> 0x001C
      uint8_t s_w0 = writeReg(wires[i], 0x00, 0x001C);
      delay(5);

      // 5. Config Reg 2: RES_XYZ=0x2, DIG_FLT=0x4 -> 0x0550
      uint8_t s_w2 = writeReg(wires[i], 0x02, 0x0550);
      delay(5);

      // Verify registers
      uint16_t r0 = 0, r2 = 0;
      readReg(wires[i], 0x00, r0);
      readReg(wires[i], 0x02, r2);
      Serial.print(F("   -> Reg0=0x")); Serial.print(r0, HEX);
      Serial.print(F(", Reg2=0x")); Serial.println(r2, HEX);

      // 6. Start Single or Burst
      // Try Single measurement first:
      uint8_t s_sm = sendCmd(wires[i], 0x3F); // Single measurement T,X,Y,Z
      delay(15);

      // Read measurement
      wires[i].beginTransmission(MLX_ADDR);
      wires[i].write(0x4F);
      wires[i].endTransmission();
      wires[i].requestFrom(MLX_ADDR, (uint8_t)9);
      uint8_t stat = wires[i].read();
      uint16_t tr = ((uint16_t)wires[i].read() << 8) | wires[i].read();
      uint16_t xr = ((uint16_t)wires[i].read() << 8) | wires[i].read();
      uint16_t yr = ((uint16_t)wires[i].read() << 8) | wires[i].read();
      uint16_t zr = ((uint16_t)wires[i].read() << 8) | wires[i].read();

      Serial.print(F("   -> Single Measurement: stat=0x")); Serial.print(stat, HEX);
      Serial.print(F(" Raw X=")); Serial.print(xr);
      Serial.print(F(" Y=")); Serial.print(yr);
      Serial.print(F(" Z=")); Serial.println(zr);

      // Now start Burst Mode:
      uint8_t s_sb = sendCmd(wires[i], 0x1F);
      Serial.print(F("   -> Start Burst Mode: stat=0x")); Serial.println(s_sb, HEX);

      ready[i] = true;
    } else {
      Serial.print(F("❌ OFFLINE (Err ")); Serial.print(pingErr); Serial.println(F(")"));
    }
  }
  Serial.println(F("==============================================\n"));
}

void loop() {
  for (uint8_t i = 0; i < 5; i++) {
    if (!ready[i]) continue;

    wires[i].beginTransmission(MLX_ADDR);
    wires[i].write(0x4F); // Read measurement
    uint8_t err = wires[i].endTransmission();

    if (err == 0) {
      uint8_t n = wires[i].requestFrom(MLX_ADDR, (uint8_t)9);
      if (n == 9) {
        uint8_t stat = wires[i].read();
        uint16_t t_raw = ((uint16_t)wires[i].read() << 8) | wires[i].read();
        uint16_t x_raw = ((uint16_t)wires[i].read() << 8) | wires[i].read();
        uint16_t y_raw = ((uint16_t)wires[i].read() << 8) | wires[i].read();
        uint16_t z_raw = ((uint16_t)wires[i].read() << 8) | wires[i].read();

        float x = ((float)((int32_t)x_raw - 32768)) * 2.40f;
        float y = ((float)((int32_t)y_raw - 32768)) * 2.40f;
        float z = ((float)((int32_t)z_raw - 32768)) * 3.872f;
        float t = 25.0f + ((float)t_raw - 46244.0f) / 45.2f;

        Serial.print(CHANNELS[i].name);
        Serial.print(F(" X:")); Serial.print(x, 1);
        Serial.print(F(" Y:")); Serial.print(y, 1);
        Serial.print(F(" Z:")); Serial.print(z, 1);
        Serial.print(F(" T:")); Serial.print(t, 1);
        Serial.print(F(" [0x")); Serial.print(stat, HEX);
        Serial.println(F("]"));
      }
    }
  }
  delay(100);
}
