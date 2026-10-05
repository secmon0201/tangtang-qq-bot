import os

from tangtang_harness.runtime import cleanup_generated


def test_cleanup_covers_business_reports_and_retains_current_avatars_and_data(tmp_path, monkeypatch):
    root = tmp_path / "TangtangHarness"
    now = 2_000_000_000
    monkeypatch.setattr("tangtang_harness.runtime.time.time", lambda: now)

    def generated(relative, age):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic")
        os.utime(path, (now - age, now - age))
        return path

    expired = {
        "runtime/reports/ranking.png",
        "runtime/screenshots/page.png",
        "runtime/speech/reply.wav",
        "reports/preview.png",
        "runtime/avatars/101.aaaaaaaaaaaaaaaa.png",
    }
    for relative in expired:
        generated(relative, 3 * 86400)
    retained = [
        generated("runtime/reports/new.png", 3600),
        generated("runtime/avatars/101.bbbbbbbbbbbbbbbb.png", 2 * 86400),
        generated("runtime/avatars/102.png", 4 * 86400),
        generated("data/backups/snapshot.db", 10 * 86400),
        generated("data/media/history.png", 10 * 86400),
    ]
    legacy = tmp_path / "reports/legacy.png"
    legacy.parent.mkdir()
    legacy.write_bytes(b"legacy")

    assert set(cleanup_generated(root, apply=False)) == expired
    assert all((root / relative).exists() for relative in expired)
    assert set(cleanup_generated(root)) == expired
    assert not any((root / relative).exists() for relative in expired)
    assert all(path.exists() for path in retained) and legacy.exists()
