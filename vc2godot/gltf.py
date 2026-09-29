from __future__ import annotations
from pathlib import Path
import os, struct, sys
import json
from array import array
from PIL import Image as PILImage


def _triples(values):
    if values is None or not values:
        return []
    if isinstance(values[0], (list, tuple)):
        return [tuple(v[:3]) for v in values]
    if len(values) % 3:
        raise ValueError(f"Expected XYZ flat array, got {len(values)} values")
    return [tuple(values[i:i + 3]) for i in range(0, len(values), 3)]


def _uvs(values):
    if values is None or not values:
        return []
    if isinstance(values[0], (list, tuple)):
        return [tuple(v[:2]) for v in values]
    if len(values) % 2:
        raise ValueError(f"Expected UV flat array, got {len(values)} values")
    return [tuple(values[i:i + 2]) for i in range(0, len(values), 2)]


def _indices(values):
    if values is None or not values:
        return []
    if isinstance(values[0], (list, tuple)):
        return [int(x) for tri in values for x in tri]
    return [int(x) for x in values]


def _matrix_transform_point(m, v):
    """Apply rwfury GenericMesh row-major 4x4 transform to a point."""
    if not m or len(m) < 16:
        return tuple(float(x) for x in v[:3])
    x, y, z = map(float, v[:3])
    return (
        m[0] * x + m[1] * y + m[2] * z + m[3],
        m[4] * x + m[5] * y + m[6] * z + m[7],
        m[8] * x + m[9] * y + m[10] * z + m[11],
    )


def _matrix_transform_normal(m, v):
    """Apply the linear part of a row-major 4x4 transform to a normal."""
    if not m or len(m) < 16:
        return tuple(float(x) for x in v[:3])
    x, y, z = map(float, v[:3])
    q = (
        m[0] * x + m[1] * y + m[2] * z,
        m[4] * x + m[5] * y + m[6] * z,
        m[8] * x + m[9] * y + m[10] * z,
    )
    l = (q[0]*q[0] + q[1]*q[1] + q[2]*q[2]) ** 0.5
    return (q[0]/l, q[1]/l, q[2]/l) if l else (0.0, 0.0, 1.0)


def _norm_tex_name(value):
    if not isinstance(value, str):
        return ""
    value = value.strip().replace("\\", "/")
    value = value.rsplit("/", 1)[-1]
    if ":" in value:
        value = value.rsplit(":", 1)[-1]
    if value.lower().endswith((".png", ".dds", ".txd")):
        value = Path(value).stem
    return value.casefold()


def _texture_name(mesh):
    for attr in ("texture_name", "material_name", "texture", "name"):
        value = getattr(mesh, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for attr in ("material", "materials"):
        value = getattr(mesh, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (list, tuple)) and value:
            item = value[0]
            for sub in ("texture_name", "name", "texture"):
                v = getattr(item, sub, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return ""


def mesh_records(meshes, texture_map=None):
    """Convert rwfury GenericMesh objects to lightweight Python records.

    No GLB is written here. This is intentional: the importer combines many
    GTA objects into a small number of chunk GLBs instead of creating thousands
    of files that Godot has to import independently.
    """
    texture_map = {_norm_tex_name(k): Path(v) for k, v in (texture_map or {}).items()}
    records = []
    for mi, m in enumerate(meshes):
        pos = _triples(getattr(m, "positions", None))
        if not pos:
            continue
        # rwfury's GenericMesh carries the RenderWare frame/atomic transform.
        # Ignoring it explodes multi-frame VC models (parts appear at the wrong
        # offsets/orientations). Apply it before the IPL instance transform.
        mesh_xform = getattr(m, "transform", None)
        if mesh_xform:
            pos = [_matrix_transform_point(mesh_xform, v) for v in pos]
        raw_normals = getattr(m, "normals", None)
        nor = _triples(raw_normals) if raw_normals else [(0.0, 1.0, 0.0)] * len(pos)
        if mesh_xform and raw_normals:
            nor = [_matrix_transform_normal(mesh_xform, v) for v in nor]
        if len(nor) != len(pos):
            nor = (nor + [(0.0, 1.0, 0.0)] * len(pos))[:len(pos)]
        tc = getattr(m, "texcoords", None)
        if tc and isinstance(tc[0], (list, tuple)) and tc[0] and isinstance(tc[0][0], (list, tuple)):
            tc = tc[0]
        uv = _uvs(tc) if tc else [(0.0, 0.0)] * len(pos)
        if len(uv) != len(pos):
            uv = (uv + [(0.0, 0.0)] * len(pos))[:len(pos)]
        inds = _indices(getattr(m, "indices", None))
        if not inds:
            continue
        tex_name = _texture_name(m)
        path = texture_map.get(_norm_tex_name(tex_name)) if tex_name else None
        records.append({"positions": pos, "normals": nor, "uv": uv, "indices": inds,
                        "texture": path, "texture_name": tex_name, "mesh_name": f"mesh_{mi}",
                        "diffuse_color": getattr(m, "diffuse_color", None)})
    return records


def _alpha_of(path):
    """(has_any_transparent_pixel)"""
    if not path:
        return False
    try:
        with PILImage.open(path) as im:
            if 'A' in im.getbands():
                return im.getchannel('A').getextrema()[0] < 255
    except Exception:
        pass
    return False


def export_records(records, out: Path):
    """Write ONE GLB for a whole chunk.

    All records that share a material are merged into a single primitive, so a
    chunk with 20 000 building pieces becomes a few hundred primitives instead
    of 20 000 nodes (this is what made Godot slow / RAM hungry).
    """
    out = Path(out)
    groups = {}
    for r in records:
        tex = r.get('texture')
        col = r.get('diffuse_color')
        col = tuple(round(float(c), 3) for c in col[:4]) if isinstance(col, (list, tuple)) and len(col) >= 3 else None
        groups.setdefault((str(tex).lower() if tex else '', col), []).append(r)

    blob = bytearray(); views = []; accessors = []
    images = []; textures = []; materials = []; meshes = []; nodes = []
    img_index = {}

    def add_view(data, target):
        blob.extend(b'\0' * ((4 - len(blob) % 4) % 4))
        off = len(blob); blob.extend(data)
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(data), "target": target})
        return len(views) - 1

    def add_acc(view, comp, typ, count, mn=None, mx=None):
        a = {"bufferView": view, "componentType": comp, "count": count, "type": typ}
        if mn is not None: a["min"] = mn
        if mx is not None: a["max"] = mx
        accessors.append(a); return len(accessors) - 1

    def tex_index(path):
        key = str(path).lower()
        if key in img_index:
            return img_index[key]
        try:
            rel = os.path.relpath(Path(path).resolve(), out.parent.resolve()).replace('\\', '/')
        except Exception:
            rel = str(path)
        images.append({"uri": rel, "name": Path(path).stem})
        textures.append({"sampler": 0, "source": len(images) - 1, "name": Path(path).stem})
        img_index[key] = len(textures) - 1
        return img_index[key]

    for (texkey, col), recs in groups.items():
        pos = array('f'); nor = array('f'); uvs = array('f'); idx = array('I')
        base = 0
        for r in recs:
            P = r['positions']; N = r['normals']; U = r['uv']
            for v in P: pos.extend(v)
            for v in N: nor.extend(v)
            for v in U: uvs.extend((v[0], v[1]))     # glTF has V-down like RenderWare -> no flip
            idx.extend([i + base for i in r['indices']])
            base += len(P)
        if not len(idx):
            continue
        if sys.byteorder != 'little':
            pos.byteswap(); nor.byteswap(); uvs.byteswap(); idx.byteswap()
        xs = pos[0::3]; ys = pos[1::3]; zs = pos[2::3]
        pmin = [min(xs), min(ys), min(zs)]; pmax = [max(xs), max(ys), max(zs)]
        pa = add_acc(add_view(pos.tobytes(), 34962), 5126, "VEC3", base, pmin, pmax)
        na = add_acc(add_view(nor.tobytes(), 34962), 5126, "VEC3", base)
        ua = add_acc(add_view(uvs.tobytes(), 34962), 5126, "VEC2", base)
        ia = add_acc(add_view(idx.tobytes(), 34963), 5125, "SCALAR", len(idx))

        pbr = {"baseColorFactor": [1.0, 1.0, 1.0, 1.0], "metallicFactor": 0.0, "roughnessFactor": 1.0}
        mat = {"name": recs[0].get('texture_name') or 'default', "pbrMetallicRoughness": pbr}
        tpath = recs[0].get('texture')
        if tpath:
            pbr["baseColorTexture"] = {"index": tex_index(tpath)}
            if _alpha_of(tpath):
                mat["alphaMode"] = "MASK"; mat["alphaCutoff"] = 0.5; mat["doubleSided"] = True
            # a textured material keeps white tint; RW material colour is only
            # meaningful for untextured surfaces (many VC materials are 255,255,255 anyway)
        elif col:
            vals = list(col[:4])
            if max(vals[:3]) > 1.0: vals[:3] = [x / 255.0 for x in vals[:3]]
            if len(vals) < 4: vals.append(1.0)
            elif vals[3] > 1.0: vals[3] /= 255.0
            pbr["baseColorFactor"] = vals
        materials.append(mat)
        meshes.append({"name": mat["name"], "primitives": [{
            "attributes": {"POSITION": pa, "NORMAL": na, "TEXCOORD_0": ua},
            "indices": ia, "material": len(materials) - 1}]})
        nodes.append({"mesh": len(meshes) - 1, "name": mat["name"]})

    doc = {"asset": {"version": "2.0", "generator": "VC2Godot 0.5"},
           "scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}],
           "nodes": nodes, "meshes": meshes, "materials": materials,
           "buffers": [{"byteLength": len(blob)}], "bufferViews": views, "accessors": accessors}
    if textures:
        doc["images"] = images; doc["textures"] = textures
        doc["samplers"] = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]
    js = json.dumps(doc, separators=(',', ':')).encode('utf-8')
    js += b' ' * ((4 - len(js) % 4) % 4)
    binb = bytes(blob) + b'\0' * ((4 - len(blob) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(binb)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'wb') as f:
        f.write(struct.pack('<III', 0x46546C67, 2, total))
        f.write(struct.pack('<II', len(js), 0x4E4F534A)); f.write(js)
        f.write(struct.pack('<II', len(binb), 0x004E4942)); f.write(binb)
    return out
