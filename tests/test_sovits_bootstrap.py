import builtins
import os

from bot.integrations.sovits_bootstrap import redirect_g2pw_cache


def test_vendor_cache_writes_redirect_without_changing_other_files(tmp_path):
    vendor = tmp_path / "source/GPT_SoVITS/text/g2pw"
    vendor.mkdir(parents=True)
    (vendor / "g2pw.py").write_text('"polyphonic.pickle" "polyphonic.md5" open(MD5_PATH open(CACHE_PATH', encoding="utf-8")
    marker = vendor / "polyphonic.md5"
    marker.write_text("original", encoding="utf-8")
    original_open = builtins.open
    with redirect_g2pw_cache(tmp_path / "source", tmp_path / "cache"):
        assert not os.path.exists(marker)
        with open(marker, "w", encoding="utf-8") as stream:
            stream.write("runtime")
        assert os.path.exists(marker)
        with open(marker, encoding="utf-8") as stream:
            assert stream.read() == "runtime"
    assert builtins.open is original_open
    assert marker.read_text(encoding="utf-8") == "original"
    assert (tmp_path / "cache/polyphonic.md5").read_text(encoding="utf-8") == "runtime"
