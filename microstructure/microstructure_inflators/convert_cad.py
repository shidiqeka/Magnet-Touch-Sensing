#!/usr/bin/env python3
import os
import sys
import json
import copy
import struct
import argparse
import numpy as np

def stl_to_obj_scaled(stl_path, obj_path, scale=1000.0):
    vertices = []
    faces = []
    vert_map = {}
    
    with open(stl_path, 'rb') as f:
        f.seek(80)
        n_triangles = struct.unpack('<I', f.read(4))[0]
        for _ in range(n_triangles):
            data = f.read(50)
            if len(data) < 50:
                break
            pts = struct.unpack('<12fH', data)
            face_indices = []
            for i in range(3):
                pt = (round(pts[3+i*3]*scale, 4),
                      round(pts[4+i*3]*scale, 4),
                      round(pts[5+i*3]*scale, 4))
                if pt not in vert_map:
                    vert_map[pt] = len(vertices) + 1
                    vertices.append(pt)
                face_indices.append(vert_map[pt])
            faces.append(face_indices)
            
    with open(obj_path, 'w') as f:
        for v in vertices:
            f.write(f'v {v[0]} {v[1]} {v[2]}\n')
        for tri in faces:
            f.write(f'f {tri[0]} {tri[1]} {tri[2]}\n')
            
    print(f"[1/4] Scaled and converted STL ({n_triangles} tris) -> OBJ ({len(vertices)} verts, {len(faces)} faces)")
    return obj_path

def main():
    parser = argparse.ArgumentParser(description="Convert CAD STL to eFlesh Microstructure Lattice")
    parser.add_argument("--input", default="inputs/right_little_pip.STL", help="Path to input STL or OBJ file")
    parser.add_argument("--scale", type=float, default=1000.0, help="Scale factor (1000 for meters to mm)")
    parser.add_argument("--cell_size", type=float, default=4.0, help="Microstructure cell size in mm (default: 4.0)")
    parser.add_argument("--strut_scale", type=float, default=1.5, help="Multiplier to scale strut/beam thickness (default: 1.5, try 1.5-2.0 for thicker struts)")
    parser.add_argument("--resolution", type=int, default=50, help="Grid marching cubes resolution (default: 50)")
    parser.add_argument("--output", default=None, help="Output OBJ path")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    if os.path.isabs(args.input):
        input_path = args.input
    elif os.path.exists(os.path.abspath(args.input)):
        input_path = os.path.abspath(args.input)
    elif os.path.exists(os.path.join(script_dir, args.input)):
        input_path = os.path.join(script_dir, args.input)
    else:
        input_path = os.path.abspath(args.input)
    
    if not os.path.exists(input_path):
        print(f"Error: Input file {input_path} does not exist.")
        sys.exit(1)

    # 1. Scale and prepare OBJ    base_name = os.path.splitext(os.path.basename(input_path))[0]
    out_dir = os.path.join(script_dir, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    
    if input_path.lower().endswith('.stl'):
        scaled_obj = os.path.join(out_dir, f"{base_name}_scaled.obj")
        stl_to_obj_scaled(input_path, scaled_obj, scale=args.scale)
        surface_path = scaled_obj
    else:
        surface_path = input_path

    # 2. Compute bounding box and cell indices
    coords = []
    with open(surface_path) as f:
        for line in f:
            if line.startswith('v '):
                parts = line.strip().split()
                coords.append([float(parts[1]), float(parts[2]), float(parts[3])])
    coords = np.array(coords)
    bbox = [np.amin(coords, axis=0), np.amax(coords, axis=0)]
    dims = bbox[1] - bbox[0]
    print(f"[2/4] Model Dimensions (mm): {dims[0]:.2f} x {dims[1]:.2f} x {dims[2]:.2f}")

    cell_size = args.cell_size
    corner0 = list(map(int, np.ceil(bbox[0] / cell_size) - 1))
    corner1 = list(map(int, np.floor(bbox[1] / cell_size)))
    print(f"      Grid corners: corner0={corner0}, corner1={corner1}")
    grid_dims = [corner1[i] - corner0[i] + 1 for i in range(3)]
    print(f"      Grid size: {grid_dims[0]} x {grid_dims[1]} x {grid_dims[2]} ({grid_dims[0]*grid_dims[1]*grid_dims[2]} cells)")

    # 3. Load material coefficients
    matopt_path = os.path.join(os.path.dirname(script_dir), "matopt")
    sys.path.insert(0, os.path.join(matopt_path, "tools/material2geometry"))
    from material2geometry import Material2Geometry
    
    coeff_path = os.path.join(matopt_path, "tools/material2geometry/0646_geo_1_coeffs.txt")
    mat2geo = Material2Geometry(in_path=coeff_path)

    nu = 0.09
    def young(i, j, k):
        # Layered Young's modulus gradient along depth (z-direction / k index)
        # Higher E produces stronger, more printable TPU microstructures
        rel_k = k - corner0[2]
        if rel_k <= 1:
            return 0.002
        elif rel_k <= 3:
            return 0.005
        else:
            return 0.010

    patterns = []
    pattern_file = os.path.join(script_dir, "data/patterns/3D/reference_wires/pattern0646.wire")
    entry = {
        "params": [],
        "symmetry": "Cubic",
        "pattern": pattern_file,
        "index": [0, 0, 0]
    }

    sample_thick = None
    for i in range(corner0[0], corner1[0] + 1):
        for j in range(corner0[1], corner1[1] + 1):
            for k in range(corner0[2], corner1[2] + 1):
                geo_params = list(mat2geo.evaluate(nu, young(i, j, k)))
                # Scale strut thickness (parameters 6, 7, 8 in pattern0646 wire definition)
                if args.strut_scale != 1.0:
                    geo_params[6] = min(0.35, geo_params[6] * args.strut_scale)
                    geo_params[7] = min(0.35, geo_params[7] * args.strut_scale)
                    geo_params[8] = min(0.35, geo_params[8] * args.strut_scale)
                if sample_thick is None:
                    sample_thick = [geo_params[6] * cell_size, geo_params[7] * cell_size, geo_params[8] * cell_size]
                e = copy.deepcopy(entry)
                e["params"] = geo_params
                e["index"] = [i, j, k]
                patterns.append(e)

    print(f"      Strut Scale Multiplier: {args.strut_scale}x")
    if sample_thick:
        print(f"      Estimated Physical Strut Thickness: {sample_thick[0]:.2f} mm to {sample_thick[2]:.2f} mm")

    data_json_path = os.path.join(out_dir, f"data_{base_name}.json")
    with open(data_json_path, 'w') as f:
        json.dump(patterns, f)
    print(f"[3/4] Generated cell pattern specs: {len(patterns)} cells -> {data_json_path}")

    # 4. Execute cut_cells_cli
    cut_cells_cli = os.path.join(script_dir, "build/isosurface_inflator/cut_cells_cli")
    if not os.path.exists(cut_cells_cli):
        # Fallback check
        cut_cells_cli = os.path.join(script_dir, "build/bin/cut_cells_cli")
    
    if not os.path.exists(cut_cells_cli):
        print(f"\nError: Inflator binary not found at {cut_cells_cli}.")
        print("Please compile first by running: ./fast_build.sh")
        sys.exit(1)

    strut_suffix = f"_strut{args.strut_scale:.1f}x" if args.strut_scale != 1.0 else ""
    out_obj = args.output or os.path.join(out_dir, f"{base_name}_eflesh_lattice_{cell_size}mm{strut_suffix}.obj")
    cmd = f'"{cut_cells_cli}" -p "{data_json_path}" --surface "{surface_path}" --gridSize {cell_size} -o "{out_obj}" -r {args.resolution}'
    print(f"[4/4] Executing cut_cells_cli:\n      {cmd}")
    ret = os.system(cmd)
    if ret == 0:
        print(f"\n SUCCESS! eFlesh lattice created at:\n {out_obj}")
    else:
        print(f"\n❌ cut_cells_cli exited with return code {ret}")

if __name__ == '__main__':
    main()
