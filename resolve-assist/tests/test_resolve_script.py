"""ResolveAssist_ApplyCuts.py を Resolve API のモックで end-to-end 検証する。

実機の Resolve なしで、スクリプトが cuts.json を読み、クリップ取り込み・
タイムライン作成・区間配置・マーカー打ちを正しい引数で行うことを確認する。
"""

import json
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "resolve_scripts"
    / "ResolveAssist_ApplyCuts.py"
)


class FakeClip:
    def __init__(self, path):
        self.path = path

    def GetClipProperty(self, key):
        assert key == "File Path"
        return self.path


class FakeFolder:
    def __init__(self, clips):
        self.clips = clips

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return []


class FakeTimeline:
    def __init__(self, name):
        self.name = name
        self.markers = []

    def GetName(self):
        return self.name

    def AddMarker(self, frame_id, color, name, note, duration):
        self.markers.append((frame_id, color, name, note, duration))
        return True


class FakeMediaPool:
    def __init__(self, root):
        self.root = root
        self.imported = []
        self.timelines = []
        self.appended = None

    def GetRootFolder(self):
        return self.root

    def ImportMedia(self, paths):
        clips = [FakeClip(p) for p in paths]
        self.imported.extend(paths)
        self.root.clips.extend(clips)
        return clips

    def CreateEmptyTimeline(self, name):
        tl = FakeTimeline(name)
        self.timelines.append(tl)
        return tl

    def AppendToTimeline(self, clip_infos):
        self.appended = clip_infos
        return [object()] * len(clip_infos)


class FakeProject:
    def __init__(self, media_pool, existing_timelines=()):
        self.media_pool = media_pool
        self.existing = list(existing_timelines)
        self.current_timeline = None

    def GetMediaPool(self):
        return self.media_pool

    def GetTimelineCount(self):
        return len(self.existing)

    def GetTimelineByIndex(self, i):
        return self.existing[i - 1]

    def SetCurrentTimeline(self, tl):
        self.current_timeline = tl


class FakeResolve:
    def __init__(self, project):
        self.project = project

    def GetProjectManager(self):
        return self

    def GetCurrentProject(self):
        return self.project


def run_script(fake_resolve):
    code = SCRIPT.read_text(encoding="utf-8")
    exec(compile(code, str(SCRIPT), "exec"), {"resolve": fake_resolve})


def make_cuts(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "video.mp4"
    source.write_bytes(b"x")
    cuts = {
        "version": 1,
        "source": str(source),
        "fps": 30.0,
        "duration_sec": 10.0,
        "timeline_name": "video_cut",
        "segments": [
            {"start_sec": 0.0, "end_sec": 2.0, "start_frame": 0, "end_frame": 60},
            {"start_sec": 4.0, "end_sec": 6.0, "start_frame": 120, "end_frame": 180},
        ],
        "markers": [
            {"frame": 130, "name": "フィラー: えー", "note": "カット候補",
             "color": "Red", "duration_frames": 12},
            {"frame": 90, "name": "カット済み区間", "color": "Blue",
             "duration_frames": 1},
        ],
        "scene_cuts": [],
        "srt": None,
    }
    cuts_path = tmp_path / "cuts.json"
    cuts_path.write_text(json.dumps(cuts, ensure_ascii=False), encoding="utf-8")
    pointer_dir = tmp_path / ".resolve_assist"
    pointer_dir.mkdir()
    (pointer_dir / "latest.json").write_text(
        json.dumps({"cuts": str(cuts_path), "srt": None}), encoding="utf-8"
    )
    return source


def test_apply_cuts_full_flow(tmp_path, monkeypatch, capsys):
    source = make_cuts(tmp_path, monkeypatch)
    media_pool = FakeMediaPool(FakeFolder([]))
    project = FakeProject(media_pool)
    run_script(FakeResolve(project))

    out = capsys.readouterr().out
    assert "完了" in out, out

    # 取り込みとタイムライン作成
    assert media_pool.imported == [str(source)]
    assert len(media_pool.timelines) == 1
    timeline = media_pool.timelines[0]
    assert timeline.name == "video_cut"
    assert project.current_timeline is timeline

    # 区間: end_frame は排他的なので -1 されて渡る
    assert [(c["startFrame"], c["endFrame"]) for c in media_pool.appended] == [
        (0, 59),
        (120, 179),
    ]

    # マーカー: ソース130フレーム → タイムライン 60 + (130-120) = 70。
    # ソース90フレームはカットで消えた区間なのでスキップされる
    assert timeline.markers == [(70, "Red", "フィラー: えー", "カット候補", 12)]


def test_apply_cuts_reuses_existing_clip_and_uniquifies_name(
    tmp_path, monkeypatch, capsys
):
    source = make_cuts(tmp_path, monkeypatch)
    existing_clip = FakeClip(str(source))
    media_pool = FakeMediaPool(FakeFolder([existing_clip]))
    project = FakeProject(
        media_pool, existing_timelines=[FakeTimeline("video_cut")]
    )
    run_script(FakeResolve(project))

    assert media_pool.imported == []  # 再取り込みしない
    assert media_pool.timelines[0].name == "video_cut_2"  # 名前重複を回避
    assert "完了" in capsys.readouterr().out
