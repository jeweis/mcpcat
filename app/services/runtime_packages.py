"""读取活动环境或缓存清单，不导入上游程序或执行其代码。"""

import json
from pathlib import Path


def _python_packages(root: Path, observed: dict) -> set:
    prefix = (
        Path(observed["python_environment"]).resolve()
        if observed.get("python_environment")
        else None
    )
    metadata = (
        prefix.glob("lib/python*/site-packages/*.dist-info/METADATA")
        if prefix and prefix.is_relative_to(root)
        else (root / "uv" / "archive-v0").glob("*/*.dist-info/METADATA")
    )
    result = set()
    for index, path in enumerate(metadata):
        if index >= 1000:
            break
        try:
            if not path.resolve().is_relative_to(root.resolve()):
                continue
            name = version = None
            with path.open() as source:
                for _ in range(30):
                    line = source.readline(2048)
                    if line.startswith("Name: "):
                        name = line[6:].strip()[:128]
                    elif line.startswith("Version: "):
                        version = line[9:].strip()[:128]
            if name and version:
                result.add(("python", name, version))
        except OSError:
            continue
    return result


def _node_packages(root: Path, observed: dict) -> set:
    node = (
        Path(observed["node_environment"]).resolve()
        if observed.get("node_environment")
        else None
    )
    locks = (
        [node / "package-lock.json"]
        if node and node.is_relative_to(root)
        else (root / "npm" / "_npx").glob("*/package-lock.json")
    )
    result = set()
    for index, path in enumerate(locks):
        if index >= 50:
            break
        try:
            if not path.resolve().is_relative_to(root.resolve()):
                continue
            if path.stat().st_size > 4_000_000:
                continue
            for key, package in (
                json.loads(path.read_text()).get("packages", {}).items()
            ):
                if key and "node_modules/" in key and package.get("version"):
                    result.add(
                        (
                            "node",
                            key.rsplit("node_modules/", 1)[-1][:128],
                            str(package["version"])[:128],
                        )
                    )
        except (OSError, ValueError):
            continue
    return result


def package_snapshot(root: Path, observed: dict | None = None) -> list[dict]:
    """有界读取版本；能定位活动环境时避免混入同缓存中其他版本。"""
    observed = observed or {}
    packages = _python_packages(root, observed) | _node_packages(root, observed)
    return [
        {"runtime": kind, "name": name, "version": version}
        for kind, name, version in sorted(packages)[:200]
    ]
