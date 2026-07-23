from xml.etree import ElementTree as ET

from resolve_assist.export.xmeml import build_xmeml, rate_for_fps
from resolve_assist.types import Marker, MediaInfo, Segment


def make_info(tmp_path, fps=30.0, has_audio=True):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    return MediaInfo(
        path=str(video), duration=10.0, fps=fps,
        width=1920, height=1080, has_audio=has_audio,
    )


def parse(xml_str):
    body = xml_str.split("<!DOCTYPE xmeml>")[1]
    return ET.fromstring(body)


def test_rate_for_fps():
    assert rate_for_fps(29.97) == (30, True)
    assert rate_for_fps(23.976) == (24, True)
    assert rate_for_fps(30.0) == (30, False)
    assert rate_for_fps(25.0) == (25, False)


def test_build_xmeml_structure(tmp_path):
    info = make_info(tmp_path)
    xml = build_xmeml(
        info,
        [Segment(1.0, 2.0), Segment(4.0, 6.0)],
        markers=[Marker(sec=4.5, name="フィラー: えー", note="カット候補")],
    )
    root = parse(xml)
    seq = root.find("sequence")
    assert seq.findtext("name") == "video_cut"
    assert seq.findtext("duration") == "90"  # 30f + 60f
    assert seq.findtext("rate/timebase") == "30"
    assert seq.findtext("rate/ntsc") == "FALSE"

    vclips = seq.findall("media/video/track/clipitem")
    aclips = seq.findall("media/audio/track/clipitem")
    assert len(vclips) == 2
    assert len(aclips) == 2

    # 1本目: src 30-60 → rec 0-30
    assert vclips[0].findtext("in") == "30"
    assert vclips[0].findtext("out") == "60"
    assert vclips[0].findtext("start") == "0"
    assert vclips[0].findtext("end") == "30"
    # 2本目: src 120-180 → rec 30-90
    assert vclips[1].findtext("start") == "30"
    assert vclips[1].findtext("end") == "90"

    # file 実体は最初のクリップのみ、以降は id 参照
    assert vclips[0].find("file").findtext("pathurl", "").startswith("file://")
    assert vclips[1].find("file").findtext("pathurl") is None
    assert vclips[1].find("file").get("id") == "file-1"

    # A/V リンク
    link_refs = [l.findtext("linkclipref") for l in vclips[0].findall("link")]
    assert link_refs == ["clipitem-v1", "clipitem-a1"]

    # マーカー: ソース 4.5s(135f) → タイムライン 30 + (135-120) = 45
    markers = seq.findall("marker")
    assert len(markers) == 1
    assert markers[0].findtext("in") == "45"
    assert markers[0].findtext("name") == "フィラー: えー"


def test_no_audio_track_when_source_has_none(tmp_path):
    info = make_info(tmp_path, has_audio=False)
    xml = build_xmeml(info, [Segment(0.0, 1.0)])
    root = parse(xml)
    assert root.findall(".//media/audio/track/clipitem") == []
    vclips = root.findall(".//media/video/track/clipitem")
    # 音声がない場合はリンクは video のみ
    assert [l.findtext("mediatype") for l in vclips[0].findall("link")] == ["video"]


def test_marker_in_cut_region_is_dropped(tmp_path):
    info = make_info(tmp_path)
    xml = build_xmeml(info, [Segment(0.0, 1.0)], markers=[Marker(sec=5.0, name="x")])
    root = parse(xml)
    assert root.findall(".//sequence/marker") == []
