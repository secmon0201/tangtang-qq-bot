"""Summarize real snapshot coverage without emitting private rows or identities."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from tangtang_harness.config import DEFAULT_ROOT
from tangtang_harness.migration import SOURCES, TARGETS, table_names, quote
from tangtang_harness.store import Store


def verify(root):
    store = Store(root)
    report = {'legacy_source_files': {}, 'snapshots': [], 'business_tables': [], 'pending_replayed': 0}
    baseline = root / 'data' / 'legacy-source-baseline.json'
    if baseline.exists():
        original = json.loads(baseline.read_text(encoding='utf-8'))
        changes = []
        for relative, expected in original.items():
            path = root.parent / relative
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                changes.append(relative)
        report['legacy_source_files'] = {'checked': len(original), 'changed': len(changes), 'paths': changes}
    imports = {item['origin'].split(':', 1)[0]: item for item in store.imports()}
    for kind, origin in SOURCES.items():
        relative = store.get_setting('legacy_snapshot:' + kind)
        if not relative:
            continue
        snapshot = root / relative
        with snapshot.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        record = imports[origin]
        report['snapshots'].append({'kind': kind, 'hash_matches_import': digest == record['source_hash']})
        if kind not in TARGETS:
            continue
        target = root / 'runtime' / TARGETS[kind]
        with sqlite3.connect(snapshot) as source, sqlite3.connect(target) as new:
            names = set(table_names(new))
            for table in table_names(source):
                if table not in names or (kind == 'tangtang' and table != 'tangtang_group_messages'):
                    continue
                if kind == 'skill_audit' and table != 'skill_audit_entries':
                    continue
                if any(word in table for word in ('compaction_jobs','proactive_state','claims','expression_events','persona_profile_jobs')) or table in {'schema_migrations','chat_gate_revisions'}:
                    continue
                source_count = source.execute('SELECT count(*) FROM ' + quote(table)).fetchone()[0]
                if 'harness_import_rows' in names:
                    imported = new.execute('SELECT count(*) FROM harness_import_rows WHERE origin=? AND table_name=?', (origin,table)).fetchone()[0]
                else:
                    imported = 0
                report['business_tables'].append({'kind':kind,'table':table,'source_rows':source_count,'imported_rows':imported,'complete':source_count == imported})
    with store.connect() as conn:
        report['confirmed_turns'] = conn.execute("SELECT count(*) FROM turns WHERE event_key LIKE 'import:%'").fetchone()[0]
        report['frozen_image_assets'] = conn.execute('SELECT count(*) FROM assets').fetchone()[0]
        report['unresolved_legacy_vision_refs'] = conn.execute("SELECT count(*) FROM turns WHERE event_key LIKE 'import:%' AND user_content LIKE '%vision:%'").fetchone()[0]
        report['new_queued_jobs'] = conn.execute("SELECT count(*) FROM background_jobs WHERE status='queued'").fetchone()[0]
        report['foreign_key_issues'] = len(conn.execute('PRAGMA foreign_key_check').fetchall())
    for name in set(TARGETS.values()):
        path = root / 'runtime' / name
        if path.exists():
            with sqlite3.connect(path) as conn:
                report['foreign_key_issues'] += len(conn.execute('PRAGMA foreign_key_check').fetchall())
    report['complete_business_coverage'] = all(row['complete'] for row in report['business_tables'])
    path = root / 'runtime' / 'verification' / 'migration_verification.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2), encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key != 'business_tables'},ensure_ascii=False,indent=2))
    print('business_tables=' + str(len(report['business_tables'])))
    print('incomplete_tables=' + json.dumps([row for row in report['business_tables'] if not row['complete']],ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    verify(parser.parse_args().root)
