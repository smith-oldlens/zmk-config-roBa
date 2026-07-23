from xml.etree import ElementTree as ET

from resolve_assist.export.fcpxml import build_fcpxml, frame_duration_fraction
from resolve_assist.types import Marker, MediaInfo, Segment


def make_info(tmp_path, fps=30.0):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    return MediaInfo(
        path=str(video), duration=10.0, fps=fps, width=1920, height=1080
    )


def parse(xml_str):
    body = xml_str.split("<!DOCTYPE fcpxml>")[1]
    return ET.fromstring(body)


def test_frame_duration_ntsc():
    assert frame_duration_fraction(29.97) == (1001, 30000)
    assert frame_duration_fraction(23.976) == (1001, 24000)
    assert frame_duration_fraction(59.94) == (1001, 60000)
    assert frame_duration_fraction(30.0) == (1, 30)
    assert frame_duration_fraction(24.0) == (1, 24)


def test_build_fcpxml_structure(tmp_path):
    info = make_info(tmp_path)
    xml = build_fcpxml(
        info,
        [Segment(1.0, 2.0), Segment(4.0, 6.0)],
        markers=[Marker(sec=4.5, name="フィラー: えー", color="Red")],
    )
    root = parse(xml)
    assert root.get("version") == "1.9"

    fmt = root.find("resources/format")
    assert fmt.get("frameDuration") == "1/30s"
    assert fmt.get("width") == "1920"

    asset = root.find("resources/asset")
    assert asset.get("duration") == "300/30s"
    assert asset.find("media-rep").get("src").startswith("file://")

    clips = root.findall(".//spine/asset-clip")
    assert len(clips) == 2
    # 1本目: src 30-60 (30f) → offset 0
    assert clips[0].get("offset") == "0/30s"
    assert clips[0].get("start") == "30/30s"
    assert clips[0].get("duration") == "30/30s"
    # 2本目: src 120-180 (60f) → offset 30f
    assert clips[1].get("offset") == "30/30s"
    assert clips[1].get("start") == "120/30s"

    # マーカーは 4.5 秒 (=135f) を含む2本目のクリップにソース時間で付く
    assert clips[0].findall("marker") == []
    markers = clips[1].findall("marker")
    assert len(markers) == 1
    assert markers[0].get("start") == "135/30s"
    assert markers[0].get("value") == "フィラー: えー"


def test_marker_in_cut_region_is_dropped(tmp_path):
    info = make_info(tmp_path)
    xml = build_fcpxml(
        info,
        [Segment(0.0, 1.0)],
        markers=[Marker(sec=5.0, name="消える")],
    )
    root = parse(xml)
    assert root.findall(".//marker") == []


def test_sequence_duration_is_total_kept(tmp_path):
    info = make_info(tmp_path, fps=29.97)
    xml = build_fcpxml(info, [Segment(0.0, 1.0), Segment(2.0, 3.0)])
    root = parse(xml)
    seq = root.find(".//sequence")
    # 各セグメント約30fずつ → 60f
    assert seq.get("duration") == f"{60 * 1001}/30000s"
    assert seq.get("tcFormat") == "NDF"
