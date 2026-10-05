from pathlib import Path
import argparse
import json
from tangtang_harness.config import DEFAULT_ROOT
from tangtang_harness.migration import import_snapshot

parser = argparse.ArgumentParser(description="只读在线快照迁移；默认预览，不运行旧任务")
parser.add_argument("--legacy-root", type=Path, default=DEFAULT_ROOT.parent)
parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
parser.add_argument("--apply", action="store_true")
parser.add_argument("--snapshot-id", help="复用新目录内已有一致性快照，不重新读取活跃旧库")
args = parser.parse_args()
result = import_snapshot(args.legacy_root, args.root, apply=args.apply, snapshot_id=args.snapshot_id)
print(json.dumps(result, ensure_ascii=False, indent=2))
