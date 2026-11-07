#!/usr/bin/env python3
"""
eFlesh Tactile Calibration Wizard
Calibrates the mapping between magnetic signals (dBx, dBy, dBz) and
physical touch coordinates (X, Y in mm) based on index-touch-skin geometry.
"""

import sys
import time
import json
import os
import numpy as np
import serial

def read_samples(ser, duration_sec=2.5, prompt=""):
    print(f"\n👉 {prompt}")
    input("   [Press ENTER when finger is ready to press...] ")
    print("   Recording data...", end='', flush=True)

    samples = []
    start = time.time()
    while time.time() - start < duration_sec:
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if "X:" in line and "Y:" in line and "Z:" in line:
            try:
                parts = line.split()
                x_val, y_val, z_val = 0.0, 0.0, 0.0
                for p in parts:
                    if p.startswith("X:"): x_val = float(p.split(":")[1])
                    elif p.startswith("Y:"): y_val = float(p.split(":")[1])
                    elif p.startswith("Z:"): z_val = float(p.split(":")[1])
                samples.append([x_val, y_val, z_val])
            except Exception:
                pass
        time.sleep(0.01)
    
    if len(samples) < 5:
        print(" ❌ Too few samples - make sure the serial connection is active!")
        return None
    
    avg = np.mean(samples, axis=0)
    print(f" Done! ({len(samples)} samples)")
    return avg

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else '/dev/ttyACM1'
    print("=" * 65)
    print("  eFlesh Tactile Calibration Wizard - index-touch-skin")
    print("=" * 65)
    print(f"Connecting to {port}...")

    try:
        ser = serial.Serial(port, 115200, timeout=1.0)
        time.sleep(1.0)
    except Exception as e:
        print(f"❌ Failed to open {port}: {e}")
        sys.exit(1)

    print("✅ Serial port connected!")
    print("\nThis calibration procedure will map the direction & scale of your physical touch")
    print("to the actual STL coordinate space (28.4 x 18.0 mm).")

    # 1. Baseline
    input("\n[STEP 1/5] DO NOT TOUCH the sensor (rest/neutral position). Press ENTER to record baseline...")
    print("Recording baseline...", end='', flush=True)
    baseline_samples = []
    for _ in range(50):
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if "X:" in line and "Y:" in line and "Z:" in line:
            parts = line.split()
            vals = [float(p.split(":")[1]) for p in parts if p.startswith(("X:", "Y:", "Z:"))]
            if len(vals) == 3:
                baseline_samples.append(vals)
        time.sleep(0.02)
    
    baseline = np.mean(baseline_samples, axis=0)
    print(f" Done! Baseline B0 = [X:{baseline[0]:.1f}, Y:{baseline[1]:.1f}, Z:{baseline[2]:.1f}]")

    # 2. Touch Points
    # Physical target coordinates in mm (relative to magnet center 0, 0):
    # Tip (front):  X = +6.0 mm, Y = 0.0 mm
    # Base (rear):  X = -18.0 mm, Y = 0.0 mm
    # Right:        X = 0.0 mm, Y = +7.5 mm
    # Left:         X = 0.0 mm, Y = -7.5 mm

    targets_mm = []
    measured_deltas = []

    # Point A: Tip (Front)
    avg_a = read_samples(ser, duration_sec=3.0, prompt="[STEP 2/5] PRESS & HOLD the FRONT TIP of the sensor (direction +X, approx +6 mm)")
    if avg_a is not None:
        delta_a = avg_a - baseline
        targets_mm.append([6.0, 0.0])
        measured_deltas.append([delta_a[0], delta_a[1]])
        print(f"   ΔB = [ΔBx: {delta_a[0]:+.1f}, ΔBy: {delta_a[1]:+.1f}, ΔBz: {delta_a[2]:+.1f}]")

    # Point B: Base (Rear)
    avg_b = read_samples(ser, duration_sec=3.0, prompt="[STEP 3/5] PRESS & HOLD the REAR BASE of the sensor (direction -X, approx -18 mm)")
    if avg_b is not None:
        delta_b = avg_b - baseline
        targets_mm.append([-18.0, 0.0])
        measured_deltas.append([delta_b[0], delta_b[1]])
        print(f"   ΔB = [ΔBx: {delta_b[0]:+.1f}, ΔBy: {delta_b[1]:+.1f}, ΔBz: {delta_b[2]:+.1f}]")

    # Point C: Right Side
    avg_c = read_samples(ser, duration_sec=3.0, prompt="[STEP 4/5] PRESS & HOLD the RIGHT SIDE of the sensor (direction +Y, approx +7.5 mm)")
    if avg_c is not None:
        delta_c = avg_c - baseline
        targets_mm.append([0.0, 7.5])
        measured_deltas.append([delta_c[0], delta_c[1]])
        print(f"   ΔB = [ΔBx: {delta_c[0]:+.1f}, ΔBy: {delta_c[1]:+.1f}, ΔBz: {delta_c[2]:+.1f}]")

    # Point D: Left Side
    avg_d = read_samples(ser, duration_sec=3.0, prompt="[STEP 5/5] PRESS & HOLD the LEFT SIDE of the sensor (direction -Y, approx -7.5 mm)")
    if avg_d is not None:
        delta_d = avg_d - baseline
        targets_mm.append([0.0, -7.5])
        measured_deltas.append([delta_d[0], delta_d[1]])
        print(f"   ΔB = [ΔBx: {delta_d[0]:+.1f}, ΔBy: {delta_d[1]:+.1f}, ΔBz: {delta_d[2]:+.1f}]")

    ser.close()

    if len(measured_deltas) < 4:
        print("\n❌ Calibration failed: some touch points were not recorded successfully.")
        sys.exit(1)

    # Compute 2x2 Transformation Matrix via Least Squares:
    # Target_mm = [dBx, dBy] * W
    X_mat = np.array(measured_deltas)  # shape (4, 2)
    Y_mat = np.array(targets_mm)        # shape (4, 2)

    # Add bias column: [dBx, dBy, 1]
    A = np.hstack([X_mat, np.ones((len(X_mat), 1))])
    # Solve A * M = Y_mat -> M shape (3, 2)
    M, residuals, rank, s = np.linalg.lstsq(A, Y_mat, rcond=None)

    W = M[:2, :].tolist()   # 2x2 matrix
    bias = M[2, :].tolist() # 2 bias terms

    config = {
        'baseline': baseline.tolist(),
        'transform_matrix': W,
        'bias': bias,
        'threshold': 100.0,
        'calibrated_at': time.strftime('%Y-%m-%d %H:%M:%S')
    }

    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calibration_config.json')
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=4)

    print("\n" + "=" * 65)
    print("🎉 CALIBRATION SAVED SUCCESSFULLY!")
    print(f"File saved to: {config_path}")
    print(f"Matrix W: {W}")
    print(f"Bias    : {bias}")
    print("=" * 65)
    print("\nNow run the visualizer again:")
    print(f"python3 {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'viz_touch_skin.py')} {port}")

if __name__ == '__main__':
    main()
