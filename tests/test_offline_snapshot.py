"""Guards for the offline forecast snapshot (writer + renderer wiring)."""

from pathlib import Path


def test_snapshot_writer_includes_new_fields():
    """The dashboard saves the go/no-go score, summary, best times, and gear."""
    index = Path("templates/index.html").read_text(encoding="utf-8")
    assert "fishforecast_snapshot" in index
    for field in ("score:", "summary:", "best_times:", "gear:"):
        assert field in index, f"snapshot writer missing {field}"


def test_offline_page_renders_new_sections():
    """offline.html reads the snapshot and renders the new sections."""
    offline = Path("static/offline.html").read_text(encoding="utf-8")
    assert "fishforecast_snapshot" in offline
    assert "snap-besttimes" in offline
    assert "snap-gear" in offline
    # Score is shown alongside the verdict.
    assert "/100" in offline


def test_service_worker_precaches_offline_page():
    sw = Path("static/sw.js").read_text(encoding="utf-8")
    assert "offline.html" in sw
    # Cache version was bumped so the updated offline page is picked up.
    assert "fishforecast-v10" in sw
