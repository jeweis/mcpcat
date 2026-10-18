"""安全下限不可被后续锁文件或非 uv 安装路径静默降级。"""

import re
from pathlib import Path

import pytest
from packaging.version import Version

ROOT = Path(__file__).parents[1]
FLOORS = {
    "pyjwt": ("2.15.1", "3"),
    "pygments": ("2.21.0", "3"),
    "anyio": ("4.15.1", "5"),
    "cryptography": ("50.0.2", "51"),
    "pydantic-settings": ("2.14.2", "3"),
    "python-dotenv": ("1.2.4", "2"),
    "requests": ("2.34.2", "3"),
    "urllib3": ("2.8.0", "3"),
    "sse-starlette": ("3.5.0", "4"),
}


@pytest.mark.parametrize("name", FLOORS)
def test_all_installation_paths_and_lock_respect_security_floor(name: str) -> None:
    """包括 Python 3.10 分支，不以当前机器选中的一个版本代替全锁校验。"""
    minimum, maximum = FLOORS[name]
    requirement = f"{name}>={minimum},<{maximum}"
    assert requirement in (ROOT / "requirements.txt").read_text()
    assert f'"{requirement}"' in (ROOT / "pyproject.toml").read_text()
    packages = re.split(
        r"^\[\[package\]\]\s*$", (ROOT / "uv.lock").read_text(), flags=re.M
    )
    versions = [
        re.search(r'^version = "([^"]+)"', package, re.M).group(1)
        for package in packages
        if re.search(rf'^name = "{re.escape(name)}"$', package, re.M)
    ]
    assert versions
    assert all(
        Version(minimum) <= Version(value) < Version(maximum) for value in versions
    )


def test_docker_build_tools_and_python_floor_are_preserved() -> None:
    """构建工具显式固定；不得以删除 Python 3.10 支持绕过安全约束。"""
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert '"pip==26.2"' in dockerfile
    assert '"uv==0.13.1"' in dockerfile
    assert 'ENV PATH="$PNPM_HOME:$PATH"' in dockerfile
    assert 'requires-python = ">=3.10"' in (ROOT / "pyproject.toml").read_text()
