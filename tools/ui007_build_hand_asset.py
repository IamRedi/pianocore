#!/usr/bin/env python3
"""Build the PIANO-UI-007 Tier-A hand asset from the Owner's Fal GLB.

The source is an unrigged, textured mesh. This pipeline creates Piano Core's
own armature and weights, normalizes the left hand, bakes a right-hand
geometric/rigged derivative, and exports the sole production GLB::

    blender --background --factory-startup \
      --python tools/ui007_build_hand_asset.py -- \
      --source /home/dev/Downloads/wmfS8MobJ9dzDAFs8m2Mm_model.glb \
      --output src/piano_core/web/static/assets/piano-hand-tier-a.glb \
      --report docs/work/evidence/ui007-tier-a/asset-build-report.json

No CGTrader mesh, texture, skeleton, weight or other asset data is read by
this pipeline. The semantic contract is wrist plus three joints per finger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

import bpy
import bmesh
from mathutils import Matrix, Vector


EXPECTED_SOURCE_SHA256 = "330640358d9fdc337e026ef2a0602d77fd5df6aaba85dbd1050885ede971c5b8"
TARGET_WRIST_TO_MIDDLE_TIP_M = 0.19

# Explicit Blender-space landmarks for the exact Fal source. X is lateral;
# Z runs from wrist (+) to fingertips (-). Y is sampled from the mesh locally
# so the rest armature follows the source's authored finger curl.
LANDMARK_XZ = {
    "wrist": (0.000, 0.790),
    "thumb": ((0.285, 0.175), (0.390, -0.015), (0.455, -0.225), (0.465, -0.445)),
    "index": ((0.215, 0.105), (0.225, -0.235), (0.225, -0.525), (0.220, -0.775)),
    "middle": ((0.020, 0.095), (0.020, -0.285), (0.018, -0.610), (0.015, -0.895)),
    "ring": ((-0.165, 0.095), (-0.175, -0.265), (-0.180, -0.565), (-0.185, -0.830)),
    "pinky": ((-0.335, 0.115), (-0.355, -0.185), (-0.360, -0.440), (-0.365, -0.675)),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    raw = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", default=EXPECTED_SOURCE_SHA256)
    return parser.parse_args(raw)


def glb_document(path: Path) -> dict:
    with path.open("rb") as stream:
        magic, version, _length = struct.unpack("<4sII", stream.read(12))
        if magic != b"glTF" or version != 2:
            raise ValueError(f"{path} is not a glTF 2.0 GLB")
        json_length, json_type = struct.unpack("<II", stream.read(8))
        if json_type != 0x4E4F534A:
            raise ValueError("GLB first chunk is not JSON")
        return json.loads(stream.read(json_length).decode("utf-8"))


def glb_metrics(path: Path) -> dict[str, object]:
    document = glb_document(path)
    vertices = triangles = maximum_influences = 0
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            position = document["accessors"][primitive["attributes"]["POSITION"]]
            vertices += position["count"]
            indices = primitive.get("indices")
            triangles += (document["accessors"][indices]["count"] // 3
                          if indices is not None else position["count"] // 3)
            if "JOINTS_0" in primitive["attributes"]:
                maximum_influences = max(maximum_influences, 4)
            if "JOINTS_1" in primitive["attributes"]:
                maximum_influences = max(maximum_influences, 8)
    return {
        "meshes": len(document.get("meshes", [])), "vertices": vertices,
        "triangles": triangles, "materials": len(document.get("materials", [])),
        "textures": len(document.get("textures", [])), "skins": len(document.get("skins", [])),
        "animations": len(document.get("animations", [])),
        "maximum_exported_influences": maximum_influences,
    }


def source_metrics(path: Path) -> dict[str, object]:
    document = glb_document(path)
    accessors = document.get("accessors", [])
    vertices = triangles = 0
    bounds = None
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            position = accessors[primitive["attributes"]["POSITION"]]
            vertices += position["count"]
            triangles += accessors[primitive["indices"]]["count"] // 3
            bounds = {"min": position.get("min"), "max": position.get("max")}
    return {
        "meshes": len(document.get("meshes", [])), "vertices": vertices,
        "triangles": triangles, "materials": len(document.get("materials", [])),
        "textures": len(document.get("textures", [])), "skins": len(document.get("skins", [])),
        "animations": len(document.get("animations", [])), "bounding_box_gltf": bounds,
        "embedded_texture": {"mime_type": "image/jpeg", "width": 2048, "height": 2048},
    }


def require_source(args: argparse.Namespace) -> str:
    if not args.source.is_file():
        raise FileNotFoundError(args.source)
    actual = sha256(args.source)
    if actual != args.expected_source_sha256:
        raise ValueError(f"Fal source SHA-256 mismatch: expected {args.expected_source_sha256}, got {actual}")
    metrics = source_metrics(args.source)
    expected = {"meshes": 1, "vertices": 23382, "triangles": 31277,
                "materials": 1, "textures": 1, "skins": 0, "animations": 0}
    mismatch = {key: (metrics[key], value) for key, value in expected.items() if metrics[key] != value}
    if mismatch:
        raise ValueError(f"unexpected Fal source structure: {mismatch}")
    return actual


def import_source(path: Path) -> bpy.types.Object:
    result = bpy.ops.import_scene.gltf(filepath=str(path))
    if "FINISHED" not in result:
        raise RuntimeError(f"glTF import failed: {result}")
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    if len(meshes) != 1 or armatures:
        raise ValueError(f"expected one unrigged mesh, got meshes={len(meshes)}, armatures={len(armatures)}")
    mesh = meshes[0]
    bpy.context.view_layer.objects.active = mesh
    mesh.select_set(True)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if len(mesh.data.uv_layers) != 1 or len(mesh.data.materials) != 1:
        raise ValueError("Fal source must retain exactly one UV set and one material")
    mesh.name = "HandMesh.L"
    mesh.data.name = "PianoHandFalMesh.L"
    mesh.data.materials[0].name = "PianoHandFalMaterial"
    return mesh


def local_center_y(mesh: bpy.types.Object, x: float, z: float) -> float:
    candidates = [v.co.y for v in mesh.data.vertices
                  if abs(v.co.x - x) <= 0.075 and abs(v.co.z - z) <= 0.060]
    if not candidates:
        candidates = [v.co.y for v in mesh.data.vertices
                      if abs(v.co.x - x) <= 0.11 and abs(v.co.z - z) <= 0.10]
    if not candidates:
        raise ValueError(f"no mesh samples near landmark x={x}, z={z}")
    return (min(candidates) + max(candidates)) * 0.5


def source_landmarks(mesh: bpy.types.Object) -> dict[str, object]:
    wx, wz = LANDMARK_XZ["wrist"]
    landmarks: dict[str, object] = {"wrist": Vector((wx, local_center_y(mesh, wx, wz), wz))}
    for finger in ("thumb", "index", "middle", "ring", "pinky"):
        landmarks[finger] = [Vector((x, local_center_y(mesh, x, z), z))
                             for x, z in LANDMARK_XZ[finger]]
    return landmarks


def normalize(mesh: bpy.types.Object, landmarks: dict[str, object]) -> float:
    wrist = landmarks["wrist"]
    source_length = (landmarks["middle"][-1] - wrist).length
    if not 1.4 < source_length < 2.1:
        raise ValueError(f"unexpected source wrist-to-middle-tip length: {source_length}")
    scale = TARGET_WRIST_TO_MIDDLE_TIP_M / source_length
    transform = (Matrix.Scale(scale, 4) @ Matrix.Scale(-1.0, 4, (0.0, 1.0, 0.0))
                 @ Matrix.Translation(-wrist))
    mesh.data.transform(transform)
    bm = bmesh.new(); bm.from_mesh(mesh.data)
    bmesh.ops.reverse_faces(bm, faces=bm.faces[:]); bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh.data); bm.free()
    mesh.matrix_world = Matrix.Identity(4)
    for key, value in list(landmarks.items()):
        landmarks[key] = [transform @ point for point in value] if isinstance(value, list) else transform @ value
    return scale


def create_armature(landmarks: dict[str, object], side: str = "L") -> bpy.types.Object:
    data = bpy.data.armatures.new(f"PianoHandArmature.{side}")
    armature = bpy.data.objects.new(f"HandArmature.{side}", data)
    bpy.context.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature; armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    wrist = data.edit_bones.new(f"wrist.{side}")
    wrist.head = landmarks["wrist"]; wrist.tail = Vector((0.0, 0.0, -0.060))
    for finger in ("thumb", "index", "middle", "ring", "pinky"):
        points = landmarks[finger]; parent = wrist
        for joint in range(3):
            bone = data.edit_bones.new(f"{finger}.{joint + 1:02d}.{side}")
            bone.head = points[joint]; bone.tail = points[joint + 1]
            bone.parent = parent; bone.use_connect = joint > 0; parent = bone
        end = data.edit_bones.new(f"{finger}.03.{side}_end")
        end.head = points[-1]; end.tail = points[-1] + (points[-1] - points[-2]).normalized() * 0.008
        end.parent = parent; end.use_connect = True; end.use_deform = False
    bpy.ops.object.mode_set(mode="OBJECT")
    armature.show_in_front = True
    return armature


def closest_chain_position(point: Vector, chain: list[Vector]) -> tuple[float, float, list[float]]:
    """Return distance, arc position, and cumulative lengths on a polyline."""
    cumulative = [0.0]
    for start, end in zip(chain, chain[1:]):
        cumulative.append(cumulative[-1] + (end - start).length)
    best_distance = float("inf")
    best_position = 0.0
    for index, (start, end) in enumerate(zip(chain, chain[1:])):
        segment = end - start
        length_squared = segment.length_squared
        parameter = 0.0 if length_squared == 0 else max(0.0, min(1.0, (point - start).dot(segment) / length_squared))
        nearest = start + segment * parameter
        distance = (point - nearest).length
        if distance < best_distance:
            best_distance = distance
            best_position = cumulative[index] + parameter * segment.length
    return best_distance, best_position, cumulative


def bind_custom(mesh: bpy.types.Object, armature: bpy.types.Object, landmarks: dict[str, object]) -> None:
    """Create deterministic, reviewable weights for this exact pianist hand.

    Finger ownership is selected by the nearest authored centerline. Within a
    digit, weights interpolate between wrist and the three phalange controls
    along arc length. This avoids cross-finger heat leakage and guarantees
    normalized, attached vertices while retaining smooth joint transitions.
    """
    modifier = mesh.modifiers.new("PianoHandSkin", "ARMATURE")
    modifier.object = armature
    groups = {bone.name: mesh.vertex_groups.new(name=bone.name)
              for bone in armature.data.bones if bone.use_deform}
    fingers = ("thumb", "index", "middle", "ring", "pinky")
    for vertex in mesh.data.vertices:
        point = vertex.co
        candidates = []
        for finger in fingers:
            distance, position, cumulative = closest_chain_position(point, landmarks[finger])
            # Thumb receives a small penalty outside its lateral lobe so the
            # adjacent index/palm web is never pulled into opposition.
            if finger == "thumb" and point.x < 0.020:
                distance += 0.040
            candidates.append((distance, finger, position, cumulative))
        distance, finger, position, cumulative = min(candidates)

        # The wrist/palm owns everything proximal to the finger bases. A
        # narrow blend below the webbing gives the MCPs volume-preserving
        # transitions without allowing one digit to influence another.
        palm_boundary = -0.066
        if point.z >= palm_boundary and not (finger == "thumb" and distance < 0.028):
            groups["wrist.L"].add([vertex.index], 1.0, "REPLACE")
            continue

        segment_lengths = [cumulative[i + 1] - cumulative[i] for i in range(3)]
        controls = [
            (0.0, "wrist.L"),
            (segment_lengths[0] * 0.32, f"{finger}.01.L"),
            (cumulative[1] + segment_lengths[1] * 0.42, f"{finger}.02.L"),
            (cumulative[2] + segment_lengths[2] * 0.48, f"{finger}.03.L"),
        ]
        if position <= controls[0][0]:
            weights = [(controls[0][1], 1.0)]
        elif position >= controls[-1][0]:
            weights = [(controls[-1][1], 1.0)]
        else:
            weights = []
            for (left_s, left_name), (right_s, right_name) in zip(controls, controls[1:]):
                if left_s <= position <= right_s:
                    blend = (position - left_s) / (right_s - left_s)
                    weights = [(left_name, 1.0 - blend), (right_name, blend)]
                    break
        for name, weight in weights:
            if weight > 1e-8:
                groups[name].add([vertex.index], weight, "REPLACE")


def limit_and_normalize_weights(mesh: bpy.types.Object, maximum: int = 4) -> dict[str, int]:
    trimmed = unweighted = 0
    for vertex in mesh.data.vertices:
        weighted = sorted(((entry.group, entry.weight) for entry in vertex.groups if entry.weight > 1e-8),
                          key=lambda item: (-item[1], item[0]))
        if not weighted:
            unweighted += 1; continue
        if len(weighted) > maximum: trimmed += 1
        keep = weighted[:maximum]; total = sum(weight for _group, weight in keep)
        for group, _weight in weighted[maximum:]: mesh.vertex_groups[group].remove([vertex.index])
        for group, weight in keep: mesh.vertex_groups[group].add([vertex.index], weight / total, "REPLACE")
    if unweighted:
        raise ValueError(f"{unweighted} detached/unweighted vertices after skin binding")
    memberships = {}
    for group in mesh.vertex_groups:
        memberships[group.name] = sum(
            1 for vertex in mesh.data.vertices
            if any(entry.group == group.index and entry.weight > 1e-8 for entry in vertex.groups)
        )
    return {"vertices_trimmed_to_four_influences": trimmed,
            "unweighted_vertices": unweighted,
            "weighted_vertex_memberships": memberships}


def exercise_deformation(mesh: bpy.types.Object, armature: bpy.types.Object) -> dict[str, object]:
    """Exercise every joint and reject non-finite or explosive deformation."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    baseline = [vertex.co.copy() for vertex in mesh.data.vertices]
    for finger in ("thumb", "index", "middle", "ring", "pinky"):
        for joint, angle in enumerate((-0.32, -0.58, -0.36), start=1):
            pose_bone = armature.pose.bones[f"{finger}.{joint:02d}.L"]
            pose_bone.rotation_mode = "XYZ"
            pose_bone.rotation_euler.x = angle
    bpy.context.view_layer.update()
    evaluated = mesh.evaluated_get(depsgraph)
    deformed = [vertex.co.copy() for vertex in evaluated.data.vertices]
    if len(deformed) != len(baseline):
        raise ValueError("deformation changed vertex count")
    values = [component for point in deformed for component in point]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("non-finite vertex produced by rig exercise")
    maximum_displacement = max((after - before).length for before, after in zip(baseline, deformed))
    if maximum_displacement > 0.20:
        raise ValueError(f"explosive rig deformation: {maximum_displacement}")
    for pose_bone in armature.pose.bones:
        pose_bone.rotation_mode = "QUATERNION"
        pose_bone.rotation_quaternion.identity()
        pose_bone.location.zero()
        pose_bone.scale = (1.0, 1.0, 1.0)
    bpy.context.view_layer.update()
    return {
        "exercise": "all MCP/PIP/DIP chains flexed together at 0.32/0.58/0.36 radians",
        "vertices_checked": len(deformed),
        "finite_vertices": True,
        "maximum_vertex_displacement_m": maximum_displacement,
    }


def duplicate_right(left_armature: bpy.types.Object, left_mesh: bpy.types.Object) -> tuple[bpy.types.Object, bpy.types.Object]:
    right_armature = left_armature.copy(); right_armature.data = left_armature.data.copy()
    right_armature.name = "HandArmature.R"; right_armature.data.name = "PianoHandArmature.R"
    right_mesh = left_mesh.copy(); right_mesh.data = left_mesh.data.copy()
    right_mesh.name = "HandMesh.R"; right_mesh.data.name = "PianoHandFalMesh.R"
    bpy.context.collection.objects.link(right_armature); bpy.context.collection.objects.link(right_mesh)
    for modifier in right_mesh.modifiers:
        if modifier.type == "ARMATURE": modifier.object = right_armature
    mirror = Matrix.Scale(-1.0, 4, (1.0, 0.0, 0.0)); right_mesh.data.transform(mirror)
    bm = bmesh.new(); bm.from_mesh(right_mesh.data)
    bmesh.ops.reverse_faces(bm, faces=bm.faces[:]); bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(right_mesh.data); bm.free(); right_armature.data.transform(mirror)
    for bone in right_armature.data.bones: bone.name = bone.name.replace(".L", ".R")
    for group in right_mesh.vertex_groups: group.name = group.name.replace(".L", ".R")
    return right_armature, right_mesh


def clean_scene(keep: set[bpy.types.Object]) -> None:
    for obj in list(bpy.context.scene.objects):
        if obj not in keep: bpy.data.objects.remove(obj, do_unlink=True)
    for action in list(bpy.data.actions): bpy.data.actions.remove(action)


def export_glb(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True); bpy.ops.object.select_all(action="SELECT")
    result = bpy.ops.export_scene.gltf(
        filepath=str(output), export_format="GLB", use_selection=True, export_yup=True,
        export_animations=False, export_cameras=False, export_lights=False,
        export_apply=False, export_skins=True, export_tangents=False,
        export_all_influences=False, export_image_format="AUTO")
    if "FINISHED" not in result: raise RuntimeError(f"glTF export failed: {result}")


def bone_inventory(armature: bpy.types.Object) -> list[dict[str, object]]:
    return [{"name": bone.name, "parent": bone.parent.name if bone.parent else None,
             "deform": bone.use_deform,
             "head": [round(v, 8) for v in bone.head_local],
             "tail": [round(v, 8) for v in bone.tail_local]}
            for bone in armature.data.bones]


def main() -> None:
    args = parse_args(); source_hash = require_source(args)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mesh = import_source(args.source); imported_dimensions = [round(v, 8) for v in mesh.dimensions]
    landmarks = source_landmarks(mesh); uniform_scale = normalize(mesh, landmarks)
    armature = create_armature(landmarks); bind_custom(mesh, armature, landmarks)
    weight_stats = limit_and_normalize_weights(mesh)
    deformation_check = exercise_deformation(mesh, armature)
    right_armature, right_mesh = duplicate_right(armature, mesh)
    clean_scene({armature, mesh, right_armature, right_mesh}); export_glb(args.output)
    output_metrics = glb_metrics(args.output)
    if output_metrics["skins"] != 2 or output_metrics["animations"] != 0:
        raise ValueError(f"unexpected output structure: {output_metrics}")
    if output_metrics["maximum_exported_influences"] > 4:
        raise ValueError(f"too many exported skin influences: {output_metrics}")
    report = {
        "pipeline": "tools/ui007_build_hand_asset.py", "blender_version": bpy.app.version_string,
        "provenance": {
            "source": "Owner-generated via fal.ai; exact service model/parameters not recovered locally",
            "original_filename": args.source.name, "original_bytes": args.source.stat().st_size,
            "original_sha256": source_hash,
            "legal_terms_review": "separate review pending; no license claim invented",
            "cgtrader_data_included": False,
        },
        "source": {**source_metrics(args.source), "blender_imported_dimensions": imported_dimensions},
        "transformation": {
            "description": "Fal mesh import; Y reflection to dorsal +Y with winding/normal repair; wrist-origin meter normalization; new Piano Core armature; deterministic centerline/arc-length pianist-hand weights; explicit four-influence trim/renormalization; baked X-mirrored right mesh+armature with winding/normal repair; embedded material retained",
            "target_wrist_to_middle_tip_m": TARGET_WRIST_TO_MIDDLE_TIP_M,
            "uniform_scale": uniform_scale, "orientation": "wrist origin; fingers -Z; dorsal +Y; meters",
            "weight_stats": weight_stats,
            "deformation_check": deformation_check,
            "right_hand": "baked X-axis mesh+armature derivative; UVs/weights mirrored with geometry; winding/normals rebuilt; source/output have no tangent attribute because the retained material has no normal map; no runtime negative scale",
        },
        "rig": {
            "contract": "wrist/root plus thumb/index/middle/ring/pinky, three deforming joints each",
            "left_bones": bone_inventory(armature), "right_bones": bone_inventory(right_armature),
            "deforming_bones_per_hand": sum(1 for bone in armature.data.bones if bone.use_deform),
            "total_bones_per_hand": len(armature.data.bones),
        },
        "output": {"path": str(args.output), "bytes": args.output.stat().st_size,
                   "sha256": sha256(args.output), **output_metrics, "hands": ["LEFT", "RIGHT"]},
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("PIANO_UI007_ASSET_REPORT=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__": main()
