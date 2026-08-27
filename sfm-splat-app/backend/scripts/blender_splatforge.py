"""
Blender headless script — SplatForge-ready scene setup.
Usage: blender --background --python blender_splatforge.py -- --ply /path/to/output.ply --out /path/to/scene.blend
"""
import bpy
import sys
import argparse
import os

# Parse custom args after '--'
argv = sys.argv[sys.argv.index("--") + 1:]
parser = argparse.ArgumentParser()
parser.add_argument("--ply", required=True, help="Path to the .ply splat file")
parser.add_argument("--out", required=True, help="Output .blend path")
args = parser.parse_args(argv)

# Clean default scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# Import PLY as mesh (SplatForge will re-interpret it)
bpy.ops.wm.ply_import(filepath=args.ply)
splat_obj = bpy.context.selected_objects[0]
splat_obj.name = "GaussianSplat"

# Tag for SplatForge detection
splat_obj["splatforge_ready"] = True
splat_obj["ply_source"] = args.ply
splat_obj["pipeline_tool"] = "3dgs-pipeline-app"

# Basic scene setup
# World shader — neutral grey HDRI-ready
world = bpy.context.scene.world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (0.05, 0.05, 0.05, 1.0)
bg.inputs[1].default_value = 1.0

# Area light
bpy.ops.object.light_add(type='AREA', location=(3, -3, 5))
light = bpy.context.active_object
light.data.energy = 500
light.data.size = 2.0

# Camera — orbital position facing scene center
bpy.ops.object.camera_add(location=(4, -4, 3))
cam = bpy.context.active_object
cam.data.lens = 35
# Point camera at origin
import mathutils
direction = mathutils.Vector((0, 0, 0)) - cam.location
rot_quat = direction.to_track_quat('-Z', 'Y')
cam.rotation_euler = rot_quat.to_euler()
bpy.context.scene.camera = cam

# Render settings — EEVEE Next
bpy.context.scene.render.engine = 'BLENDER_EEVEE_NEXT'
bpy.context.scene.render.resolution_x = 1920
bpy.context.scene.render.resolution_y = 1080

# Save
bpy.ops.wm.save_as_mainfile(filepath=args.out)
print(f"[3DGS Pipeline] Scene saved: {args.out}")
print("[3DGS Pipeline] Open in Blender and install SplatForge addon to begin relighting.")
