from resolve_assist.export.cutlist import (
    build_cutlist,
    read_cutlist,
    sec_to_frame,
    write_cutlist,
)
from resolve_assist.types import Marker, MediaInfo, Segment


def make_info(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"")
    return MediaInfo(path=str(video), duration=10.0, fps=29.97)


def test_sec_to_frame():
    assert sec_to_frame(1.0, 30) == 30
    assert sec_to_frame(1.0, 29.97) == 30
    assert sec_to_frame(0.5, 24) == 12


def test_build_and_roundtrip(tmp_path):
    info = make_info(tmp_path)
    cuts = build_cutlist(
        info,
        [Segment(0.5, 2.0), Segment(3.0, 5.0)],
        markers=[Marker(sec=1.0, name="フィラー: えー", color="Red", duration_sec=0.4)],
        scene_cuts=[4.0],
        timeline_name="video_cut",
    )
    assert cuts["timeline_name"] == "video_cut"
    assert len(cuts["segments"]) == 2
    assert cuts["segments"][0]["start_frame"] == sec_to_frame(0.5, 29.97)
    assert cuts["markers"][0]["name"] == "フィラー: えー"
    assert cuts["scene_cuts"][0]["frame"] == sec_to_frame(4.0, 29.97)

    path = write_cutlist(cuts, tmp_path / "cuts.json")
    loaded = read_cutlist(path)
    assert loaded == cuts


def test_read_rejects_unknown_version(tmp_path):
    path = tmp_path / "cuts.json"
    path.write_text('{"version": 99}', encoding="utf-8")
    try:
        read_cutlist(path)
        raise AssertionError("ValueError が発生するはず")
    except ValueError:
        pass
