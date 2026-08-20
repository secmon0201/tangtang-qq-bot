"""Validate dependency direction inside the project-owned bot package."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


def module_name(package_root: Path, source: Path) -> str:
    relative = source.relative_to(package_root.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def module_inventory(package_root: Path) -> dict[Path, str]:
    return {
        source: module_name(package_root, source)
        for source in sorted(package_root.rglob("*.py"))
        if "__pycache__" not in source.parts
    }


def imported_modules(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names if alias.name.startswith("bot"))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("bot"):
            imports.add(node.module)
            imports.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imports


def resolve_import(name: str, known_modules: set[str]) -> str | None:
    candidate = name
    while candidate.startswith("bot"):
        if candidate in known_modules:
            return candidate
        if "." not in candidate:
            return None
        candidate = candidate.rsplit(".", 1)[0]
    return None


def build_graph(package_root: Path) -> dict[str, set[str]]:
    inventory = module_inventory(package_root)
    known = set(inventory.values())
    graph: dict[str, set[str]] = {name: set() for name in known}
    for source, importer in inventory.items():
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for imported in imported_modules(tree):
            resolved = resolve_import(imported, known)
            if resolved and resolved != importer:
                graph[importer].add(resolved)
    return graph


def dependency_cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []
    cycles: set[tuple[str, ...]] = set()

    def visit(module: str) -> None:
        if module in visited:
            return
        if module in visiting:
            start = stack.index(module)
            cycle = stack[start:]
            rotations = [tuple(cycle[index:] + cycle[:index]) for index in range(len(cycle))]
            cycles.add(min(rotations))
            return
        visiting.add(module)
        stack.append(module)
        for dependency in sorted(graph.get(module, ())):
            visit(dependency)
        stack.pop()
        visiting.remove(module)
        visited.add(module)

    for module in sorted(graph):
        visit(module)
    return sorted(cycles)


def duplicate_function_bodies(
    package_root: Path, minimum_lines: int = 12
) -> list[tuple[tuple[str, str, int], ...]]:
    """Find substantial functions copied verbatim across different modules."""
    fingerprints: dict[str, list[tuple[str, str, int]]] = {}
    for source, module in module_inventory(package_root).items():
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            end_line = int(node.end_lineno or node.lineno)
            if end_line - int(node.lineno) + 1 < minimum_lines:
                continue
            body = list(node.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(
                body[0].value, ast.Constant
            ) and isinstance(body[0].value.value, str):
                body.pop(0)
            if len(body) < 2:
                continue
            fingerprint = ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False)
            fingerprints.setdefault(fingerprint, []).append((module, node.name, int(node.lineno)))

    duplicates: list[tuple[tuple[str, str, int], ...]] = []
    for locations in fingerprints.values():
        if len({module for module, _name, _line in locations}) > 1:
            duplicates.append(tuple(sorted(locations)))
    return sorted(duplicates)


def validate_architecture(project_root: Path) -> list[str]:
    package_root = project_root / "bot"
    graph = build_graph(package_root)
    errors: list[str] = []
    for importer, dependencies in sorted(graph.items()):
        for dependency in sorted(dependencies):
            if importer.startswith("bot.plugins.") and dependency.startswith("bot.plugins."):
                errors.append(f"plugin-to-plugin import: {importer} -> {dependency}")
            if importer.startswith(("bot.services.", "bot.application.")) and dependency.startswith(
                "bot.plugins."
            ):
                errors.append(f"dependency inversion: {importer} -> {dependency}")
    for cycle in dependency_cycles(graph):
        errors.append("dependency cycle: " + " -> ".join((*cycle, cycle[0])))
    for duplicate in duplicate_function_bodies(package_root):
        locations = ", ".join(
            f"{module}.{name}:{line}" for module, name, line in duplicate
        )
        errors.append(f"duplicate implementation: {locations}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    errors = validate_architecture(args.root.resolve())
    if errors:
        print("Architecture validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "Architecture validation passed: dependency direction, cycles, and duplicate "
        "implementations are clean."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
