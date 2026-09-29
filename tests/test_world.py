import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import json, struct, logging
from pathlib import Path
from PIL import Image
from vc2godot.ipl import Placement
from vc2godot.ide import IdeObject
from vc2godot.gltf import mesh_records, export_records
from vc2godot.world import _transform_record, build_chunks, is_lod


class M:  # fake rwfury GenericMesh: a 2x1 wall in the XZ plane facing -Y (GTA space)
    positions = [(0, 0, 0), (2, 0, 0), (2, 0, 1), (0, 0, 1)]
    normals = [(0, -1, 0)] * 4
    texcoords = [(0, 0), (1, 0), (1, 1), (0, 1)]
    indices = [0, 1, 2, 0, 2, 3]
    texture_name = 'wall'
    diffuse_color = (255, 255, 255, 255)
    transform = None


def test_ipl_rotation_is_conjugated():
    # IPL quaternion for a 90deg turn is stored inverted; +90 stored as (0,0,-s,c)
    s = 0.70710678
    p = Placement(1, 0, 0, 0, 0, 0, -s, s, model='x')
    rec = _transform_record(mesh_records([M()])[0], p)
    # +90 deg about Z (GTA): (2,0,0) -> (0,2,0) -> Godot (0,0,-2)
    x, y, z = rec['positions'][1]
    assert abs(x) < 1e-5 and abs(z + 2) < 1e-5 and abs(y) < 1e-5


def test_lod_detection():
    assert is_lod('LODbuild1') and not is_lod('hotel1')


def test_glb_roundtrip(tmp_path):
    tex = tmp_path / 'assets/textures/generic'; tex.mkdir(parents=True)
    Image.new('RGBA', (4, 4), (255, 0, 0, 255)).save(tex / 'wall.png')
    rec = mesh_records([M()], {'wall': tex / 'wall.png'})
    recs = [_transform_record(rec[0], Placement(1, 10 * i, 0, 0, 0, 0, 0, 1, model='x')) for i in range(50)]
    glb = tmp_path / 'world/chunks/c.glb'
    export_records(recs, glb)
    b = glb.read_bytes()
    magic, ver, total = struct.unpack_from('<III', b, 0)
    assert magic == 0x46546C67 and ver == 2 and total == len(b)
    jl, jt = struct.unpack_from('<II', b, 12)
    doc = json.loads(b[20:20 + jl])
    assert len(doc['meshes']) == 1                       # 50 wall instances merged into 1 primitive
    assert doc['accessors'][0]['count'] == 200
    assert doc['images'][0]['uri'] == '../../assets/textures/generic/wall.png'
    bl, bt = struct.unpack_from('<II', b, 20 + jl)
    assert bl >= doc['buffers'][0]['byteLength']


class FakeConv:
    def meshes(self, m, t): return [M()], {}
    def report(self): pass


def test_build_chunks_skips_lod_and_interiors(tmp_path):
    ides = {1: IdeObject(1, 'hotel1', 'generic'), 2: IdeObject(2, 'lodhotel', 'generic')}
    pl = [Placement(1, 5, 5, 0, 0, 0, 0, 1, model='hotel1'),
          Placement(2, 5, 5, 0, 0, 0, 0, 1, model='lodhotel'),
          Placement(1, 9, 9, 0, 0, 0, 0, 1, interior=3, model='hotel1')]
    res, c = build_chunks(tmp_path, pl, ides, FakeConv(), 512, logging.getLogger('t'))
    assert sum(v['objects'] for v in res.values()) == 1
