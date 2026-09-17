# Preset animations and PyMOL movies

Every preset can render a rocking or full-rotation camera animation. The ordinary
command remains a still render with preset 1 unless installation configuration
selects another preset.

```text
blender_render preset=neon_duplex_hero, animation=rock
blender_render preset=dried_paint_green, animation=360, gif=1, width=960, height=540
blender_render preset=preset_1_interface, animation=rock, duration=4, fps=24, rock_angle=15
```

The CLI accepts the same settings:

```sh
blender-render render public.cif --preset neon_duplex_hero --animation 360 \
  --width 960 --height 540 --gif -o spin.png
```

Rocking defaults to three seconds and ±12°. A 360° spin defaults to six seconds.
Both use 24 FPS and exclude a duplicate final frame, so looping is continuous.
`duration` × `fps` must be an integer from 4 to 360. Duration is at most 15 seconds,
FPS is 1–60, and decoded RGBA frames must fit within 1 GiB. Reduce resolution or
duration if needed. `rock_angle` applies only to rocking, with a maximum of 45°.
Animation and `raw_view` cannot be combined.

Animations inherit the ordinary preset, overrides, camera policy, and quality.
The camera orbits around the molecular center along its initial up axis. The
first frame preserves the resolved view and lens; the orbit does not zoom to
refit each frame. A tightly cropped initial view can clip during rotation; frame
it with sufficient margin before rendering. Rough mode changes performance
settings, not the motion. This is a rendered camera animation of one selected
molecular state, not a trajectory or editable molecular movie.

## Returned files and PyMOL

For `output_name=spin.png`, the files are `spin.png`, `spin.mp4`,
`spin.frames.zip`, `spin.manifest.json`, and optional `spin.gif` / `spin.blend`.
The frame archive contains the original lossless PNGs and FPS, dimensions,
ordering, and frame checksums. It is an intentional saved result, not a temporary
render cache. Keep it for later export. Server copies retain ordinary ownership,
expiry, acknowledgement, and recovery semantics.

After complete verified delivery, PyMOL replaces its existing movie timeline
with the returned frame sequence, sets the FPS, and displays frame one. Its
molecular objects and saved view are preserved. Use PyMOL movie playback controls
or `mplay` to play it. **Import replaces any existing PyMOL movie.** Use
`auto_preview=0` to download without modifying the movie.

For `blocking=0`, use the existing `blender_render_result` command to download
and present the completed animation. A presentation failure leaves the files
available and never submits another render. Synchronous animation calls return
an artifact result dictionary; still calls retain their PNG-path return value.

PyMOL's `mpng` can export the cached images. Native movie export support depends
on the installed PyMOL build and encoder; use the plugin command below for a
consistent export from the retained Blender frames. Do not clear the image cache
or request PyMOL ray/draw rerendering if you want to retain the Blender appearance.
Native export can clear the cache after writing; present the animation again
before a second native export. The plugin exporter always uses saved frames.

## GIF and later exports

`gif=1` requests an infinitely looping GIF along with the MP4. You can also export
later, without network access or GPU work:

```text
blender_movie_export presentation.gif
blender_movie_export presentation.mp4, source=spin.frames.zip
```

An omitted `source` uses the latest verified animation in this PyMOL instance.
After restarting PyMOL, provide the saved archive explicitly. The export command
writes the named file, replacing an existing file only after encoding succeeds.
MP4 uses H.264 and GIF uses a generated palette; transparent pixels are composited
onto white for those formats. Original PNG frames retain alpha. Odd MP4 dimensions
are padded by one pixel rather than cropped. GIF delays have centisecond precision.

## Runtime and scope

Local, Apptainer/HPC, and personal/private Modal use the same resolved JSON and
renderer entry point. The execution host needs FFmpeg with libx264 and GIF
encoding; GIF/MP4 re-export needs FFmpeg on the PyMOL computer. Pillow validates
frame images before import. New container recipes include FFmpeg. Older workers
must be upgraded; incompatible capabilities or missing encoders fail before GPU
work. Stop/drain a controller before upgrading it.

Shared hosted animation remains disabled. Private paid deployments explicitly
enable it as server policy while retaining lifetime budgets and upfront job
reservations. Animation is one FIFO job with one total execution deadline and
cancellation. No renderer or encoding retry silently submits another GPU job.
The compatibility command `python -m blender_render.rocking` delegates scene and
motion rendering to the shared renderer.

The acceptance record lists executed public-fixture checks and outstanding
platform gates. This feature branch does not deploy a service or constitute a
release approval.
