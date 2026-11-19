"""持久运行缓存修订；配置秘密仅参与 HMAC，不写入清单。"""

import hashlib
import hmac
import json
import os
import platform
import secrets
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from app.core.config import settings


def write_json(path: Path, value: dict) -> None:
    """原子替换私有元数据，进程中断不留下半份清单。"""
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(value, output, ensure_ascii=False)
    temporary.replace(path)


def executable_identity(command: str) -> dict:
    """用运行时文件身份使升级后的工具链自动获得新修订。"""
    identities = []
    for item in (command, "node", "uv"):
        path = shutil.which(item)
        try:
            stat = Path(path).stat() if path else None
            identities.append(
                (
                    path,
                    stat.st_mtime_ns if stat else None,
                    stat.st_size if stat else None,
                )
            )
        except OSError:
            identities.append((path, None, None))
    return {"files": identities, "python": sys.version, "platform": platform.platform()}


def configuration_signature(root: Path, config: dict[str, Any]) -> str:
    """仅将配置秘密用于带密钥摘要，清单不保存原文。"""
    with FileLock(str(root / ".key.lock"), timeout=10):
        key = root / ".identity-key"
        if not key.exists():
            descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(secrets.token_bytes(32))
        payload = {
            name: config.get(name)
            for name in (
                "command",
                "args",
                "env",
                "environment_mode",
                "startup_compatibility",
            )
        }
        payload["runtime"] = executable_identity(config["command"])
        payload["local_files"] = [
            (item, Path(item).stat().st_mtime_ns, Path(item).stat().st_size)
            for item in config.get("args", [])
            if item.endswith((".py", ".js", ".mjs")) and Path(item).is_file()
        ]
        signature = hmac.new(
            key.read_bytes(), json.dumps(payload, sort_keys=True).encode(), "sha256"
        ).hexdigest()
        return signature


class RuntimeEnvironment:
    """缓存可共享，进程和协议会话始终独立；所有清单操作使用 OS 锁。"""

    def __init__(self, server: str, config: dict[str, Any]) -> None:
        root = Path(settings.mcpcat_database_path).resolve().parent / "runtime"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / hashlib.sha256(server.encode()).hexdigest()[:24]
        self.path.mkdir(mode=0o700, exist_ok=True)
        self.lock = FileLock(str(self.path / ".lock"), timeout=10)
        self.config = config
        command = Path(config["command"]).name.lower()
        self.manager = (
            "npm"
            if command in {"npx", "npx.cmd", "npx.exe"}
            else (
                "uv"
                if command in {"uvx", "uvx.exe"}
                or command in {"uv", "uv.exe"}
                and (config.get("args") or [])[:2] == ["tool", "run"]
                else "native"
            )
        )
        self.server = server
        self.external = config.get("environment_mode", "managed") == "external"
        self.signature = configuration_signature(root, config)
        with self.lock:
            state = self._load()
            if (
                not state.get("current")
                or state["revisions"][state["current"]]["config_hash"] != self.signature
            ):
                self._new(state)
            self.revision = state["current"]

    def _load(self) -> dict:
        path = self.path / "index.json"
        return (
            json.loads(path.read_text())
            if path.exists()
            else {"current": None, "revisions": {}}
        )

    def _new(self, state: dict) -> str:
        revision = uuid.uuid4().hex
        state["revisions"][revision] = {
            "config_hash": self.signature,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "validated": False,
            "packages": [],
        }
        state["current"] = revision
        (self.path / revision).mkdir(mode=0o700)
        write_json(self.path / "index.json", state)
        return revision

    @property
    def directory(self) -> Path:
        """当前实例使用的稳定缓存目录，不跟随其他连接动态切换。"""
        return self.path / self.revision

    def environment(self) -> dict[str, str]:
        """尊重显式缓存、代理和依赖约束，只为未指定的缓存提供隔离路径。"""
        env = dict(self.config.get("env") or {})
        if not self.external:
            if self.manager == "npm" and not any(
                key.lower() == "npm_config_cache" for key in env
            ):
                env["npm_config_cache"] = str(self.directory / "npm")
            if self.manager == "uv":
                env.setdefault("UV_CACHE_DIR", str(self.directory / "uv"))
        return env

    def recovery_args(self, args: list[str]) -> list[str] | None:
        """同一成功环境复用已验证的依赖修复，不改变用户显式约束。"""
        from app.services.runtime_client import uv_tool_prefix
        from app.services.stdio_compatibility import explicit_dependencies

        with self.lock:
            row = self._load()["revisions"][self.revision]
        prefix = uv_tool_prefix(self.config["command"], args)
        if (
            row.get("validated")
            and row.get("recovery_profile") == "python-mcp1-import"
            and prefix is not None
            and not explicit_dependencies(prefix, self.config.get("env") or {})
        ):
            return [*prefix, "--with", "mcp<2", *args[len(prefix) :]]
        return None

    def protocol_mode(self) -> str:
        """同一已验证环境记住旧协议；刷新修订后重新自动探测。"""
        from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS

        with self.lock:
            row = self._load()["revisions"][self.revision]
        return (
            "legacy"
            if row.get("validated")
            and row.get("protocol") in HANDSHAKE_PROTOCOL_VERSIONS
            else "auto"
        )

    def cached_args(self, args: list[str]) -> list[str]:
        """已验证的托管缓存离线复用；显式刷新或外部缓存不改写。"""
        with self.lock:
            valid = self._load()["revisions"][self.revision]["validated"]
        env = self.config.get("env") or {}
        if (
            self.external
            or not valid
            or any(key.lower() in {"uv_cache_dir", "npm_config_cache"} for key in env)
        ):
            return list(args)
        if any(item in args for item in ("--refresh", "--no-cache", "--offline")):
            return list(args)
        name = Path(self.config["command"]).name.lower()
        if name in {"npx", "npx.cmd", "npx.exe", "uvx", "uvx.exe"}:
            return ["--offline", *args]
        if name in {"uv", "uv.exe"} and args[:2] == ["tool", "run"]:
            return [*args[:2], "--offline", *args[2:]]
        return list(args)

    def snapshot(self) -> dict:
        """只输出清单元数据，不返回命令参数、凭证或文件绝对路径。"""
        with self.lock:
            state = self._load()
        return {
            "mode": "external" if self.external else "managed",
            "dependency_manager": self.manager,
            "external_caches": [
                key
                for key in (self.config.get("env") or {})
                if key.lower() in {"uv_cache_dir", "npm_config_cache"}
            ],
            "current": state["current"],
            "revisions": [
                {
                    "id": key,
                    **{k: v for k, v in row.items() if k != "config_hash"},
                    "compatible": row["config_hash"] == self.signature,
                }
                for key, row in state["revisions"].items()
            ],
        }

    def replace_failed_cache(self) -> None:
        """同一失败修订只旋转一次，多 worker 复用已创建的新修订。"""
        with self.lock:
            state = self._load()
            if state["current"] == self.revision:
                self._new(state)

    def select(self, revision: str | None = None) -> str:
        """创建新修订或选择同配置的历史成功修订；不删除任何缓存。"""
        with self.lock:
            state = self._load()
            if revision is None:
                return self._new(state)
            row = state["revisions"].get(revision)
            if not row or row["config_hash"] != self.signature or not row["validated"]:
                raise ValueError("只能回滚到同一配置和工具链的已验证修订")
            state["current"] = revision
            write_json(self.path / "index.json", state)
            return revision

    def record(self, attempt: dict, *, packages: list[dict] | None = None) -> None:
        """保存脱敏运行摘要和实际包快照，旧连接不能改写当前修订指针。"""
        with self.lock:
            state = self._load()
            row = state["revisions"][self.revision]
            row["last_attempt"] = attempt
            if attempt.get("error_code") == "cache":
                row["validated"] = False
            if attempt.get("ready") and attempt.get("error_code") != "cache":
                row["validated"] = True
                row["protocol"] = attempt.get("protocol")
                if attempt.get("recovery_profile"):
                    row["recovery_profile"] = attempt["recovery_profile"]
            if packages is not None:
                row["packages"] = packages
                row["packages_scope"] = attempt.get("packages_scope", "cache_inventory")
            write_json(self.path / "index.json", state)
