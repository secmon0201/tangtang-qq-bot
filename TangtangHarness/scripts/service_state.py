"""Read local service records for the Windows operator scripts."""
import argparse
import json
from pathlib import Path

from tangtang_harness.harness_process import status
from tangtang_harness.speech_runtime import SpeechRuntimeManager


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    settings_path = root / 'config' / 'settings.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8-sig')) if settings_path.exists() else {}
    print(json.dumps({
        'harness': status(root),
        'speech': SpeechRuntimeManager(root).status(),
        'port': settings.get('port', 8090),
        'speech_gate': settings.get('speech_enabled', False),
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
