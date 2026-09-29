# VC2Godot 0.5.1 — full-map pass

This build is based on the supplied 0.5.0 that already had the working TXD decoder.
The 0.5.1 changes target the two remaining problems visible in the test run:

1. Linux case-sensitive discovery could miss `.IDE` / `.IPL` files from a Windows VC install.
2. The free camera moved, but `WorldStreamer` followed the stationary `Player`, so the runtime was permanently limited to the initial neighborhood.
3. Normal VC objects with large draw distances were not treated as LOD just because they were far-drawn; doing that can remove whole blocks of the city.
4. Loose `models/generic.txd`, `particle.txd`, collision files, and additional world IMG v1 archives are now searchable.
5. COL collision is enabled by default and attached per high-detail chunk.

## Import

Use exactly the command already used for this project:

```bash
vc2godot import "/home/nullch/.local/share/Steam/steamapps/common/Grand Theft Auto Vice City/" "$HOME/ViceCityGodot"
```

Run the generated project:

```bash
godot --path "$HOME/ViceCityGodot"
```

Controls: `WASD`, `Shift`, `Space`, mouse look, `Esc` capture/release, `F3` collision stats.

## Important runtime fix

The streamer tracks `Player/Head/Camera3D`, not the stationary player root. As the camera crosses a 512m chunk boundary the required high-detail and LOD chunks are replaced automatically. This means the complete imported map is reachable instead of only the first 3x3 neighborhood.

## Textures

The supplied 0.5.0 native VC TXD decoder is kept. Texture dictionaries are resolved from IMG archives and loose files; `generic.txd` and `particle.txd` are included as shared fallbacks.

## Collision

COL models are indexed from IMG archives and loose `.col` files. Embedded DFF collision is also attempted. Collision is written once per world chunk and instantiated as one `ConcavePolygonShape3D` for that chunk.
