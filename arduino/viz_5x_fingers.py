#!/usr/bin/env python3
"""
=============================================================================
  eFlesh Multi-Sensor Tactile Telemetry (Clean White, Large Font, Zero Latency)
=============================================================================
Design highlights:
1. CLEAN WHITE background (#ffffff) - comfortable for scientific instrument displays
2. LARGE & CLEAR font sizes (14pt - 18pt), readable on 4K/Full HD monitors
3. ZERO-LATENCY ENGINE (drains serial buffer instantly, 0 ms queue delay, 60 FPS)
4. Monitors both active PCBs (INDEX & PINKY) simultaneously side-by-side
   with a status bar for all 5 eFlesh finger channels.

Keyboard shortcuts:
- [Space] or [T] : Tare / zero the magnetic field baseline
- [+] or [-]     : Increase / decrease contact sensitivity
- [R]            : Reset waveform history
- [Q]            : Quit
=============================================================================
"""

import sys
import os
import glob
import time
import threading
import collections
import numpy as np
import serial
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.animation import FuncAnimation

# -----------------------------------------------------------------------------
# Typography & Background Configuration (Large Font, Pure White)
# -----------------------------------------------------------------------------
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Liberation Sans', 'Arial', 'sans-serif']
plt.rcParams['font.size'] = 13
plt.rcParams['axes.titlesize'] = 14
plt.rcParams['axes.titleweight'] = 'bold'
plt.rcParams['axes.labelsize'] = 12
plt.rcParams['axes.labelweight'] = 'bold'
plt.rcParams['xtick.labelsize'] = 11
plt.rcParams['ytick.labelsize'] = 11
plt.rcParams['axes.linewidth'] = 1.2
plt.rcParams['figure.facecolor'] = '#ffffff'
plt.rcParams['axes.facecolor'] = '#ffffff'

# Physical skin outline of eFlesh fingertip (in mm)
SKIN_OUTLINE_MM = np.array([
    [-21.34, -7.97], [-21.33, -8.07], [-21.30, -8.24], [-21.26, -8.38],
    [-21.21, -8.47], [-20.64, -9.00], [-20.57, -9.00], [5.08, -9.00],
    [5.15, -9.00], [6.97, -8.49], [7.01, -8.38], [7.02, -8.24],
    [7.04, -7.69], [7.04, 8.31], [7.03, 8.45], [6.99, 8.57],
    [5.08, 9.00], [-3.74, 9.00], [-19.74, 8.99], [-20.57, 8.99],
    [-20.71, 8.97], [-20.79, 8.94], [-20.92, 8.80], [-21.10, 8.57],
    [-21.21, 8.42], [-21.33, 8.24], [-21.33, 8.17], [-21.34, 8.03],
    [-21.34, -7.97]
])
SKIN_OUTLINE_VERTICAL = np.column_stack([SKIN_OUTLINE_MM[:, 1], SKIN_OUTLINE_MM[:, 0]])

CHANNELS = ["INDEX", "PINKY", "MIDDLE", "RING", "THUMB"]
PINS = {
    "INDEX":  "Pin 2, 3",
    "PINKY":  "Pin 4, 5",
    "MIDDLE": "Pin 6, 7",
    "RING":   "Pin 8, 9",
    "THUMB":  "Pin 10, 16"
}

class FastZeroLatencyReader(threading.Thread):
    """
    Instant serial reader: always reads the latest data (reverse scan)
    and immediately discards old queued data so delay = 0 ms.
    """
    def __init__(self, port='/dev/ttyACM0', baud=115200):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.running = True
        self.connected = False
        self.lock = threading.Lock()

        self.latest_data = {ch: np.array([0.0, 0.0, 0.0, 0.0]) for ch in CHANNELS}
        self.packet_count = {ch: 0 for ch in CHANNELS}
        self.hz_rate = {ch: 0.0 for ch in CHANNELS}
        self._hz_timer = time.time()
        self._hz_counter = {ch: 0 for ch in CHANNELS}

    def run(self):
        while self.running:
            active_port = self.port
            if not os.path.exists(active_port):
                acm = sorted(glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*'))
                if acm:
                    active_port = acm[0]
                    self.port = active_port

            if not os.path.exists(active_port):
                self.connected = False
                time.sleep(0.3)
                continue

            try:
                ser = serial.Serial(active_port, self.baud, timeout=0.02)
                self.connected = True
                print(f"[Serial] Connected to {active_port} (115200 bps)...")
                ser.reset_input_buffer()
                buf = b""

                while self.running:
                    waiting = ser.in_waiting
                    if waiting > 0:
                        chunk = ser.read(waiting)
                        buf += chunk
                        if b'\n' in buf:
                            lines = buf.split(b'\n')
                            buf = lines[-1]

                            # SCAN FROM BACK (NEWEST) -> DROP ALL OLD DATA (0 ms lag!)
                            updated = set()
                            now = time.time()
                            for line_b in reversed(lines[:-1]):
                                text = line_b.decode('utf-8', errors='ignore').strip()
                                if not text:
                                    continue
                                for ch in CHANNELS:
                                    if ch not in updated and text.startswith(ch):
                                        try:
                                            parts = text.split()
                                            x, y, z, t = 0.0, 0.0, 0.0, 0.0
                                            for p in parts:
                                                if p.startswith("X:"): x = float(p.split(":")[1])
                                                elif p.startswith("Y:"): y = float(p.split(":")[1])
                                                elif p.startswith("Z:"): z = float(p.split(":")[1])
                                                elif p.startswith("T:"): t = float(p.split(":")[1])

                                            with self.lock:
                                                self.latest_data[ch] = np.array([x, y, z, t])
                                                self.packet_count[ch] += 1
                                                self._hz_counter[ch] += 1
                                            updated.add(ch)
                                        except Exception:
                                            pass

                                # If both active sensors already have latest samples, exit immediately!
                                if len(updated) >= 2:
                                    break

                            # Calculate refresh rate Hz
                            dt = now - self._hz_timer
                            if dt >= 1.0:
                                with self.lock:
                                    for ch in CHANNELS:
                                        self.hz_rate[ch] = self._hz_counter[ch] / dt
                                        self._hz_counter[ch] = 0
                                    self._hz_timer = now
                    else:
                        time.sleep(0.001)
                ser.close()
            except Exception:
                self.connected = False
                time.sleep(0.4)

    def get_snapshot(self):
        with self.lock:
            snap = {}
            for ch in CHANNELS:
                snap[ch] = {
                    "raw": self.latest_data[ch].copy(),
                    "hz": self.hz_rate[ch],
                    "pkts": self.packet_count[ch],
                    "active": (self.hz_rate[ch] > 2.0 or self.packet_count[ch] > 10)
                }
            return snap

def main():
    port = None
    if len(sys.argv) > 1 and sys.argv[1].startswith('/dev/'):
        port = sys.argv[1]
    else:
        arduino_by_id = glob.glob('/dev/serial/by-id/*Arduino*') + glob.glob('/dev/serial/by-id/*Micro*')
        if arduino_by_id:
            port = os.path.realpath(arduino_by_id[0])
        else:
            acm = sorted(glob.glob('/dev/ttyACM*'))
            port = acm[-1] if len(acm) > 1 else (acm[0] if acm else '/dev/ttyACM1')

    print("=" * 72)
    print("  eFlesh High-Speed Telemetry Monitor (Clean White, Large Font)")
    print("=" * 72)
    print(f"Opening serial port: {port} (115200 bps)...")

    reader = FastZeroLatencyReader(port=port)
    reader.start()

    time.sleep(1.2)
    init_snap = reader.get_snapshot()

    print("\nREAL-TIME SENSOR STATUS:")
    for ch in CHANNELS:
        s = init_snap[ch]
        st = f"ACTIVE ({s['hz']:.0f} Hz, {s['pkts']} packets)" if s['active'] else "Standby (Offline)"
        print(f" - {ch:<7} ({PINS[ch]:<10}) : {st}")
    print("=" * 72)

    # Baseline tare
    baselines = {ch: np.array([0.0, 0.0, 0.0]) for ch in CHANNELS}
    for ch in CHANNELS:
        if init_snap[ch]["active"]:
            baselines[ch] = init_snap[ch]["raw"][:3].copy()

    sensitivity = 0.15  # mm / uT
    deadband = 8.0      # uT

    hist_len = 100
    time_pts = np.arange(hist_len)
    waves = {
        "INDEX": {"x": collections.deque([0.0]*hist_len, maxlen=hist_len),
                  "y": collections.deque([0.0]*hist_len, maxlen=hist_len),
                  "z": collections.deque([0.0]*hist_len, maxlen=hist_len)},
        "PINKY": {"x": collections.deque([0.0]*hist_len, maxlen=hist_len),
                  "y": collections.deque([0.0]*hist_len, maxlen=hist_len),
                  "z": collections.deque([0.0]*hist_len, maxlen=hist_len)}
    }

    # =========================================================================
    # DASHBOARD LAYOUT: CLEAN WHITE & LARGE FONT FOR READABILITY
    # =========================================================================
    fig = plt.figure(figsize=(15.5, 8.8), facecolor='#ffffff')
    fig.canvas.manager.set_window_title(f"eFlesh Real-Time Telemetry Monitor [{port}]")

    # Layout: Row 1 (2D Contact Map: Index & Pinky), Row 2 (Signal Oscilloscopes)
    gs = fig.add_gridspec(2, 2, height_ratios=[1.3, 1.0], hspace=0.32, wspace=0.22,
                           top=0.88, bottom=0.08, left=0.08, right=0.95)

    # Large Header Bar at Top
    fig.text(0.08, 0.955, "eFlesh Tactile Sensor Monitor (Real-Time 60 Hz)",
             fontsize=17, fontweight='bold', color='#0f172a')
    header_status = fig.text(0.08, 0.925, f"Port: {port}  |  Baud: 115200  |  Sensitivity: {sensitivity:.2f} mm/uT",
                             fontsize=12, color='#475569')

    # Channel Status Badges (displays all five sensors at the top)
    ch_bar = fig.text(
        0.95, 0.940,
        "[INDEX: ACTIVE]   [PINKY: ACTIVE]   [MID: Standby]   [RING: Standby]   [THM: Standby]\n"
        "Shortcuts: [Space / T] Tare Zero    |    [+ / -] Sensitivity    |    [Q] Quit",
        fontsize=10.5, color='#1e293b', ha='right', va='center',
        bbox=dict(boxstyle="square,pad=0.4", facecolor='#f1f5f9', edgecolor='#94a3b8', lw=1.0)
    )

    # -------------------------------------------------------------------------
    # LEFT PANEL: PCB 1 (INDEX FINGER - Pin 2, 3)
    # -------------------------------------------------------------------------
    ax_map1 = fig.add_subplot(gs[0, 0], facecolor='#ffffff')
    ax_map1.set_title("PCB 1: INDEX FINGER - Pin 2, 3", fontsize=14, fontweight='bold', color='#1e3a8a', pad=8)
    ax_map1.set_xlim(-14, 14)
    ax_map1.set_ylim(-26, 11)
    ax_map1.set_aspect('equal')
    ax_map1.grid(True, linestyle='--', color='#e2e8f0', linewidth=1.0)
    ax_map1.set_xlabel("Skin Width, Y [mm]", fontsize=12, fontweight='bold')
    ax_map1.set_ylabel("Skin Length, X [mm]", fontsize=12, fontweight='bold')

    # Skin Boundary
    poly1 = patches.Polygon(SKIN_OUTLINE_VERTICAL, closed=True, facecolor='#f8fafc', edgecolor='#0f172a', linewidth=2.0, zorder=2)
    ax_map1.add_patch(poly1)
    pouch1 = patches.Circle((0, 0), radius=3.2, facecolor='none', edgecolor='#64748b', linewidth=1.5, linestyle='--', zorder=3)
    ax_map1.add_patch(pouch1)
    ax_map1.plot(0, 0, marker='+', markersize=10, color='#0f172a', mew=1.5, zorder=4)
    ax_map1.text(0, -5.2, "Magnet Center (0,0)", fontsize=9.5, ha='center', color='#64748b')

    # Contact Point & Shear Arrow
    pt1, = ax_map1.plot([0], [0], 'o', markersize=12, color='#dc2626', markeredgecolor='#000000', markeredgewidth=1.5, zorder=10, alpha=0.0)
    arrow1 = ax_map1.annotate('', xy=(0, 0), xytext=(0, 0),
                              arrowprops=dict(arrowstyle="->", color='#0f172a', lw=2.5), zorder=9)

    # Large Numeric Readout
    readout1 = ax_map1.text(
        0, 8.2, "ΔBx: +0.0 uT  |  ΔBy: +0.0 uT  |  ΔBz: +0.0 uT  |  26.0 °C",
        fontsize=12, fontweight='bold', color='#0f172a', ha='center', family='monospace',
        bbox=dict(boxstyle="square,pad=0.35", facecolor='#f8fafc', edgecolor='#cbd5e1', lw=1.2)
    )

    # -------------------------------------------------------------------------
    # RIGHT PANEL: PCB 2 (PINKY FINGER - Pin 4, 5)
    # -------------------------------------------------------------------------
    ax_map2 = fig.add_subplot(gs[0, 1], facecolor='#ffffff')
    ax_map2.set_title("PCB 2: PINKY FINGER - Pin 4, 5", fontsize=14, fontweight='bold', color='#581c87', pad=8)
    ax_map2.set_xlim(-14, 14)
    ax_map2.set_ylim(-26, 11)
    ax_map2.set_aspect('equal')
    ax_map2.grid(True, linestyle='--', color='#e2e8f0', linewidth=1.0)
    ax_map2.set_xlabel("Skin Width, Y [mm]", fontsize=12, fontweight='bold')
    ax_map2.set_ylabel("Skin Length, X [mm]", fontsize=12, fontweight='bold')

    poly2 = patches.Polygon(SKIN_OUTLINE_VERTICAL, closed=True, facecolor='#f8fafc', edgecolor='#0f172a', linewidth=2.0, zorder=2)
    ax_map2.add_patch(poly2)
    pouch2 = patches.Circle((0, 0), radius=3.2, facecolor='none', edgecolor='#64748b', linewidth=1.5, linestyle='--', zorder=3)
    ax_map2.add_patch(pouch2)
    ax_map2.plot(0, 0, marker='+', markersize=10, color='#0f172a', mew=1.5, zorder=4)
    ax_map2.text(0, -5.2, "Magnet Center (0,0)", fontsize=9.5, ha='center', color='#64748b')

    pt2, = ax_map2.plot([0], [0], 'o', markersize=12, color='#7c3aed', markeredgecolor='#000000', markeredgewidth=1.5, zorder=10, alpha=0.0)
    arrow2 = ax_map2.annotate('', xy=(0, 0), xytext=(0, 0),
                              arrowprops=dict(arrowstyle="->", color='#0f172a', lw=2.5), zorder=9)

    readout2 = ax_map2.text(
        0, 8.2, "ΔBx: +0.0 uT  |  ΔBy: +0.0 uT  |  ΔBz: +0.0 uT  |  26.0 °C",
        fontsize=12, fontweight='bold', color='#0f172a', ha='center', family='monospace',
        bbox=dict(boxstyle="square,pad=0.35", facecolor='#f8fafc', edgecolor='#cbd5e1', lw=1.2)
    )

    # -------------------------------------------------------------------------
    # BOTTOM ROW: 2 LARGE REAL-TIME WAVEFORM OSCILLOSCOPES
    # -------------------------------------------------------------------------
    # Oscilloscope 1 (INDEX)
    ax_w1 = fig.add_subplot(gs[1, 0], facecolor='#ffffff')
    ax_w1.set_title("Deflection Waveform: X, Y, Z Axes (INDEX)", fontsize=13, fontweight='bold', color='#1e3a8a')
    ax_w1.set_ylabel("Deflection [uT]", fontsize=12, fontweight='bold')
    ax_w1.set_xlabel("Time Samples (60 Hz)", fontsize=11)
    ax_w1.set_xlim(0, hist_len)
    ax_w1.set_ylim(-100, 100)
    ax_w1.grid(True, linestyle='--', color='#e2e8f0', linewidth=1.0)
    ax_w1.axhline(0, color='#64748b', linestyle='-', linewidth=1.0)

    line1_x, = ax_w1.plot([], [], label="ΔBx (Red)", color='#dc2626', lw=2.5)
    line1_y, = ax_w1.plot([], [], label="ΔBy (Green)", color='#16a34a', lw=2.5, linestyle='--')
    line1_z, = ax_w1.plot([], [], label="ΔBz (Blue)",  color='#2563eb', lw=2.5, linestyle='-.')
    ax_w1.legend(loc='upper right', fontsize=10.5, framealpha=0.9, facecolor='#ffffff', edgecolor='#cbd5e1')

    # Oscilloscope 2 (PINKY)
    ax_w2 = fig.add_subplot(gs[1, 1], facecolor='#ffffff')
    ax_w2.set_title("Deflection Waveform: X, Y, Z Axes (PINKY)", fontsize=13, fontweight='bold', color='#581c87')
    ax_w2.set_ylabel("Deflection [uT]", fontsize=12, fontweight='bold')
    ax_w2.set_xlabel("Time Samples (60 Hz)", fontsize=11)
    ax_w2.set_xlim(0, hist_len)
    ax_w2.set_ylim(-100, 100)
    ax_w2.grid(True, linestyle='--', color='#e2e8f0', linewidth=1.0)
    ax_w2.axhline(0, color='#64748b', linestyle='-', linewidth=1.0)

    line2_x, = ax_w2.plot([], [], label="ΔBx (Red)", color='#dc2626', lw=2.5)
    line2_y, = ax_w2.plot([], [], label="ΔBy (Green)", color='#16a34a', lw=2.5, linestyle='--')
    line2_z, = ax_w2.plot([], [], label="ΔBz (Blue)",  color='#2563eb', lw=2.5, linestyle='-.')
    ax_w2.legend(loc='upper right', fontsize=10.5, framealpha=0.9, facecolor='#ffffff', edgecolor='#cbd5e1')

    # Keyboard Handlers
    def on_key(event):
        nonlocal sensitivity
        if event.key in ['t', ' ']:
            snap = reader.get_snapshot()
            for ch in CHANNELS:
                if snap[ch]["active"]:
                    baselines[ch] = snap[ch]["raw"][:3].copy()
            print("[TARE] Baseline zeroed for all active sensors.")
        elif event.key in ['+', '=']:
            sensitivity = min(0.60, sensitivity + 0.03)
            header_status.set_text(f"Port: {port}  |  Baud: 115200  |  Sensitivity: {sensitivity:.2f} mm/uT")
            print(f"[GAIN] Sensitivity increased: {sensitivity:.2f} mm/uT")
        elif event.key in ['-', '_']:
            sensitivity = max(0.03, sensitivity - 0.03)
            header_status.set_text(f"Port: {port}  |  Baud: 115200  |  Sensitivity: {sensitivity:.2f} mm/uT")
            print(f"[GAIN] Sensitivity decreased: {sensitivity:.2f} mm/uT")
        elif event.key == 'r':
            for ch in ["INDEX", "PINKY"]:
                for ax_k in ["x", "y", "z"]:
                    waves[ch][ax_k].clear()
                    waves[ch][ax_k].extend([0.0]*hist_len)
            print("[RESET] Waveform history cleared.")
        elif event.key in ['q', 'escape']:
            plt.close(fig)

    fig.canvas.mpl_connect('key_press_event', on_key)

    # Frame Update Loop (Blitted & Fixed Limits -> 60+ FPS Instant!)
    def update(frame):
        snap = reader.get_snapshot()

        # Update INDEX
        s1 = snap["INDEX"]
        raw1 = s1["raw"]
        if np.all(baselines["INDEX"] == 0.0) and np.any(raw1[:3] != 0.0) and abs(raw1[0]) < 70000:
            baselines["INDEX"] = raw1[:3].copy()
            print(f"[AUTO-TARE] INDEX baseline set to: X={raw1[0]:.1f}, Y={raw1[1]:.1f}, Z={raw1[2]:.1f}")
        db1 = raw1[:3] - baselines["INDEX"]
        waves["INDEX"]["x"].append(db1[0])
        waves["INDEX"]["y"].append(db1[1])
        waves["INDEX"]["z"].append(db1[2])

        line1_x.set_data(time_pts, list(waves["INDEX"]["x"]))
        line1_y.set_data(time_pts, list(waves["INDEX"]["y"]))
        line1_z.set_data(time_pts, list(waves["INDEX"]["z"]))

        readout1.set_text(f"ΔX:{db1[0]:+5.1f} | ΔY:{db1[1]:+5.1f} | ΔZ:{db1[2]:+5.1f} uT | {raw1[3]:4.1f}°C")

        norm1 = np.sqrt(db1[0]**2 + db1[1]**2 + db1[2]**2)
        if norm1 > deadband:
            cx1 = np.clip(db1[1] * sensitivity * -1.0, -8.0, 8.0)
            cy1 = np.clip(db1[0] * sensitivity * -1.0, -18.0, 5.0)
            pt1.set_data([cx1], [cy1])
            pt1.set_alpha(1.0)

            dx1 = np.clip(db1[1] * 0.10, -6.0, 6.0)
            dy1 = np.clip(db1[0] * 0.10, -8.0, 8.0)
            arrow1.xy = (cx1 + dx1, cy1 + dy1)
            arrow1.set_position((cx1, cy1))
            arrow1.set_visible(True)
        else:
            pt1.set_alpha(0.0)
            arrow1.set_visible(False)

        # Update PINKY
        s2 = snap["PINKY"]
        raw2 = s2["raw"]
        if np.all(baselines["PINKY"] == 0.0) and np.any(raw2[:3] != 0.0) and abs(raw2[0]) < 70000:
            baselines["PINKY"] = raw2[:3].copy()
            print(f"[AUTO-TARE] PINKY baseline set to: X={raw2[0]:.1f}, Y={raw2[1]:.1f}, Z={raw2[2]:.1f}")
        db2 = raw2[:3] - baselines["PINKY"]
        waves["PINKY"]["x"].append(db2[0])
        waves["PINKY"]["y"].append(db2[1])
        waves["PINKY"]["z"].append(db2[2])

        line2_x.set_data(time_pts, list(waves["PINKY"]["x"]))
        line2_y.set_data(time_pts, list(waves["PINKY"]["y"]))
        line2_z.set_data(time_pts, list(waves["PINKY"]["z"]))

        readout2.set_text(f"ΔX:{db2[0]:+5.1f} | ΔY:{db2[1]:+5.1f} | ΔZ:{db2[2]:+5.1f} uT | {raw2[3]:4.1f}°C")

        norm2 = np.sqrt(db2[0]**2 + db2[1]**2 + db2[2]**2)
        if norm2 > deadband:
            cx2 = np.clip(db2[1] * sensitivity * -1.0, -8.0, 8.0)
            cy2 = np.clip(db2[0] * sensitivity * -1.0, -18.0, 5.0)
            pt2.set_data([cx2], [cy2])
            pt2.set_alpha(1.0)

            dx2 = np.clip(db2[1] * 0.10, -6.0, 6.0)
            dy2 = np.clip(db2[0] * 0.10, -8.0, 8.0)
            arrow2.xy = (cx2 + dx2, cy2 + dy2)
            arrow2.set_position((cx2, cy2))
            arrow2.set_visible(True)
        else:
            pt2.set_alpha(0.0)
            arrow2.set_visible(False)

        return [line1_x, line1_y, line1_z, pt1, arrow1, readout1,
                line2_x, line2_y, line2_z, pt2, arrow2, readout2]

    # Blit=True with 12ms interval delivers clean 60-80 FPS real-time rendering!
    anim = FuncAnimation(fig, update, interval=12, blit=True, cache_frame_data=False)
    plt.show()
    reader.running = False

if __name__ == '__main__':
    main()
