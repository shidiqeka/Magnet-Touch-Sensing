#!/usr/bin/env python3
import os
import sys
import glob
import time
import subprocess

script_dir = os.path.dirname(os.path.abspath(__file__))
inputs_dir = os.path.join(script_dir, "inputs")
outputs_dir = os.path.join(script_dir, "outputs")
os.makedirs(outputs_dir, exist_ok=True)

files = sorted(glob.glob(os.path.join(inputs_dir, "*.STL")) + glob.glob(os.path.join(inputs_dir, "*.stl")))
if not files:
    print("No STL files found in inputs/")
    sys.exit(1)

print(f"=== Found {len(files)} STL files to process ===")
for f in files:
    print(f"  - {os.path.basename(f)}")

start_all = time.time()
for idx, stl_file in enumerate(files, 1):
    base_name = os.path.splitext(os.path.basename(stl_file))[0]
    print(f"\n=======================================================")
    print(f"[{idx}/{len(files)}] Processing: {base_name}")
    print(f"=======================================================")
    
    t0 = time.time()
    # Step 1: CAD to Lattice (cell_size=4.0mm, strut_scale=1.5x)
    print("\n>>> [Step 1/2] Generating eFlesh Lattice (Strut 1.5x, Cell 4.0mm)...")
    cmd_step1 = [
        sys.executable,
        os.path.join(script_dir, "convert_cad.py"),
        "--input", stl_file,
        "--cell_size", "4.0",
        "--strut_scale", "1.5",
        "--resolution", "40"
    ]
    res1 = subprocess.run(cmd_step1)
    if res1.returncode != 0:
        print(f"❌ Failed Step 1 for {base_name}")
        continue

    lattice_obj = os.path.join(outputs_dir, f"{base_name}_eflesh_lattice_4.0mm_strut1.5x.obj")
    scaled_obj = os.path.join(outputs_dir, f"{base_name}_scaled.obj")
    final_stl = os.path.join(outputs_dir, f"{base_name}_skin0.8mm_pouch_2x1mm_strut1.5x.stl")

    # Step 2: Add 0.8mm skin + 2x1mm magnet pouch
    print("\n>>> [Step 2/2] Adding 0.8mm Solid Skin & 2x1mm Magnet Pouch...")
    cmd_step2 = [
        sys.executable,
        os.path.join(script_dir, "create_pouch_fast.py"),
        "--input", lattice_obj,
        "--output", final_stl,
        "--surface", scaled_obj,
        "--skin", "0.8",
        "--diam", "2.0",
        "--depth", "1.0",
        "--axis", "+z"
    ]
    res2 = subprocess.run(cmd_step2)
    if res2.returncode != 0:
        print(f"❌ Failed Step 2 for {base_name}")
        continue

    dt = time.time() - t0
    print(f"\n✅ Completed {base_name} in {dt:.1f}s")
    print(f"   Final STL: {final_stl}")

total_dt = time.time() - start_all
print(f"\n🎉 ALL {len(files)} MODELS PROCESSED IN {total_dt:.1f}s!")

