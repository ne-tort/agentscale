"""Owned subprocess/uvicorn/Celery lifecycle on ephemeral localhost ports.

No shell, fixed HTTP port, FastAPI state mutation or dependency override is used.
Every reader task and every process is joined during reverse-order shutdown.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
from collections import deque
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import httpx

API_ROOT = Path(__file__).resolve().parents[3]
_SAFE_INHERITED = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TZ",
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR",
    "LD_LIBRARY_PATH", "LIBPATH", "SHLIB_PATH",
})


def ephemeral_localhost_port() -> int:
    """Ask the OS for a real free port; never substitute a required service port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        value = int(sock.getsockname()[1])
    if not 1 <= value <= 65535:
        raise RuntimeError("OS returned an invalid ephemeral port")
    return value


def isolated_environment(explicit: Mapping[str, str]) -> dict[str, str]:
    """Drop ambient application/live/shared settings before exec."""
    env = {name: value for name, value in os.environ.items() if name in _SAFE_INHERITED}
    env.update({
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": str(API_ROOT / "src"),
    })
    env.update({name: str(value) for name, value in explicit.items()})
    return env


class ManagedProcess:
    def __init__(
        self,
        *,
        name: str,
        argv: list[str],
        env: Mapping[str, str],
        shutdown_timeout: float,
        sensitive: Iterable[str] = (),
    ) -> None:
        self.name = name
        self.argv = argv
        self.env = dict(env)
        self.shutdown_timeout = float(shutdown_timeout)
        self._sensitive = tuple(value for value in sensitive if value)
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._lines: deque[str] = deque(maxlen=250)
        self._closed = False

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def returncode(self) -> int | None:
        return self._process.returncode if self._process is not None else None

    def _redact(self, text: str) -> str:
        result = text
        for value in self._sensitive:
            result = result.replace(value, "<redacted>")
        return result.rstrip("\r\n")

    async def start(self) -> None:
        if self._process is not None or self._closed:
            raise RuntimeError(f"{self.name}: process cannot be started twice")
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        self._process = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=str(API_ROOT),
            env=self.env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **kwargs,
        )
        self._reader = asyncio.create_task(
            self._read_output(), name=f"entrypoint-acceptance-output:{self.name}"
        )

    async def _read_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        while True:
            raw = await process.stdout.readline()
            if not raw:
                return
            text = raw.decode("utf-8", errors="replace")
            self._lines.append(self._redact(text))
            if len(self._lines) == self._lines.maxlen:
                # Keep memory bounded even if a child is verbose or stuck logging.
                continue

    def tail(self, count: int = 30) -> str:
        return "\n".join(list(self._lines)[-count:])

    async def wait_http(self, path: str, *, base_url: str, timeout: float, expected: Any = None) -> Any:
        if self._process is None:
            raise RuntimeError(f"{self.name}: not started")
        last: Any = None
        async def poll() -> Any:
            nonlocal last
            async with httpx.AsyncClient(trust_env=False, timeout=2.0) as client:
                while True:
                    if self.returncode is not None:
                        raise RuntimeError(
                            f"{self.name} exited before readiness: {self.returncode}\n{self.tail()}"
                        )
                    try:
                        response = await client.get(base_url + path)
                        last = (response.status_code, response.text[:512])
                        if response.status_code == 200:
                            payload = response.json()
                            if expected is None or payload == expected:
                                return payload
                    except (httpx.HTTPError, ValueError):
                        pass
                    await asyncio.sleep(0.1)
        try:
            return await asyncio.wait_for(poll(), timeout=timeout)
        except TimeoutError:
            raise RuntimeError(
                f"{self.name}: readiness timed out at {path}; last={last!r}\n{self.tail()}"
            ) from None

    async def wait_exit(self, *, timeout: float, expected: int | None = 0) -> int:
        if self._process is None:
            raise RuntimeError(f"{self.name}: not started")
        try:
            code = await asyncio.wait_for(self._process.wait(), timeout=timeout)
        except TimeoutError:
            raise RuntimeError(f"{self.name}: helper did not exit\n{self.tail()}") from None
        finally:
            await self._join_reader()
        if expected is not None and code != expected:
            raise RuntimeError(f"{self.name}: exit {code}, expected {expected}\n{self.tail()}")
        return code

    def json_result(self) -> Any:
        for line in reversed(self._lines):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and "ok" in value:
                if value.get("ok") is not True:
                    raise RuntimeError(f"{self.name}: helper failed: {value}")
                return value
        raise RuntimeError(f"{self.name}: no JSON result\n{self.tail()}")

    async def _join_reader(self) -> None:
        task = self._reader
        if task is None:
            return
        self._reader = None
        try:
            await asyncio.wait_for(task, timeout=self.shutdown_timeout)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise RuntimeError(f"{self.name}: output reader did not stop") from None

    async def _signal(self, sig: int) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            return
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, sig)

    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is None:
            return
        if process.returncode is None:
            await self._signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=self.shutdown_timeout)
            except TimeoutError:
                if os.name == "nt":
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(process.pid), "/T", "/F",
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(killer.wait(), timeout=self.shutdown_timeout)
                else:
                    await self._signal(signal.SIGKILL)
                await asyncio.wait_for(process.wait(), timeout=self.shutdown_timeout)
        await self._join_reader()

    async def aclose(self) -> None:
        await self.stop()


class ProcessSupervisor:
    def __init__(self, *, shutdown_timeout: float) -> None:
        self.shutdown_timeout = shutdown_timeout
        self.processes: dict[str, ManagedProcess] = {}
        self._closed = False

    def add(self, process: ManagedProcess) -> ManagedProcess:
        if self._closed or process.name in self.processes:
            raise RuntimeError(f"duplicate/closed process owner: {process.name}")
        self.processes[process.name] = process
        return process

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        first: Exception | None = None
        for process in reversed(tuple(self.processes.values())):
            try:
                await process.aclose()
            except Exception as exc:
                if first is None:
                    first = exc
        if first is not None:
            raise first

    def assert_all_stopped(self) -> None:
        running = [name for name, process in self.processes.items() if process.returncode is None]
        readers = [name for name, process in self.processes.items() if process._reader is not None]
        assert not running, f"processes still running: {running}"
        assert not readers, f"output readers still running: {readers}"
        assert all(process._closed for process in self.processes.values())


def uvicorn_process(
    *,
    name: str,
    module: str,
    port: int,
    env: Mapping[str, str],
    shutdown_timeout: float,
    sensitive: Iterable[str],
) -> ManagedProcess:
    return ManagedProcess(
        name=name,
        argv=[
            sys.executable, "-m", "uvicorn", f"{module}:app",
            "--host", "127.0.0.1", "--port", str(port),
            "--lifespan", "on", "--log-level", "info",
        ],
        env=env,
        shutdown_timeout=shutdown_timeout,
        sensitive=sensitive,
    )


def celery_worker_process(
    *,
    env: Mapping[str, str],
    shutdown_timeout: float,
    sensitive: Iterable[str],
) -> ManagedProcess:
    return ManagedProcess(
        name="celery-worker",
        argv=[
            sys.executable, "-m", "celery",
            "-A", "prodavan.core.infra.worker_manager.celery_app",
            "worker", "--loglevel=info", "--pool=prefork", "--concurrency=1",
            "--without-gossip", "--without-mingle",
        ],
        env=env,
        shutdown_timeout=shutdown_timeout,
        sensitive=sensitive,
    )
