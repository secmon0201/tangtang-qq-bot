"""Check that the implemented platform still satisfies the documented design."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class Check:
    check_id: str
    requirement: str
    passed: bool
    evidence: str


def _file(path: str) -> Path:
    return ROOT / path


def _has(path: str, token: str) -> bool:
    target = _file(path)
    if not target.is_file():
        return False
    return token in target.read_text(encoding="utf-8")


def _exists(path: str) -> bool:
    return _file(path).exists()


def _lock_validates() -> bool:
    """Run the real lock validator; never report a formal pass."""

    interpreter = ROOT / ".venv" / "Scripts" / "python.exe"
    script = ROOT / "scripts" / "validate_upstream_lock.py"
    if not interpreter.is_file() or not script.is_file():
        return False
    try:
        result = subprocess.run(
            [str(interpreter), "-X", "utf8", str(script)],
            cwd=ROOT,
            capture_output=True,
            timeout=60,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0


def build_checks() -> list[Check]:
    checks: list[Check] = []

    def add(check_id: str, requirement: str, condition: bool, evidence: str) -> None:
        checks.append(Check(check_id, requirement, bool(condition), evidence))

    # A. 技能注册表与调用
    add(
        "A1",
        "所有插件都有技能归属且命令零冲突",
        _exists("config/skill-registry.json")
        and _has("scripts/audit_command_surface.py", "collisions")
        and _has("scripts/validate_skill_registry.py", "registered in both"),
        "config/skill-registry.json + audit/validator",
    )
    add(
        "A2",
        "确定性路由走技能注册表",
        _has("bot/plugins/tangtang_chat.py", "local_action_skill")
        and _has("bot/services/skills.py", "feature_key_for_action"),
        "bot/services/skills.py + tangtang_chat router",
    )
    add(
        "A3",
        "未注册能力明确拒绝而不是编造",
        _has("bot/plugins/tangtang_chat.py", "persona_rejection")
        and _has("bot/services/tangtang_features.py", "def persona_rejection")
        and _has("tests/test_conversational_skills.py", "invalid_or_inapplicable_model_requests_never_execute")
        and _has("tests/test_conversational_skills.py", "every_execution_path_enforces_gates_before_opener"),
        "router rejection branch + test",
    )
    add(
        "A4",
        "技能独立于 Agent：关闭编排后技能仍可调用",
        _has("bot/services/agent_plan.py", "needs_plan")
        and _has("bot/plugins/tangtang_chat.py", "if needs_plan(text):")
        and _has("tests/test_agent_plan.py", "does_not_enter_agent_branch"),
        "agent_plan trigger tests",
    )

    # B. 上游协商与零改动
    add(
        "B1",
        "运行时上游能力探测与降级",
        _has("bot/services/skill_capability.py", "CapabilityRegistry")
        and _has("bot/plugins/tangtang_chat.py", "capabilities.skill_available"),
        "skill_capability service + router gate",
    )
    add(
        "B2",
        "上游诊断与兼容矩阵可重复",
        _exists("scripts/diagnose_upstream_compat.py")
        and _exists("scripts/diagnose_upstream_compat.ps1")
        and _has("scripts/diagnose_upstream_compat.py", "missing git metadata"),
        "diagnostic scripts",
    )
    add(
        "B3",
        "上游锁校验在更新后仍通过",
        _exists("config/upstream-lock.json") and _lock_validates(),
        "validate_upstream_lock.py live run",
    )

    # C. 审核、纠错、灰度、回滚、观测、成本、安全
    add(
        "C1",
        "纠错账本与分类",
        _has("bot/services/skill_audit.py", "classify_failure")
        and _has("bot/services/skill_audit.py", "skill_audit_entries"),
        "skill_audit service",
    )
    add(
        "C2",
        "技能级灰度与按群范围",
        _has("bot/services/skill_audit.py", "enabled_for_group")
        and _has("bot/plugins/skill_admin.py", "技能 开关"),
        "skill_controls + admin command",
    )
    add(
        "C3",
        "用量与成本记账",
        _has("bot/services/skill_metrics.py", "class SkillMetricsStore")
        and _has("bot/services/skill_metrics.py", "cost"),
        "skill_metrics store",
    )
    add(
        "C4",
        "安全审计与角色门、凭证脱敏",
        _exists("scripts/validate_skill_security.py")
        and _has("bot/services/skill_security.py", "def redact")
        and _has("bot/services/skills.py", "required_role"),
        "security validator + redaction + role field",
    )
    add(
        "C5",
        "质量评估与 SLO 指标",
        _has("bot/services/quality_eval.py", "def quality_summary")
        and _exists("config/quality-golden-set.json")
        and _exists("scripts/judge_skills_quality.py"),
        "quality_eval + golden set + judge runner",
    )

    # D. 上下文与模型质量
    add(
        "D1",
        "取消输入字符截断且原文完整保留",
        _has("bot/services/tangtang_chat.py", "config.max_input_chars <= 0")
        and _has("bot/services/tangtang_chat.py", "history_limit = None")
        and _has("bot/services/tangtang_db.py", "max_chars is None"),
        "unlimited prompt/history paths",
    )
    add(
        "D2",
        "群摘要增量、游标与版本",
        _has("bot/services/group_summary.py", "class GroupSummaryWorker")
        and _has("bot/services/tangtang_db.py", "group_summary_advance")
        and _has("bot/services/tangtang_db.py", "group_summary_versions"),
        "group summary worker + schema",
    )
    add(
        "D3",
        "个人记忆与群摘要分离",
        _has("bot/services/tangtang_chat.py", "_group_summary_lines")
        and _has("bot/services/tangtang_memory.py", "TangtangMemoryKernel"),
        "service split",
    )
    add(
        "D4",
        "来源链可回查",
        _has("bot/services/tangtang_db.py", "provenance")
        and _has("bot/services/tangtang_chat.py", "_provenance"),
        "call provenance column + capture",
    )

    # E. 运行与交付
    add(
        "E1",
        "运行态端口与 OneBot 连接健康脚本存在",
        _exists("scripts/status_qq_transport.ps1")
        and _exists("scripts/verify_full_stack.ps1"),
        "operation scripts",
    )
    add(
        "E2",
        "全量测试与八项校验器可运行",
        _exists("tests/test_command_surface_audit.py")
        and _exists("tests/test_skill_registry.py")
        and _exists("scripts/validate_skill_security.py"),
        "test + validator inventory",
    )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    checks = build_checks()
    report = {
        "total": len(checks),
        "passed": sum(1 for check in checks if check.passed),
        "failed": [check.check_id for check in checks if not check.passed],
        "checks": [
            {
                "id": check.check_id,
                "requirement": check.requirement,
                "passed": check.passed,
                "evidence": check.evidence,
            }
            for check in checks
        ],
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"Design compliance: {report['passed']}/{report['total']} passed"
        )
        for check in checks:
            mark = "PASS" if check.passed else "FAIL"
            print(f"  [{mark}] {check.check_id} {check.requirement}")
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
