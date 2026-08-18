from __future__ import annotations

import contextlib
import fcntl
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Iterator, Sequence


CommandRunner = Callable[[Sequence[str]], None]


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean, got {value!r}")


def run_command(command: Sequence[str]) -> None:
    try:
        subprocess.run(
            list(command),
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "").strip()
        detail = f": {output}" if output else ""
        raise RuntimeError(f"command failed ({' '.join(command)}){detail}") from exc


@contextlib.contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield


def atomic_write(path: Path, content: bytes, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ownership_source = path if path.exists() else path.parent
    ownership = ownership_source.stat()
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.chmod(temporary_path, mode)
        # Preserve the installed root:dnsdist ownership. Without this, a root
        # updater would replace a readable file with root:root mode 0640.
        os.chown(temporary_path, ownership.st_uid, ownership.st_gid)
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def activate_generated_file(
    target: Path,
    content: str,
    main_config: Path,
    *,
    check: bool = True,
    reload_service: bool = True,
    runner: CommandRunner = run_command,
) -> bool:
    """Atomically replace a generated file, validate, and reload if active.

    Returns True when the target changed. On validation/reload failure the old
    file is restored, and a best-effort reload of the restored configuration is
    attempted when the failed step might already have stopped the service.
    """

    new_bytes = content.encode("utf-8")
    old_bytes = target.read_bytes() if target.exists() else None
    if old_bytes == new_bytes:
        return False

    atomic_write(target, new_bytes)
    check_command = ("dnsdist", "--check-config", "-C", str(main_config))
    reload_command = (
        "systemctl",
        "try-reload-or-restart",
        "dnsdist.service",
    )

    validation_succeeded = not check
    try:
        if check:
            runner(check_command)
            validation_succeeded = True
        if reload_service:
            runner(reload_command)
    except Exception:
        if old_bytes is None:
            target.unlink(missing_ok=True)
        else:
            atomic_write(target, old_bytes)
        if validation_succeeded and reload_service:
            with contextlib.suppress(Exception):
                runner(reload_command)
        raise

    return True
