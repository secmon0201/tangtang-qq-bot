from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_lagrange_scripts_keep_the_existing_reverse_onebot_endpoint():
    configuration = (ROOT / "scripts" / "configure_lagrange_onebot.ps1").read_text(
        encoding="utf-8"
    )
    assert "Type = 'ReverseWebSocket'" in configuration
    assert "Suffix = '/onebot/v11/ws'" in configuration
    assert "AccessToken = [string]$values['ONEBOT_ACCESS_TOKEN']" in configuration


def test_lagrange_installer_requires_the_published_archive_hash_before_extracting():
    installer = (ROOT / "scripts" / "install_lagrange_onebot.ps1").read_text(encoding="utf-8")
    assert "F49D7351EE37F4985D3B90383A727F5FE25A2FBA1265B7153822BA5702291025" in installer
    assert "Get-FileHash" in installer
    assert "Expand-Archive" in installer


def test_lagrange_handoff_stops_the_old_transport_before_starting_the_new_one():
    switcher = (ROOT / "scripts" / "switch_to_lagrange_onebot.ps1").read_text(encoding="utf-8")
    assert "backup.ps1" in switcher
    assert switcher.index("stop_napcat_transport.ps1") < switcher.index("start_lagrange_onebot.ps1")
