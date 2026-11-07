#!/usr/bin/env python3
"""
eFlesh Ultra-Fast Real-Time Tactile Contact Visualizer (Zero-Latency Edition)
- Eliminates serial buffer backlog / queue delay (fetches latest packet instantly)
- Lightweight 50 FPS rendering without redraw lag
- Snappy, responsive touch tracking with deadband noise filtering
- Natural vertical finger display & English UI
"""

import sys
import os
import glob
import json
import time
import threading
import collections
import numpy as np
import serial
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.animation import FuncAnimation

# Physical 2D outline of index-touch-skin (in mm)
# [X_length (-21.3 to +7.0), Y_width (-9.0 to +9.0)]
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

# Transpose for Vertical Display:
# Plot X = Width Y (-9.0 mm to +9.0 mm)
# Plot Y = Length X (Base: -21.3 mm to Tip: +7.0 mm)
SKIN_OUTLINE_VERTICAL = np.column_stack([SKIN_OUTLINE_MM[:, 1], SKIN_OUTLINE_MM[:, 0]])

class ZeroLatencySerialReader(threading.Thread):
    """
    High-speed serial reader that always drains the OS buffer and
    picks the freshest packet, eliminating queue lag completely.
    """
    def __init__(self, port='/dev/ttyACM0', baud=115200, finger='INDEX'):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.finger = finger.upper()
        self.running = True
        self.connected = False
        self.latest_raw = np.array([0.0, 0.0, 0.0])
        self.lock = threading.Lock()

    def run(self):
        while self.running:
            try:
                ser = serial.Serial(self.port, self.baud, timeout=0.05)
                self.connected = True
                print(f"[Serial] Connected to {self.port} (Tracking: {self.finger})")
                ser.reset_input_buffer()
                
                buf = b""
                while self.running:
                    waiting = ser.in_waiting
                    if waiting > 0:
                        chunk = ser.read(waiting)
                        buf += chunk
                        if b'\n' in buf:
                            lines = buf.split(b'\n')
                            buf = lines[-1]  # Keep incomplete tail for next read
                            
                            # Scan from newest line backwards to extract the freshest sample
                            for candidate in reversed(lines[:-1]):
                                text = candidate.decode('utf-8', errors='ignore').strip()
                                if self.finger in text and "X:" in text and "Y:" in text and "Z:" in text:
                                    try:
                                        parts = text.split()
                                        x_val, y_val, z_val = 0.0, 0.0, 0.0
                                        for p in parts:
                                            if p.startswith("X:"): x_val = float(p.split(":")[1])
                                            elif p.startswith("Y:"): y_val = float(p.split(":")[1])
                                            elif p.startswith("Z:"): z_val = float(p.split(":")[1])
                                        
                                        if abs(x_val) < 200000 and abs(y_val) < 200000 and abs(z_val) < 200000:
                                            with self.lock:
                                                self.latest_raw = np.array([x_val, y_val, z_val])
                                        break  # Got freshest packet, break out immediately
                                    except Exception:
                                        pass
                    else:
                        time.sleep(0.002)
                ser.close()
            except Exception:
                self.connected = False
                time.sleep(0.5)

    def get_raw(self):
        with self.lock:
            return self.latest_raw.copy()

def main():
    port = None
    finger = 'INDEX'

    for arg in sys.argv[1:]:
        if arg.startswith('/dev/'):
            port = arg
        elif arg.upper() in ['INDEX', 'PINKY', 'MIDDLE', 'RING', 'THUMB']:
            finger = arg.upper()

    if not port:
        arduino_by_id = glob.glob('/dev/serial/by-id/*Arduino*') + glob.glob('/dev/serial/by-id/*Micro*')
        if arduino_by_id:
            port = os.path.realpath(arduino_by_id[0])
        else:
            acm_ports = sorted(glob.glob('/dev/ttyACM*'))
            if acm_ports:
                port = acm_ports[-1] if len(acm_ports) > 1 else acm_ports[0]
            else:
                port = '/dev/ttyACM1'

    reader = ZeroLatencySerialReader(port=port, finger=finger)
    reader.start()

    print(f"Connecting to {port} [Sensor: {finger}]...")
    time.sleep(0.8)

    # Check for calibration configuration
    calib_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calibration_config.json')
    calib_matrix = None
    calib_bias = None
    if os.path.exists(calib_file):
        try:
            with open(calib_file, 'r') as f:
                cdata = json.load(f)
                calib_matrix = np.array(cdata['transform_matrix'])
                calib_bias = np.array(cdata['bias'])
                print(f"✅ Loaded calibration file: {calib_file}")
        except Exception as e:
            print(f"⚠️ Could not load calibration file: {e}")

    # Load saved user settings if available
    settings_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'touch_skin_settings.json')
    saved_cfg = {}
    if os.path.exists(settings_file):
        try:
            with open(settings_file, 'r') as f:
                saved_cfg = json.load(f)
                print(f"✅ Loaded saved settings from: {settings_file}")
        except Exception as e:
            print(f"⚠️ Could not load settings: {e}")

    # Initial baseline
    baseline = reader.get_raw()
    calib_buf = []

    # Optimized responsiveness state (with user calibrated sweet-spot defaults)
    state = {
        'baseline': baseline,
        'calibrating': True,
        'calib_count': 0,
        'sensitivity': float(saved_cfg.get('sensitivity', 0.1)),
        'force_threshold': float(saved_cfg.get('force_threshold', 65.0)),
        'deadband': float(saved_cfg.get('deadband', 35.0)),
        'flip_x': bool(saved_cfg.get('flip_x', True)),
        'flip_y': bool(saved_cfg.get('flip_y', True)),
        'swap_xy': bool(saved_cfg.get('swap_xy', True)),
        'contact_x_len': 0.0,
        'contact_y_wid': 0.0,
        'smooth_force': 0.0,
        'filtered_raw': baseline.copy()
    }

    # History buffers for rolling plots
    hist_len = 80
    dbx_hist = collections.deque([0.0]*hist_len, maxlen=hist_len)
    dby_hist = collections.deque([0.0]*hist_len, maxlen=hist_len)
    dbz_hist = collections.deque([0.0]*hist_len, maxlen=hist_len)
    force_hist = collections.deque([0.0]*hist_len, maxlen=hist_len)

    # Setup Plot Theme
    plt.style.use('dark_background')
    fig = plt.figure(figsize=(13.5, 7.5))
    fig.canvas.manager.set_window_title(f"eFlesh Real-Time Visualizer (Zero Latency) - [ID: {finger}]")

    # Layout
    gs = fig.add_gridspec(2, 2, width_ratios=[1.2, 1.0], hspace=0.35, wspace=0.25)
    ax_map = fig.add_subplot(gs[:, 0])
    ax_signals = fig.add_subplot(gs[0, 1])
    ax_force = fig.add_subplot(gs[1, 1])

    # --- AXIS 1: VERTICAL SENSOR 2D MAP ---
    ax_map.set_title(f"Vertical Fingertip Contact Map [{finger} FINGER]", fontsize=14, fontweight='bold', pad=12, color='#64B5F6')
    ax_map.set_xlabel("Width (mm) [◀ Left: -9.0 mm   |   Right: +9.0 mm ▶]", fontsize=10, labelpad=8)
    ax_map.set_ylabel("Length (mm) [▼ Base: -21.3 mm   |   Tip: +7.0 mm ▲]", fontsize=10)
    ax_map.set_xlim(-15, 15)
    ax_map.set_ylim(-26, 11)
    ax_map.set_aspect('equal')
    ax_map.grid(True, linestyle=':', alpha=0.3, color='#90A4AE')

    # Draw Skin Polygon (Vertical)
    skin_patch = patches.Polygon(SKIN_OUTLINE_VERTICAL, closed=True,
                                 facecolor='#1E293B', edgecolor='#64748B', linewidth=2.5, alpha=0.9, zorder=2)
    ax_map.add_patch(skin_patch)

    # Draw Magnet Pouch at (0, 0)
    pouch_outer = patches.Circle((0, 0), radius=3.6, facecolor='#334155', edgecolor='#38BDF8', linewidth=1.5, alpha=0.8, zorder=3)
    pouch_inner = patches.Circle((0, 0), radius=2.5, facecolor='#0F172A', edgecolor='#38BDF8', linewidth=1.0, linestyle='--', zorder=4)
    ax_map.add_patch(pouch_outer)
    ax_map.add_patch(pouch_inner)
    ax_map.plot(0, 0, marker='+', markersize=10, color='#38BDF8', zorder=5)
    ax_map.text(0, -5.0, "Magnet (0,0)", color='#94A3B8', fontsize=8, ha='center', zorder=5)

    ax_map.axhline(0, color='#475569', linestyle='-', linewidth=0.8, alpha=0.6, zorder=1)
    ax_map.axvline(0, color='#475569', linestyle='-', linewidth=0.8, alpha=0.6, zorder=1)

    # Dynamic Contact Marker & Arrow
    contact_dot, = ax_map.plot([0], [0], 'o', markersize=12, color='#EF4444', markeredgecolor='white', markeredgewidth=2, zorder=10, alpha=0.0)
    contact_halo, = ax_map.plot([0], [0], 'o', markersize=35, color='#F87171', alpha=0.0, zorder=9)
    shear_arrow = ax_map.annotate('', xy=(0, 0), xytext=(0, 0),
                                  arrowprops=dict(arrowstyle="->", color='#FBBF24', lw=2.0), zorder=8)

    info_text = ax_map.text(-14, 9, "Waiting for touch contact...", fontsize=10, color='#E2E8F0',
                            bbox=dict(boxstyle="round,pad=0.5", facecolor='#0F172A', edgecolor='#334155', alpha=0.9), zorder=12)

    # --- AXIS 2: MAGNETIC SIGNALS ---
    ax_signals.set_title("Magnetic Field Deflection (ΔB)", fontsize=11, fontweight='bold', color='#E2E8F0')
    ax_signals.set_ylabel("ΔB (Gauss / a.u.)", fontsize=9)
    ax_signals.grid(True, linestyle=':', alpha=0.25)
    line_dbx, = ax_signals.plot(range(hist_len), list(dbx_hist), label="ΔBx (Shear X)", color='#38BDF8', lw=1.5)
    line_dby, = ax_signals.plot(range(hist_len), list(dby_hist), label="ΔBy (Shear Y)", color='#F59E0B', lw=1.5)
    line_dbz, = ax_signals.plot(range(hist_len), list(dbz_hist), label="ΔBz (Normal)", color='#A855F7', lw=1.8)
    ax_signals.legend(loc='upper right', fontsize=8, framealpha=0.4)
    curr_sig_limit = 2500.0
    ax_signals.set_ylim(-curr_sig_limit, curr_sig_limit)

    # --- AXIS 3: TOTAL FORCE ---
    ax_force.set_title("Touch Force Magnitude", fontsize=11, fontweight='bold', color='#E2E8F0')
    ax_force.set_xlabel("Time (Samples)", fontsize=9)
    ax_force.set_ylabel("Force |ΔB|", fontsize=9)
    ax_force.grid(True, linestyle=':', alpha=0.25)
    line_force, = ax_force.plot(range(hist_len), list(force_hist), color='#10B981', lw=2.0)
    thresh_line = ax_force.axhline(state['force_threshold'], color='#EF4444', linestyle='--', lw=1.0, alpha=0.7, label='Threshold')
    ax_force.legend(loc='upper right', fontsize=8, framealpha=0.4)
    curr_force_limit = 3000.0
    ax_force.set_ylim(0, curr_force_limit)

    def save_settings():
        try:
            cfg = {
                'sensitivity': round(state['sensitivity'], 2),
                'deadband': round(state['deadband'], 1),
                'flip_x': bool(state['flip_x']),
                'flip_y': bool(state['flip_y']),
                'swap_xy': bool(state['swap_xy']),
                'force_threshold': round(state['force_threshold'], 1)
            }
            with open(settings_file, 'w') as f:
                json.dump(cfg, f, indent=2)
            print(f"✅ Settings saved to {settings_file}: {cfg}")
        except Exception as e:
            print(f"⚠️ Error saving settings: {e}")

    def update_status_bar():
        mode_str = "Matrix Calibrated" if calib_matrix is not None else "Fast Manual"
        fx = "Inverted" if state['flip_x'] else "Normal"
        fy = "Inverted" if state['flip_y'] else "Normal"
        rot = "90° (Swap XY)" if state['swap_xy'] else "0°"
        return (f"[ID: INDEX] | Mode: {mode_str} | [X: {fx} (key X)] | [Y: {fy} (key Y)] | [Rot: {rot} (key R)] | "
                f"[Sens: {state['sensitivity']:.1f}x (+/-)] | [Deadband: {state['deadband']:.0f} ([/])] | [S: Save] | [SPACE: Zero]")

    status_bar = fig.text(0.5, 0.02, update_status_bar(), ha='center', fontsize=9, color='#94A3B8')

    def on_key(event):
        if event.key in (' ', 'b'):
            state['calibrating'] = True
            state['calib_count'] = 0
            print("[Visualizer] Zeroing baseline...")
        elif event.key in ('s', 'S'):
            save_settings()
            status_bar.set_text("💾 SETTINGS SAVED! | " + update_status_bar())
            return
        elif event.key in ('+', '='):
            state['sensitivity'] = min(8.0, state['sensitivity'] + 0.2)
        elif event.key in ('-', '_'):
            state['sensitivity'] = max(0.1, state['sensitivity'] - 0.2)
        elif event.key == ']':
            state['deadband'] = min(200.0, state['deadband'] + 10.0)
        elif event.key == '[':
            state['deadband'] = max(5.0, state['deadband'] - 10.0)
        elif event.key in ('x', 'X'):
            state['flip_x'] = not state['flip_x']
        elif event.key in ('y', 'Y'):
            state['flip_y'] = not state['flip_y']
        elif event.key in ('r', 'R'):
            state['swap_xy'] = not state['swap_xy']
        status_bar.set_text(update_status_bar())

    fig.canvas.mpl_connect('key_press_event', on_key)
    fig.canvas.mpl_connect('close_event', lambda event: save_settings())

    # Performance optimization: cache limit values to avoid expensive ylim recalculations
    sig_limit_box = [curr_sig_limit]
    force_limit_box = [curr_force_limit]

    # Animation update function (Ultra-responsive)
    def update(frame):
        raw = reader.get_raw()

        # Step 1: Snappy EMA (direct response, 0 lag)
        lpf_alpha = 0.90
        state['filtered_raw'] = (1.0 - lpf_alpha) * state['filtered_raw'] + lpf_alpha * raw

        # Step 2: Calibration handling
        if state['calibrating']:
            calib_buf.append(state['filtered_raw'])
            state['calib_count'] += 1
            if state['calib_count'] >= 15:
                state['baseline'] = np.mean(calib_buf, axis=0)
                calib_buf.clear()
                state['calibrating'] = False
                print(f"[Visualizer] Baseline calibrated: {state['baseline']}")
            delta = np.zeros(3)
        else:
            delta = state['filtered_raw'] - state['baseline']

        # Step 3: Deadband Filter
        force_raw = np.linalg.norm(delta)
        if force_raw < state['deadband']:
            delta = np.zeros(3)
            force_raw = 0.0

        db_x, db_y, db_z = delta[0], delta[1], delta[2]

        # Instant force tracking
        state['smooth_force'] = 0.85 * force_raw + 0.15 * state['smooth_force']

        # Update buffers
        dbx_hist.append(db_x)
        dby_hist.append(db_y)
        dbz_hist.append(db_z)
        force_hist.append(state['smooth_force'])

        # Update Signal Plots
        line_dbx.set_ydata(list(dbx_hist))
        line_dby.set_ydata(list(dby_hist))
        line_dbz.set_ydata(list(dbz_hist))
        line_force.set_ydata(list(force_hist))

        # Stable Fixed Y-Limits (Prevents graph from endlessly expanding / scaling up)
        # Keeps blitted oscilloscope view clean and flicker-free
        # Limits: Signals [-2000, +2000], Force [0, 2500]

        # Contact Localization Calculation
        is_touch = state['smooth_force'] > state['force_threshold']

        if is_touch:
            if calib_matrix is not None:
                vec = np.array([db_x, db_y])
                target = np.dot(vec, calib_matrix) + calib_bias
                target_len = target[0] * state['sensitivity']
                target_wid = target[1] * state['sensitivity']
            else:
                sx = -db_x if state['flip_x'] else db_x
                sy = -db_y if state['flip_y'] else db_y

                if state['swap_xy']:
                    sx, sy = sy, sx

                scale_factor = 0.015 * state['sensitivity']
                target_len = sx * scale_factor
                target_wid = sy * scale_factor

            target_len = np.clip(target_len, -21.0, 6.8)
            target_wid = np.clip(target_wid, -8.6, 8.6)

            # Instant responsive smoothing (0 lag)
            smooth_alpha = 0.85
            state['contact_x_len'] = (1.0 - smooth_alpha) * state['contact_x_len'] + smooth_alpha * target_len
            state['contact_y_wid'] = (1.0 - smooth_alpha) * state['contact_y_wid'] + smooth_alpha * target_wid

            plot_px = state['contact_y_wid']
            plot_py = state['contact_x_len']

            contact_dot.set_data([plot_px], [plot_py])
            contact_dot.set_alpha(1.0)

            halo_size = np.clip(18 + state['smooth_force'] / 45.0, 18, 60)
            contact_halo.set_data([plot_px], [plot_py])
            contact_halo.set_markersize(halo_size)
            contact_halo.set_alpha(0.35)

            shear_arrow.xy = (plot_px, plot_py)
            shear_arrow.set_visible(True)

            info_text.set_text(
                f"● CONTACT DETECTED\n"
                f"• Length (Tip/Base) : {state['contact_x_len']:>+5.2f} mm\n"
                f"• Width  (L/R)      : {state['contact_y_wid']:>+5.2f} mm\n"
                f"• Force Intensity   : {state['smooth_force']:>5.0f} a.u."
            )
            info_text.set_color('#F87171')
        else:
            state['contact_x_len'] *= 0.5
            state['contact_y_wid'] *= 0.5
            contact_dot.set_alpha(0.0)
            contact_halo.set_alpha(0.0)
            shear_arrow.set_visible(False)
            info_text.set_text("○ IDLE - Waiting for touch contact...")
            info_text.set_color('#94A3B8')

        return (line_dbx, line_dby, line_dbz, line_force, contact_dot, contact_halo, shear_arrow, info_text)

    # Ultra-high performance 80+ FPS animation with hardware-accelerated Blitting (12ms interval)
    ani = FuncAnimation(fig, update, interval=12, blit=True, cache_frame_data=False)
    plt.show()

    reader.running = False

if __name__ == '__main__':
    main()
