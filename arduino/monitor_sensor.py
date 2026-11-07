#!/usr/bin/env python3
"""
Live Serial Monitor for eFlesh Pro Micro Sensor
Reads X, Y, Z magnetic data from Arduino Pro Micro via USB Serial.
"""
import sys
import os
import glob
import time
import serial

def find_serial_port():
    by_id = glob.glob('/dev/serial/by-id/*Arduino*') + glob.glob('/dev/serial/by-id/*Micro*')
    if by_id:
        return os.path.realpath(by_id[0])
    ports = sorted(glob.glob('/dev/ttyACM*')) + sorted(glob.glob('/dev/ttyUSB*'))
    if not ports:
        return None
    # If multiple ACM ports exist, prefer ACM1 if ACM0 is CH340
    for p in ports:
        if 'ACM1' in p:
            return p
    return ports[0]

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else find_serial_port()
    if not port:
        print("❌ Error: No serial port (/dev/ttyACM* or /dev/ttyUSB*) detected.")
        print("   Make sure the Arduino Pro Micro is plugged into a USB port.")
        sys.exit(1)

    baud = 115200
    print(f"📡 Connecting to {port} at {baud} baud...")

    try:
        ser = serial.Serial(port, baud, timeout=1.0)
        time.sleep(1.0)  # Wait for Pro Micro serial reset to complete
    except serial.SerialException as e:
        print(f"❌ Failed to open {port}: {e}")
        print("   Tip: try running 'sudo chmod a+rw " + port + "' if you encounter a Permission denied error.")
        sys.exit(1)

    print("✅ Connected! Reading magnetic field data (Press Ctrl+C to exit):\n")
    print("-" * 65)

    try:
        while True:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line:
                print(f"\r  {line:<60}", end='', flush=True)
                if not line.startswith("S0") and not line.startswith("S1"):
                    print(f"\n  [INFO] {line}")
    except KeyboardInterrupt:
        print("\n\n⏹️ Monitoring stopped by user.")
    finally:
        ser.close()

if __name__ == '__main__':
    main()
