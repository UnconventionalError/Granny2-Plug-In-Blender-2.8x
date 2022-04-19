# <pep8 compliant>

"""
This script exports Star Wars: The Old Republic models from Blender.

Usage:
Run this script from "File->Export" menu and then save the desired GR2 model file.

https://github.com/SWTOR-Slicers/WikiPedia/wiki/GR2-File-Structure
"""

from typing import List, Union

import bpy
from bpy.types import Context, Mesh, Object, Operator
from bpy_extras.wm_utils.progress_report import ProgressReport
from mathutils import Vector, Matrix

from ..types.gr2 import Granny2
from ..utils.binary import ArrayBuffer, DataView
from ..utils.number import encodeHalfFloat


def parse(ob, mesh, has_clo=False):
    # type: (Object, Mesh, bool) -> Granny2
    gr2 = Granny2()

    # Meshes
    gr2.mesh_buffer = {0: Granny2.Mesh(ob.name)}
    gmesh = gr2.mesh_buffer[0]

    # Parse materials / sub-meshes
    gr2.material_names = {}
    gmesh.piece_header_buffer = {}

    for i, material in enumerate(mesh.materials):
        polygons = [polygon for polygon in mesh.polygons if polygon.material_index == i]

        piece = Granny2.Piece()
        piece.material_index = piece.index = i
        piece.num_polygons = len(polygons)
        piece.offset_indices = gmesh.piece_header_buffer[i - 1].num_polygons if i != 0 else 0

        piece.bounds = Granny2.BoundingBox(
            (
                min([co[0] for co in ob.bound_box]),
                min([co[1] for co in ob.bound_box]),
                min([co[2] for co in ob.bound_box]),
                1.0,
                max([co[0] for co in ob.bound_box]),
                max([co[1] for co in ob.bound_box]),
                max([co[2] for co in ob.bound_box]),
                1.0,
            )
        )

        gr2.material_names[i] = material.name
        gmesh.piece_header_buffer[i] = piece

    # Parse bone names
    gmesh.bone_names = {i: name for i, name in enumerate(ob.vertex_groups.keys())}

    # Parse mesh vertices
    gmesh.vertex_buffer = {}

    for i in range(len(mesh.loops)):
        if gmesh.vertex_buffer.get(mesh.loops[i].vertex_index, False):
            continue

        loop = mesh.loops[i]
        vert = mesh.vertices[loop.vertex_index]

        pos = vert.co
        nor = loop.normal
        tan = loop.tangent
        bit = loop.bitangent_sign
        tex = mesh.uv_layers.active.data[i].uv

        vertex = Granny2.Vertex(pos)

        if gmesh.bone_names:
            groups = sorted([(g.group, g.weight) for g in vert.groups],
                            key=lambda xy: (xy[1], xy[0]),
                            reverse=True)

            if len(groups) > 4:
                groups = groups[:4]
            else:
                for _ in range(4 - len(groups)):
                    groups.append((groups[0][0] if groups else 0.0, 0.0))

            vertex.bone_indices = Vector([groups[j][0] for j in range(4)])
            vertex.bone_weights = Vector([groups[j][1] for j in range(4)])

        vertex.normals = Vector(nor[:3] + (1.0,))
        vertex.tangents = Vector(tan[:3] + (bit,))
        vertex.uv_layer0 = Vector(tex[:2])

        gmesh.vertex_buffer[vert.index] = vertex

    # Parse mesh indices
    gmesh.indices_buffer = {poly.index: tuple(poly.vertices) for poly in mesh.polygons}

    # Type Flag
    gr2.type_flag = 1 if has_clo else 0

    # Calculate Bounds
    gr2.bounds = Granny2.BoundingBox(
        (
            min([co[0] for co in ob.bound_box]),
            min([co[1] for co in ob.bound_box]),
            min([co[2] for co in ob.bound_box]),
            1.0,
            max([co[0] for co in ob.bound_box]),
            max([co[1] for co in ob.bound_box]),
            max([co[2] for co in ob.bound_box]),
            1.0,
        )
    )

    # Calculate Offsets
    gr2.calculate_offsets()

    return gr2


def write(gr2, path):
    # type: (Granny2, str) -> None
    buffer = ArrayBuffer(gr2.num_bytes)
    dv = DataView(buffer)
    pos = 0

    # MAGIC bytes
    dv.setUint32(pos, gr2.magic_bytes, 1)
    pos = 4
    # Version major
    dv.setUint32(pos, gr2.version_major, 1)
    pos += 4
    # Version minor
    dv.setUint32(pos, gr2.version_minor, 1)
    pos += 4
    # Offset BNRY/LTLE
    dv.setUint32(pos, gr2.offset_BNRY, 1)
    pos += 4

    # Number of cached offsets
    dv.setUint32(pos, gr2.num_cached_offsets, 1)
    pos += 4
    # Type flag
    dv.setUint32(pos, gr2.type_flag, 1)
    pos += 4
    # Number of meshes
    dv.setUint16(pos, gr2.num_meshes, 1)
    pos += 2
    # Number of materials
    dv.setUint16(pos, gr2.num_materials, 1)
    pos += 2
    # Number of skeleton bones (only applies to skeleton/armature gr2s)
    dv.setUint16(pos, gr2.num_skeleton_bones, 1)
    pos += 2
    # Number of attachments
    # TODO: Figure out how to hanle attachments, 0 for now.
    dv.setUint16(pos, gr2.num_attachments, 1)
    pos += 2

    # 16 zero bytes
    dv.setBigUint64(pos, 0, 1)
    pos += 8
    dv.setBigUint64(pos, 0, 1)
    pos += 8

    # Global bounding box
    for co in gr2.bounds:
        dv.setFloat32(pos, co, 1)
        pos += 4

    # Offset of the cached offsets
    dv.setUint32(pos, gr2.offset_cached_offsets, 1)
    pos += 4
    # Offset of mesh header
    dv.setUint32(pos, gr2.offset_mesh_headers, 1)
    pos += 4
    # Offset of the material name offsets
    dv.setUint32(pos, gr2.offset_material_name_offsets, 1)
    pos += 4
    # 4 zero bytes
    dv.setUint32(pos, 0, 1)
    pos += 4

    # Offset of attachments
    # TODO: Figure out how to handle attachments, 0 for now.
    dv.setUint32(pos, 0, 1)
    pos += 4
    # Zero padding
    while (pos % 16) != 0:
        dv.setUint8(pos, 0)
        pos += 1

    for i, mesh in gr2.mesh_buffer.items():
        # Offset mesh name
        dv.setUint32(pos, mesh.offset_mesh_name, 1)
        pos += 4
        # BitFlag1
        dv.setUint32(pos, mesh.bit_flag1, 1)
        pos += 4
        # Number of sub meshes
        dv.setUint16(pos, mesh.num_pieces, 1)
        pos += 2
        # Number of bones
        dv.setUint16(pos, mesh.num_used_bones, 1)
        pos += 2
        # BitFlag2
        dv.setUint16(pos, mesh.bit_flag2, 1)
        pos += 2
        # Vertex size
        dv.setUint16(pos, mesh.vertex_size, 1)
        pos += 2

        # Number of vertices
        dv.setUint32(pos, mesh.num_vertices, 1)
        pos += 4
        # Number of indices
        dv.setUint32(pos, int(mesh.num_polygons * 3), 1)
        pos += 4
        # Offset vertices buffer
        dv.setUint32(pos, mesh.offset_vertex_buffer, 1)
        pos += 4
        # Offset sub meshes headers
        dv.setUint32(pos, mesh.offset_piece_headers, 1)
        pos += 4

        # Offset indices buffer
        dv.setUint32(pos, mesh.offset_indices_buffer, 1)
        pos += 4
        # Offset bones buffer
        dv.setUint32(pos, mesh.offset_bones_buffer, 1)
        pos += 4

    # Zero padding
    while (pos % 16) != 0:
        dv.setUint8(pos, 0)
        pos += 1

    # Sub mesh headers
    for mesh in gr2.mesh_buffer.values():
        for piece in mesh.piece_header_buffer.values():
            # Offset of sub polygons within indices buffer
            dv.setUint32(pos, piece.offset_indices, 1)
            pos += 4
            # Number of polygons used by sub
            dv.setUint32(pos, piece.num_polygons, 1)
            pos += 4
            # Material id
            dv.setUint32(pos, piece.material_index, 1)
            pos += 4
            # Sub mesh id
            dv.setUint32(pos, piece.index, 1)
            pos += 4

            # Bounding Box
            for co in piece.bounds:
                dv.setFloat32(pos, co, 1)
                pos += 4

    # Material name offsets
    offset = 0
    for i, mesh in gr2.mesh_buffer.items():
        offset = mesh.offset_mesh_name if i == 0 else offset
        offset += len(mesh.name) + 1
    for material_name in gr2.material_names.values():
        dv.setUint32(pos, offset, 1)
        offset += len(material_name) + 1
        pos += 4

    # Zero padding
    while (pos % 16) != 0:
        dv.setUint8(pos, 0)
        pos += 1

    # Attachments
    # TODO: Figure out how to handle attachments, skip for now.

    # Vertices buffer
    for mesh in gr2.mesh_buffer.values():
        for vertex in mesh.vertex_buffer.values():
            dv.setFloat32(pos, vertex.position.x, 1)
            pos += 4
            dv.setFloat32(pos, vertex.position.y, 1)
            pos += 4
            dv.setFloat32(pos, vertex.position.z, 1)
            pos += 4

            if vertex.bone_indices and vertex.bone_weights:
                for co in vertex.bone_weights:
                    dv.setUint8(pos, int(co * 255))
                    pos += 1

                for co in vertex.bone_indices:
                    dv.setUint8(pos, int(co))
                    pos += 1

            for co in vertex.normals:
                dv.setUint8(pos, int((co * 127.5) + 128))
                pos += 1

            for co in vertex.tangents:
                dv.setUint8(pos, int((co * 127.5) + 128))
                pos += 1

            dv.setUint16(pos, encodeHalfFloat(vertex.uv_layer0.x), 1)
            pos += 2
            dv.setUint16(pos, encodeHalfFloat(1 - vertex.uv_layer0.y), 1)
            pos += 2

    # Zero padding
    while (pos % 16) != 0:
        dv.setUint32(pos, 0)
        pos += 1

    # Indices buffer
    for mesh in gr2.mesh_buffer.values():
        for polygon in mesh.indices_buffer.values():
            for vertex_index in polygon:
                dv.setUint16(pos, int(vertex_index), 1)
                pos += 2

    # Zero pading
    while (pos % 16) != 0:
        dv.setUint8(pos, 0)
        pos += 1

    # Bones buffer
    for mesh in gr2.mesh_buffer.values():
        def bone_bounds(bone, axis):
            # type: (int, int) -> List[float]
            result = [v.position[axis] for v in mesh.vertex_buffer.values() if bone in v.bone_indices]
            return result if result else [0]

        if mesh.bone_names:
            for i, bone_name in mesh.bone_names.items():
                dv.setUint32(pos, offset, 1)
                offset += len(bone_name) + 1
                pos += 4
                dv.setFloat32(pos, min(bone_bounds(i, 0)), 1)
                pos += 4
                dv.setFloat32(pos, min(bone_bounds(i, 1)), 1)
                pos += 4
                dv.setFloat32(pos, min(bone_bounds(i, 2)), 1)
                pos += 4
                dv.setFloat32(pos, max(bone_bounds(i, 0)), 1)
                pos += 4
                dv.setFloat32(pos, max(bone_bounds(i, 1)), 1)
                pos += 4
                dv.setFloat32(pos, max(bone_bounds(i, 2)), 1)
                pos += 4
        else:
            dv.setUint32(pos, mesh.offset_mesh_name, 1)
            pos += 4
            dv.setFloat32(pos, gr2.bounds.minimum.x, 1)
            pos += 4
            dv.setFloat32(pos, gr2.bounds.minimum.y, 1)
            pos += 4
            dv.setFLoat32(pos, gr2.bounds.minimum.z, 1)
            pos += 4
            dv.setFloat32(pos, gr2.bounds.maximum.x, 1)
            pos += 4
            dv.setFloat32(pos, gr2.bounds.maximum.y, 1)
            pos += 4
            dv.setFloat32(pos, gr2.bounds.maximum.z, 1)
            pos += 4

    # Zero padding
    while (pos % 16) != 0:
        dv.setUint8(pos, 0)
        pos += 1

    # Strings
    for mesh in gr2.mesh_buffer.values():
        for ch in mesh.name:
            dv.setUint8(pos, ord(ch))
            pos += 1
        dv.setUint8(pos, 0)
        pos += 1

    offset_material_names = pos
    for material_name in gr2.material_names.values():
        for ch in material_name:
            dv.setUint8(pos, ord(ch))
            pos += 1
        dv.setUint8(pos, 0)
        pos += 1

    offset_bone_names = pos
    for mesh in gr2.mesh_buffer.values():
        if mesh.bone_names:
            for bone_name in mesh.bone_names.values():
                for ch in bone_name:
                    dv.setUint8(pos, ord(ch))
                    pos += 1
                dv.setUint8(pos, 0)
                pos += 1

    # Zero padding
    while (pos % 16) != 0:
        dv.setUint8(pos, 0)
        pos += 1

    # Cached offsets
    dv.setUint32(pos, 80, 1)      # 0x50
    pos += 4
    dv.setUint32(pos, pos - 4, 1)
    pos += 4
    dv.setUint32(pos, 84, 1)      # 0x54
    pos += 4
    dv.setUint32(pos, 112, 1)     # 0x70
    pos += 4
    dv.setUint32(pos, 88, 1)      # 0x58
    pos += 4
    dv.setUint32(pos, offset_material_names, 1)
    pos += 4

    for i, mesh in gr2.mesh_buffer.items():
        dv.setUint32(pos, 112 + (i * 40), 1)     # 0x70
        pos += 4
        dv.setUint32(pos, mesh.offset_mesh_name, 1)
        pos += 4
        dv.setUint32(pos, 136 + (i * 40), 1)     # 0x88
        pos += 4
        dv.setUint32(pos, mesh.offset_vertex_buffer, 1)
        pos += 4
        dv.setUint32(pos, 140 + (i * 40), 1)     # 0x8C
        pos += 4
        dv.setUint32(pos, mesh.offset_piece_headers, 1)
        pos += 4
        dv.setUint32(pos, 144 + (i * 40), 1)     # 0x90
        pos += 4
        dv.setUint32(pos, mesh.offset_indices_buffer, 1)
        pos += 4
        dv.setUint32(pos, 148 + (i * 40), 1)     # 0x94
        pos += 4
        dv.setUint32(pos, mesh.offset_bones_buffer, 1)
        pos += 4

    offset = offset_material_names
    for i, material_name in gr2.material_names.items():
        # Offset material name offset
        dv.setUint32(pos, gr2.offset_material_name_offsets + (4 * i), 1)
        pos += 4

        # Offset material name
        if i == 0:
            dv.setUint32(pos, offset_material_names, 1)
            pos += 4
        else:
            dv.setUint32(pos, offset, 1)
            offset += len(material_name) + 1
            pos += 4

    for mesh in gr2.mesh_buffer.values():
        for i, bone_name in mesh.bone_names.items():
            # Offset bone name offset
            dv.setUint32(pos, mesh.offset_bones_buffer + (28 * i), 1)
            pos += 4

            # Offset bone name
            if i == 0:
                dv.setUint32(pos, offset_bone_names, 1)
                pos += 4
            else:
                dv.setUint32(pos, offset, 1)
                offset += len(bone_name) + 1
                pos += 4

    # Zero padding
    while (pos % 16) != 0:
        dv.setUint8(pos, 0)
        pos += 1

    # BNRY/LTLE
    # TODO:
    dv.setBigUint64(pos, 0, 1)
    pos += 8
    dv.setBigUint64(pos, 0, 1)
    pos += 8

    # Bounding box
    dv.setFloat32(pos, gr2.bounds.min_x, 1)
    pos += 4
    dv.setFloat32(pos, gr2.bounds.min_y, 1)
    pos += 4
    dv.setFloat32(pos, gr2.bounds.min_z, 1)
    pos += 4
    dv.setFloat32(pos, gr2.bounds.max_x, 1)
    pos += 4
    dv.setFloat32(pos, gr2.bounds.max_y, 1)
    pos += 4
    dv.setFloat32(pos, gr2.bounds.max_z, 1)
    pos += 4
    dv.setUint32(pos, 0, 1)
    pos += 4
    for ch in "EGCD":
        dv.setUint8(pos, ord(ch))
        pos += 1
    dv.setUint32(pos, 5, 1)
    pos += 4
    dv.setUint32(pos, gr2.offset_BNRY, 1)
    pos += 4

    with open(f"{path}{gr2.mesh_buffer[0].name}.gr2", 'wb') as file:
        dv.buffer.tofile(file)


def save(operator, context, path, ob, global_matrix=None):
    # type: (Operator, Context, str, Object, Union[Matrix, None]) -> bool
    import os

    fullpath = os.path.join(path, f"{ob.name.replace(' ', '_')}.gr2")

    with ProgressReport(context.window_manager) as progress:
        progress.enter_substeps(3, f"Exporting \'{fullpath}\' ...")

        if bpy.ops.object.mode_set.poll():
            # Enter edit mode.
            bpy.ops.object.mode_set(mode='EDIT')

            # Sort vertices, faces and edges by material index.
            bpy.ops.mesh.sort_elements(type='MATERIAL')

            # Exit edit mode.
            bpy.ops.object.mode_set(mode='OBJECT')

        try:
            bmesh = ob.to_mesh()
        except RuntimeError:
            return False

        if global_matrix:
            bmesh.transform(ob.matrix_world @ global_matrix)
        else:
            bmesh.transform(ob.matrix_world)

        # If negative scaling we have to invert the normals...
        if ob.matrix_world.determinant() < 0.0:
            bmesh.flip_normals()

        # Make sure there is something to write
        if not len(bmesh.polygons) + len(bmesh.vertices):
            ob.to_mesh_clear()  # Clean-up

        # Calculate normals and tangents
        if bmesh.polygons:
            bmesh.calc_normals_split()
            bmesh.calc_tangents()

        progress.step(f"Parsing Blender Object: \'{ob.name}\' ...", 1)
        mesh = parse(ob, bmesh, has_clo=operator.has_clo)

        if mesh:
            progress.step("Done, writing file ...", 2)
            write(mesh, path + os.sep)
            progress.leave_substeps(f"Done, finished exporting: \'{fullpath}\'")

            return True
        else:
            return False
