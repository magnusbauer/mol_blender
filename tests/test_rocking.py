import json
import math
import shutil
import subprocess

import pytest

from blender_render.rocking import angles, encode, spin_angles


def test_motion_is_periodic_and_visits_both_extremes():
    motion = angles(72, 12)
    assert motion[0] == 0
    assert motion[18] == pytest.approx(12)
    assert motion[54] == pytest.approx(-12)
    # Wrap step matches the initial step, rather than pausing on a duplicate.
    assert motion[0] - motion[-1] == pytest.approx(motion[1] - motion[0])
    assert sum(motion) == pytest.approx(0)


@pytest.mark.parametrize("frames, amplitude", [(3, 12), (72, 0), (72, 46), (72, math.nan)])
def test_invalid_motion_rejected(frames, amplitude):
    with pytest.raises(ValueError):
        angles(frames, amplitude)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="Needs FFmpeg")
@pytest.mark.parametrize("name", ["rocking", "spin"])
def test_real_gif_and_mp4_encoding(tmp_path, name):
    Image = pytest.importorskip("PIL.Image")
    frames = tmp_path / "frames"
    frames.mkdir()
    for index, color in enumerate(("red", "green", "blue", "yellow")):
        Image.new("RGB", (32, 24), color).save(frames / f"{index:04d}.png")
    encode(shutil.which("ffmpeg"), frames, tmp_path, 10, name)
    for filename in (f"{name}.mp4", f"{name}.gif"):
        result = subprocess.check_output(["ffprobe", "-v", "error", "-count_frames",
            "-show_streams", "-of", "json", str(tmp_path / filename)], text=True)
        stream = json.loads(result)["streams"][0]
        assert (stream["width"], stream["height"]) == (32, 24)
        assert int(stream["nb_read_frames"]) == 4
        assert float(stream["duration"]) == pytest.approx(.4)
    with Image.open(tmp_path / f"{name}.gif") as gif:
        assert gif.info["loop"] == 0


def test_spin_has_constant_speed_including_loop_boundary():
    motion = spin_angles(144)
    assert motion[0] == 0
    assert motion[36] == 90
    assert motion[72] == 180
    assert motion[108] == 270
    assert all(b - a == pytest.approx(2.5) for a, b in zip(motion, motion[1:]))
    assert 360 - motion[-1] == pytest.approx(2.5)
    with pytest.raises(ValueError):
        spin_angles(3)
