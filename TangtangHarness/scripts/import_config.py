from pathlib import Path
import argparse
import json

from tangtang_harness.config import DEFAULT_ROOT
from tangtang_harness.config_import import import_config


def main():
    parser = argparse.ArgumentParser(description='只读导入旧实例配置；默认仅输出字段名和数量，--apply 仅写新目录')
    parser.add_argument('--legacy-root', type=Path, default=DEFAULT_ROOT.parent)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(import_config(args.legacy_root, args.root, apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
