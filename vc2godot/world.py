from __future__ import annotations
from pathlib import Path
import copy, json, math, struct
from .coords import vc_vec3
from .gltf import mesh_records, export_records


def _quat_matrix(x, y, z, w):
    n = x*x + y*y + z*z + w*w
    if n < 1e-12:
        return ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))
    s = 2.0 / n
    xx, yy, zz = x*x*s, y*y*s, z*z*s
    xy, xz, yz = x*y*s, x*z*s, y*z*s
    wx, wy, wz = w*x*s, w*y*s, w*z*s
    return ((1-yy-zz, xy-wz, xz+wy), (xy+wz, 1-xx-zz, yz-wx), (xz-wy, yz+wx, 1-xx-yy))


def placement_matrix(p):
    # Keep the quaternion convention from the working 0.5.0 importer.
    return _quat_matrix(-p.qx, -p.qy, -p.qz, p.qw)


def _transform_record(rec, p):
    r = placement_matrix(p)
    sx, sy, sz = p.sx or 1.0, p.sy or 1.0, p.sz or 1.0
    pos = []
    for v in rec['positions']:
        x, y, z = v[0]*sx, v[1]*sy, v[2]*sz
        gx = r[0][0]*x + r[0][1]*y + r[0][2]*z + p.x
        gy = r[1][0]*x + r[1][1]*y + r[1][2]*z + p.y
        gz = r[2][0]*x + r[2][1]*y + r[2][2]*z + p.z
        pos.append((gx, gz, -gy))
    nor = []
    for v in rec['normals']:
        x, y, z = v[0]/sx, v[1]/sy, v[2]/sz
        gx = r[0][0]*x + r[0][1]*y + r[0][2]*z
        gy = r[1][0]*x + r[1][1]*y + r[1][2]*z
        gz = r[2][0]*x + r[2][1]*y + r[2][2]*z
        l = (gx*gx + gy*gy + gz*gz) ** 0.5 or 1.0
        nor.append((gx/l, gz/l, -gy/l))
    out = dict(rec)
    out['positions'] = pos
    out['normals'] = nor
    if sx * sy * sz < 0:
        ind = list(rec['indices'])
        for i in range(0, len(ind) - 2, 3):
            ind[i+1], ind[i+2] = ind[i+2], ind[i+1]
        out['indices'] = ind
    return out


def _box_triangles(bmin, bmax):
    x0, y0, z0 = bmin; x1, y1, z1 = bmax
    v = [(x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),(x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)]
    tri = [0,1,2,0,2,3,4,6,5,4,7,6,0,4,5,0,5,1,1,5,6,1,6,2,2,6,7,2,7,3,4,0,3,4,3,7]
    return [v[i] for i in tri]


def _sphere_triangles(center, radius, segments=12, rings=6):
    cx, cy, cz = center; verts = []
    for r in range(rings + 1):
        phi = math.pi * r / rings; sp = math.sin(phi); cp = math.cos(phi)
        for s in range(segments):
            th = 2 * math.pi * s / segments
            verts.append((cx + radius*sp*math.cos(th), cy + radius*cp, cz + radius*sp*math.sin(th)))
    tri = []
    for r in range(rings):
        for s in range(segments):
            a = r*segments+s; b = r*segments+(s+1)%segments; c = (r+1)*segments+(s+1)%segments; d = (r+1)*segments+s
            tri.extend((verts[a], verts[b], verts[c], verts[a], verts[c], verts[d]))
    return tri


def _face_indices(face):
    for names in (('a','b','c'), ('i1','i2','i3'), ('v1','v2','v3')):
        if all(hasattr(face, n) for n in names):
            return tuple(int(getattr(face, n)) for n in names)
    vals = getattr(face, 'indices', None)
    if isinstance(vals, (tuple, list)) and len(vals) >= 3:
        return int(vals[0]), int(vals[1]), int(vals[2])
    return None


def _transform_point(v, p):
    r = placement_matrix(p)
    sx, sy, sz = p.sx or 1.0, p.sy or 1.0, p.sz or 1.0
    x, y, z = v[0] * sx, v[1] * sy, v[2] * sz
    gx = r[0][0]*x + r[0][1]*y + r[0][2]*z + p.x
    gy = r[1][0]*x + r[1][1]*y + r[1][2]*z + p.y
    gz = r[2][0]*x + r[2][1]*y + r[2][2]*z + p.z
    return (gx, gz, -gy)


def collision_triangles(model, p):
    if model is None:
        return []
    tris = []
    verts = getattr(model, 'vertices', []) or []
    for f in getattr(model, 'faces', []) or []:
        ids = _face_indices(f)
        if ids and all(0 <= i < len(verts) for i in ids):
            tris.extend((verts[ids[0]], verts[ids[1]], verts[ids[2]]))
    for b in getattr(model, 'boxes', []) or []:
        try:
            tris.extend(_box_triangles(b.min, b.max))
        except AttributeError:
            pass
    for s in getattr(model, 'spheres', []) or []:
        try:
            tris.extend(_sphere_triangles(s.center, s.radius))
        except AttributeError:
            pass
    return [_transform_point(v, p) for v in tris]


def _write_collision_bin(path, faces):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(struct.pack('<4sI', b'VCOL', len(faces)))
        for x, y, z in faces:
            f.write(struct.pack('<3f', x, y, z))


def _model_kind(p, obj, lod_ids):
    # Do NOT use draw distance as a proxy for LOD. Ordinary VC buildings often
    # have draw distances >300m; treating those as LOD deletes large parts of the city.
    if obj.model.lower().startswith('lod'):
        return 'lod'
    if (p.model or '').lower().startswith('lod'):
        return 'lod'
    if p.object_id in lod_ids:
        return 'lod'
    return 'high'


def build_chunks(out, placements, ides, converter, chunk_size, log, export_collision=True):
    stats = {'lod': 0, 'interior': 0, 'no_ide': 0, 'no_dff': 0, 'collision_models': 0}
    lod_ids = {p.lod for p in placements if p.lod >= 0}
    chunks = {}
    for p in placements:
        o = ides.get(p.object_id)
        if not o:
            stats['no_ide'] += 1
            continue
        if p.interior != 0:
            stats['interior'] += 1
            continue
        kind = _model_kind(p, o, lod_ids)
        if kind == 'lod':
            stats['lod'] += 1
        x, _, z = vc_vec3((p.x, p.y, p.z))
        chunks.setdefault((math.floor(x / chunk_size), math.floor(z / chunk_size)), {'high': [], 'lod': []})[kind].append(p)

    log.info('placements skipped: %s', stats)
    chunk_dir = out / 'world/chunks'; collision_dir = out / 'world/collision'
    chunk_dir.mkdir(parents=True, exist_ok=True)
    if export_collision:
        collision_dir.mkdir(parents=True, exist_ok=True)

    result = {}
    minx = minz = 10**9; maxx = maxz = -10**9
    totals = {'high': 0, 'lod': 0, 'collision_faces': 0}

    for n, ((cx, cz), sets) in enumerate(sorted(chunks.items()), 1):
        high_records, lod_records, collision_faces = [], [], []
        for kind in ('high', 'lod'):
            items = sets[kind]
            if not items:
                continue
            records = high_records if kind == 'high' else lod_records
            unique = {}
            for p in items:
                o = ides.get(p.object_id)
                if o:
                    unique.setdefault((o.model.casefold(), o.texture.casefold()), o)
            for o in unique.values():
                data = converter.meshes(o.model, o.texture)
                if not data:
                    stats['no_dff'] += 1
                    continue
                meshes, texmap = data
                base = mesh_records(meshes, texmap)
                for p in items:
                    po = ides.get(p.object_id)
                    if not po or po.model.casefold() != o.model.casefold() or po.texture.casefold() != o.texture.casefold():
                        continue
                    for rec in base:
                        records.append(_transform_record(copy.deepcopy(rec), p))
                    if kind == 'high' and export_collision:
                        cm = converter.collision(o.model)
                        if cm is not None:
                            stats['collision_models'] += 1
                            collision_faces.extend(collision_triangles(cm, p))

        paths = {}
        if high_records:
            gp = chunk_dir / f'chunk_{cx}_{cz}.glb'; export_records(high_records, gp); paths['high'] = gp.relative_to(out).as_posix()
        if lod_records:
            gp = chunk_dir / f'chunk_{cx}_{cz}_lod.glb'; export_records(lod_records, gp); paths['lod'] = gp.relative_to(out).as_posix()
        if export_collision and collision_faces:
            cp = collision_dir / f'chunk_{cx}_{cz}.bin'; _write_collision_bin(cp, collision_faces); paths['collision'] = cp.relative_to(out).as_posix()

        if paths:
            result[f'{cx},{cz}'] = {
                'objects': len(sets['high']) + len(sets['lod']),
                'high_objects': len(sets['high']), 'lod_objects': len(sets['lod']),
                'high_meshes': len(high_records), 'lod_meshes': len(lod_records),
                'collision_faces': len(collision_faces), 'paths': paths,
            }
            minx, minz = min(minx, cx), min(minz, cz); maxx, maxz = max(maxx, cx), max(maxz, cz)
        totals['high'] += len(high_records); totals['lod'] += len(lod_records); totals['collision_faces'] += len(collision_faces)
        log.info('CHUNK [%d/%d] %d,%d highObj=%d lodObj=%d highMeshes=%d lodMeshes=%d collisionTris=%d', n, len(chunks), cx, cz, len(sets['high']), len(sets['lod']), len(high_records), len(lod_records), len(collision_faces)//3)

    center = {'x': ((minx + maxx + 1) * chunk_size / 2 if result else 0.0), 'z': ((minz + maxz + 1) * chunk_size / 2 if result else 0.0)}
    meta = {'chunk_size': chunk_size, 'center': center, 'chunk_count': len(result), 'high_meshes': totals['high'], 'lod_meshes': totals['lod'], 'collision_triangles': totals['collision_faces']//3, 'stats': stats}
    (out/'data/chunks.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    (out/'data/world_meta.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    converter.report()
    return result, center
