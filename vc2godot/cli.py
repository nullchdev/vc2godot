from __future__ import annotations
import argparse
from pathlib import Path
from .log import setup_logging
from .importer import scan, run


def main():
    ap = argparse.ArgumentParser(prog='vc2godot')
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('scan')
    s.add_argument('game', type=Path)
    i = sub.add_parser('import')
    i.add_argument('game', type=Path)
    i.add_argument('out', type=Path)
    i.add_argument('--chunk-size', type=int, default=512)
    i.add_argument('--max-models', type=int, default=0)
    i.add_argument('--no-collision', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'scan':
        scan(a.game)
        return
    setup_logging(a.out)
    run(a.game, a.out, a.chunk_size, a.max_models, not a.no_collision)
