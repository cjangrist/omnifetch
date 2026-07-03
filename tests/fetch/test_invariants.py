"""Static invariants for the fetch package and provider test matrix."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

from omnifetch.fetch.providers.registry import import_all_providers

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FETCH_ROOT = _PROJECT_ROOT / "src" / "omnifetch" / "fetch"
_PROVIDER_ROOT = _FETCH_ROOT / "providers"
_PROVIDER_TEST_ROOT = Path(__file__).resolve().parent / "providers"
_CONFTEST_PATH = _PROJECT_ROOT / "tests" / "conftest.py"
_INFRA_MODULE_NAMES = frozenset({"__init__", "base", "kimi_proxy", "registry"})
_SHARED_PROVIDER_TEST_MODULES = {
    "firecrawl": "test_tavily_firecrawl.py",
    "tavily": "test_tavily_firecrawl.py",
}
_FORBIDDEN_IMPORT_ROOTS = frozenset({"requests", "urllib.request"})


def _python_files(root: Path) -> tuple[Path, ...]:
    """Return Python files under ``root`` excluding bytecode caches."""
    return tuple(
        path
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    )


def _provider_module_names() -> set[str]:
    """Return concrete provider module/package names present on disk."""
    file_modules = {
        path.stem
        for path in _PROVIDER_ROOT.glob("*.py")
        if path.stem not in _INFRA_MODULE_NAMES
    }
    package_modules = {
        path.name
        for path in _PROVIDER_ROOT.iterdir()
        if path.is_dir()
        and (path / "__init__.py").is_file()
        and path.name != "__pycache__"
    }
    return file_modules | package_modules


def _test_function_names(path: Path) -> tuple[str, ...]:
    """Return top-level test function names from a Python test module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return tuple(
        node.name
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
        and node.name.startswith("test_")
    )


def _format_violations(violations: Iterable[str]) -> str:
    """Return violations as a stable assertion message."""
    return "\n".join(sorted(violations))


def _provider_env_names_from_conftest() -> frozenset[str]:
    """Return provider env names isolated by the shared conftest fixture."""
    tree = ast.parse(_CONFTEST_PATH.read_text(encoding="utf-8"))
    assignment = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "_PROVIDER_ENV_NAMES"
                for target in node.targets
            )
        ),
        None,
    )
    assert assignment is not None
    value = ast.literal_eval(assignment.value)
    assert isinstance(value, tuple)
    assert all(isinstance(name, str) for name in value)
    return frozenset(value)


def _network_runtime_violations(path: Path) -> tuple[str, ...]:
    """Return direct network/runtime client violations for one source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            violations.extend(
                alias.name
                for alias in node.names
                if _is_forbidden_import(alias.name)
            )
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            if _is_forbidden_import(module_name):
                violations.append(module_name)
            if module_name == "urllib" and any(
                alias.name == "request" for alias in node.names
            ):
                violations.append("from urllib import request")
            if module_name == "httpx" and any(
                alias.name == "AsyncClient" for alias in node.names
            ):
                violations.append("from httpx import AsyncClient")
        elif isinstance(node, ast.Call):
            function = node.func
            if (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == "httpx"
                and function.attr == "AsyncClient"
            ):
                violations.append("httpx.AsyncClient")
            elif (
                isinstance(function, ast.Name) and function.id == "AsyncClient"
            ):
                violations.append("AsyncClient")
    return tuple(violations)


def _is_forbidden_import(module_name: str) -> bool:
    """Return whether an import path bypasses shared HTTP helpers."""
    return any(
        module_name == root or module_name.startswith(f"{root}.")
        for root in _FORBIDDEN_IMPORT_ROOTS
    )


def test_provider_registry_matches_provider_modules() -> None:
    """Every concrete provider module self-registers exactly one provider."""
    registered_provider_names = set(import_all_providers())

    assert registered_provider_names == _provider_module_names()
    assert "cloudflare_browser" not in registered_provider_names


def test_provider_required_secrets_are_env_isolated() -> None:
    """Provider secret names stay covered by the autouse env reset fixture."""
    provider_env_names = _provider_env_names_from_conftest()
    missing = [
        f"{provider_name}:{secret_name}"
        for provider_name, provider_class in import_all_providers().items()
        for secret_name in provider_class.required_secrets
        if secret_name not in provider_env_names
    ]

    assert missing == []


def test_each_provider_module_has_success_and_failure_tests() -> None:
    """Every concrete provider keeps at least two provider-specific tests."""
    missing: list[str] = []
    too_small: list[str] = []
    for provider_name in sorted(_provider_module_names()):
        test_filename = _SHARED_PROVIDER_TEST_MODULES.get(
            provider_name,
            f"test_{provider_name}.py",
        )
        test_path = _PROVIDER_TEST_ROOT / test_filename
        if not test_path.is_file():
            missing.append(f"{provider_name}:{test_filename}")
            continue
        test_names = _test_function_names(test_path)
        if len(test_names) < 2:
            too_small.append(f"{provider_name}:{test_filename}")

    assert missing == []
    assert too_small == []


def test_fetch_package_uses_only_shared_http_runtime_clients() -> None:
    """Fetch code does not construct clients or bypass shared HTTP helpers."""
    violations = [
        f"{path.relative_to(_PROJECT_ROOT)} uses {violation}"
        for path in _python_files(_FETCH_ROOT)
        for violation in _network_runtime_violations(path)
    ]

    assert _format_violations(violations) == ""


def test_network_runtime_violation_detection_catches_import_variants(
    tmp_path: Path,
) -> None:
    """Network invariant catches common ways to bypass shared HTTP helpers."""
    module_path = tmp_path / "network_bypass.py"
    module_path.write_text(
        "\n".join(
            (
                "from __future__ import annotations",
                "import requests.exceptions",
                "from urllib import request",
                "from httpx import AsyncClient",
                "import httpx",
                "client = httpx.AsyncClient()",
                "other = AsyncClient()",
                "",
            )
        ),
        encoding="utf-8",
    )

    assert _network_runtime_violations(module_path) == (
        "requests.exceptions",
        "from urllib import request",
        "from httpx import AsyncClient",
        "httpx.AsyncClient",
        "AsyncClient",
    )
