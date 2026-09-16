# Offline rocking animation

The trusted developer utility builds a scene with the shared renderer, then
orbits its camera around the molecular bounds along the initial camera-up axis.
Geometry, materials, lighting, camera projection and lens stay fixed. A sinusoidal
orbit returns smoothly through the original view without a duplicate endpoint.
This utility does not add an animation endpoint to the hosted service.

Export a public structure with PyMOL first (the export assigns secondary structure
and preserves the scene's camera, colors and transforms):

```sh
curl -fL https://files.rcsb.org/download/7PZB.cif -o 7pzb.cif
PYMOL_PYTHON -m blender_render.export 7pzb.cif bundle.json
python -m blender_render.rocking --bundle bundle.json --output rocking-output \
  --image blender-render.sif
```

Replace `PYMOL_PYTHON` with your PyMOL Python executable. Use the pinned project
Apptainer image, or replace `--image` with `--blender` pointing to Blender with the
project's Molecular Nodes runtime available. FFmpeg must be installed on the host
with the libx264 encoder.

Defaults: `neon_duplex_hero`, 960 × 540, 64 samples, 72 frames at 24 FPS,
±12 degrees (three seconds). `--preset`, `--width`, `--height`, `--samples`,
`--frames`, `--fps` and `--amplitude` customize the render. The preset's authored
camera is used; the first frame is the ordinary still render. GIF frame delays
have centisecond precision, so its timing approximates the MP4 frame rate.

The new output directory receives `rocking.gif` (infinite loop), `rocking.mp4`
(H.264, browser-compatible pixel format), `render.png`, `resolved-style.json`,
`manifest.json` and `render.log`. MP4 repetition is controlled by the player.
The manifest records motion, runtime, input/spec/source hashes and output hashes.
Intermediate CIF, view and frames are cleaned on success or failure. Existing
output directories are rejected. A failed render leaves diagnostics, not a
completed animation manifest. Generated outputs belong outside version control.
