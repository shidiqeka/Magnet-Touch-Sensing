#!/usr/bin/env python3
"""
Fast Pouch Generator for eFlesh Microstructure Lattice
Performs Stage 2 Boolean operations using Trimesh + Manifold engine (No Blender needed).
"""
import os
import sys
import argparse
import trimesh
import numpy as np

def make_cylinder(radius, height, sections=64):
    return trimesh.creation.cylinder(radius=radius, height=height, sections=sections)

def build_pouch_geometry(magnet_diameter, magnet_thickness, center, direction=[0, 0, 1], clearance=0.1, wall=1.0):
    # Inner cavity exactly matching requested magnet dimensions (+ clearance for 3D printed press fit)
    cavity_r = (float(magnet_diameter) / 2.0) + clearance  # e.g., 5.0mm -> radius 2.6mm (diam 5.2mm)
    cavity_h = float(magnet_thickness)                     # e.g., 3.0mm depth
    
    outer_r = cavity_r + wall                              # 1.0mm solid wall around cavity
    outer_h = cavity_h + 2.0 * wall                        # 1.0mm floor + 1.0mm ceiling
    
    lip_r = cavity_r * 0.8                                 # retaining lip opening to secure magnet
    lip_h = wall * 2.5                                     # cuts cleanly through the top

    convex_hull = make_cylinder(radius=outer_r * 0.99, height=outer_h * 0.99)
    pouch_outer = make_cylinder(radius=outer_r, height=outer_h)
    cyl_cavity = make_cylinder(radius=cavity_r, height=cavity_h)
    cyl_lip = make_cylinder(radius=lip_r, height=lip_h)
    cyl_lip.apply_translation([0, 0, outer_h / 2.0])

    pouch = pouch_outer.difference(cyl_cavity, engine='manifold')
    pouch = pouch.difference(cyl_lip, engine='manifold')

    # Translate so base is at z=0 and height extends towards +Z
    offset = [0, 0, outer_h / 2.0]
    convex_hull.apply_translation(offset)
    pouch.apply_translation(offset)

    # Rotate from [0, 0, 1] to target direction
    direction = np.array(direction, dtype=float)
    norm = np.linalg.norm(direction)
    if norm > 1e-6:
        direction = direction / norm
        if not np.allclose(direction, [0, 0, 1]):
            R = trimesh.geometry.align_vectors([0, 0, 1], direction)
            convex_hull.apply_transform(R)
            pouch.apply_transform(R)

    # Translate to desired center location
    convex_hull.apply_translation(center)
    pouch.apply_translation(center)

    return convex_hull, pouch

def parse_axis(axis_str):
    mapping = {
        '+x': [1, 0, 0], '-x': [-1, 0, 0],
        '+y': [0, 1, 0], '-y': [0, -1, 0],
        '+z': [0, 0, 1], '-z': [0, 0, -1],
        'x': [1, 0, 0], 'y': [0, 1, 0], 'z': [0, 0, 1]
    }
    return mapping.get(axis_str.lower().strip(), [0, 0, 1])

def add_pouches(lattice_path, output_path, magnets, default_axis='+z', wall=1.0, clearance=0.1, skin=0.0, surface_path=None):
    print(f"[Stage 2] Loading lattice mesh from: {lattice_path}")
    lattice = trimesh.load(lattice_path)
    
    # Auto-detect units: if max extent < 0.5, the file was exported in meters
    if lattice.extents.max() < 0.5:
        print(f"          [Auto-Scale] Detected mesh extents in meters (max extent: {lattice.extents.max():.4f}m).")
        print(f"          Auto-scaling mesh by 1000x to millimeters...")
        lattice.apply_scale(1000.0)

    print(f"          Mesh vertices: {len(lattice.vertices)}, faces: {len(lattice.faces)}")
    print(f"          Bounds (mm): {lattice.bounds.tolist()}")
    
    current_mesh = lattice

    # Step A: Optional solid outer skin (epidermis / wall)
    if skin > 0.0:
        if not surface_path or not os.path.exists(surface_path):
            # Auto-detect corresponding scaled surface OBJ
            parent_dir = os.path.dirname(os.path.abspath(lattice_path))
            name_no_ext = os.path.splitext(os.path.basename(lattice_path))[0]
            # Strip common suffixes
            for suffix in ['_eflesh_lattice_4.0mm', '_eflesh_lattice_2.0mm', '_with_pouch', '_pouch_3x1mm']:
                name_no_ext = name_no_ext.replace(suffix, '')
            cand = os.path.join(parent_dir, f"{name_no_ext}_scaled.obj")
            if os.path.exists(cand):
                surface_path = cand

        if surface_path and os.path.exists(surface_path):
            try:
                print(f"[Stage 2] Generating solid outer skin ({skin}mm thickness) from: {surface_path}")
                m_outer = trimesh.load(surface_path)
                if m_outer.extents.max() < 0.5:
                    m_outer.apply_scale(1000.0)
                if not m_outer.is_watertight:
                    trimesh.repair.fill_holes(m_outer)
                    trimesh.repair.fix_normals(m_outer)
                    trimesh.repair.fix_winding(m_outer)
                v_inner = m_outer.vertices - float(skin) * m_outer.vertex_normals
                m_inner = trimesh.Trimesh(vertices=v_inner, faces=m_outer.faces)
                shell = m_outer.difference(m_inner, engine='manifold', check_volume=False)
                print("          Fusing solid outer shell with lattice structure...")
                current_mesh = current_mesh.union(shell, engine='manifold', check_volume=False)
                print(f"          Lattice + Skin complete ({len(current_mesh.vertices)} verts, {len(current_mesh.faces)} faces)")
            except Exception as e:
                print(f"          [Warning] Could not generate outer skin ({e}). Proceeding with lattice only.")
        else:
            print(f"          [Warning] Surface mesh not found ({surface_path}), skipping solid skin.")

    # Step B: Pouch operations
    for idx, item in enumerate(magnets):
        diam = item[0]
        thick = item[1]
        center = item[2]
        direction = item[3] if len(item) > 3 else parse_axis(default_axis)
        print(f"[Stage 2] Adding pouch #{idx+1}: diam={diam}mm, thick={thick}mm at center={center}, axis={direction}, wall={wall}mm")
        convex_hull, pouch = build_pouch_geometry(diam, thick, center, direction=direction, clearance=clearance, wall=wall)
        print("          Carving hollow space (DIFFERENCE)...")
        current_mesh = current_mesh.difference(convex_hull, engine='manifold', check_volume=False)
        print("          Fusing pouch structure (UNION)...")
        current_mesh = current_mesh.union(pouch, engine='manifold', check_volume=False)

    print(f"[Stage 2] Exporting result to: {output_path}")
    current_mesh.export(output_path)
    
    # Ensure both STL and OBJ formats are saved
    base, ext = os.path.splitext(output_path)
    if ext.lower() == '.obj':
        stl_path = base + '.stl'
        current_mesh.export(stl_path)
        print(f"          Also exported STL to: {stl_path}")
    elif ext.lower() == '.stl':
        obj_path = base + '.obj'
        current_mesh.export(obj_path)
        print(f"          Also exported OBJ to: {obj_path}")
        
    print(" SUCCESS! Stage 2 complete.")
    return output_path

def main():
    parser = argparse.ArgumentParser(description="Add magnet pouches and solid skin to eFlesh lattice mesh")
    parser.add_argument("--input", default="outputs/right_little_pip_eflesh_lattice_4.0mm.obj", help="Path to lattice OBJ/STL")
    parser.add_argument("--output", default="outputs/right_little_pip_with_pouch.obj", help="Output path")
    parser.add_argument("--axis", default="+z", help="Pouch orientation axis (+x, -x, +y, -y, +z, -z)")
    parser.add_argument("--diam", type=float, default=5.0, help="Magnet diameter in mm (default: 5.0)")
    parser.add_argument("--depth", type=float, default=3.0, help="Magnet cavity depth in mm (default: 3.0)")
    parser.add_argument("--center", nargs=3, type=float, default=None, metavar=('X', 'Y', 'Z'),
                        help="Pouch center coordinates: X Y Z (default: 0 0 0)")
    parser.add_argument("--wall", type=float, default=1.0, help="Wall thickness around magnet in mm (default: 1.0)")
    parser.add_argument("--skin", type=float, default=0.0, help="Thickness of solid outer skin in mm (e.g. 0.8)")
    parser.add_argument("--surface", default=None, help="Path to original CAD surface mesh for skin generation")
    parser.add_argument("--no_pouch", action="store_true", help="Only generate skinned lattice without adding pouch")
    parser.add_argument("--clearance", type=float, default=0.1, help="Radial clearance in mm (default: 0.1)")
    parser.add_argument("--magnet", action="append", nargs=5, type=float, metavar=('DIAM', 'THICK', 'X', 'Y', 'Z'),
                        help="Magnet spec: diam thick x y z (can specify multiple)")
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

    if os.path.isabs(args.output):
        output_path = args.output
    elif os.path.dirname(args.output) and os.path.exists(os.path.dirname(os.path.abspath(args.output))):
        output_path = os.path.abspath(args.output)
    else:
        output_path = os.path.join(script_dir, args.output)

    if not os.path.exists(input_path):
        print(f"Error: Input file {input_path} not found.")
        sys.exit(1)

    axis_vec = parse_axis(args.axis)
    if args.no_pouch:
        magnets = []
    elif args.magnet:
        magnets = [[m[0], m[1], [m[2], m[3], m[4]], axis_vec] for m in args.magnet]
    elif args.center:
        magnets = [
            [args.diam, args.depth, args.center, axis_vec]
        ]
    else:
        # If input has 'pip', default to sample PIP joint position; otherwise origin [0, 0, 0]
        if "pip" in os.path.basename(input_path).lower():
            center_pos = [6.4, -0.2, -16.0]
        else:
            center_pos = [0.0, 0.0, 0.0]
        magnets = [
            [args.diam, args.depth, center_pos, axis_vec]
        ]

    add_pouches(input_path, output_path, magnets, default_axis=args.axis, wall=args.wall, clearance=args.clearance, skin=args.skin, surface_path=args.surface)

if __name__ == '__main__':
    main()
