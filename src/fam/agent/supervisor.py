"""Supervises the agent runtime as a separate OS process.

Shared by E0 and E1 (and later E2, which needs the same stop/start control).
The agent is a separate process rather than a separate Compose service so the
experiment can terminate and restart it without holding Docker control. The
architectural property that matters is preserved: an ordinary external Matrix
client with no Synapse access (testbed-architecture.md §12).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from fam.common.validity import InvalidRun, InvalidRunClass

READY_TIMEOUT_SECONDS = 60.0
STOP_TIMEOUT_SECONDS = 20.0


class AgentProcess:
    def __init__(
        self,
        *,
        user_id: str,
        password: str,
        homeserver: str,
        experiment: str,
        run_id: str,
        room_id: str,
        telemetry: Path,
        state_dir: Path,
        body_bytes: int | None = None,
    ) -> None:
        self.user_id = user_id
        self.password = password
        self.homeserver = homeserver
        self.experiment = experiment
        self.run_id = run_id
        self.room_id = room_id
        self.telemetry = telemetry
        self.state_dir = state_dir
        self.body_bytes = body_bytes
        self.process: asyncio.subprocess.Process | None = None

    @property
    def ready_file(self) -> Path:
        slug = self.user_id.lstrip("@").replace(":", "_")
        return self.state_dir / f"{slug}.ready"

    async def start(self) -> None:
        self.ready_file.unlink(missing_ok=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = "/app/src"
        argv = [
            sys.executable,
            "-m",
            "fam.agent",
            "--homeserver", self.homeserver,
            "--user", self.user_id,
            "--password", self.password,
            "--experiment", self.experiment,
            "--run-id", self.run_id,
            "--room-id", self.room_id,
            "--telemetry", str(self.telemetry),
            "--state-dir", str(self.state_dir),
        ]
        if self.body_bytes is not None:
            argv += ["--body-bytes", str(self.body_bytes)]

        self.process = await asyncio.create_subprocess_exec(*argv, env=env)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + READY_TIMEOUT_SECONDS
        while loop.time() < deadline:
            if self.ready_file.exists():
                return
            if self.process.returncode is not None:
                raise InvalidRun(
                    InvalidRunClass.RUNNER_IMPLEMENTATION_FAILURE,
                    f"agent process exited early with code {self.process.returncode}",
                )
            await asyncio.sleep(0.2)
        raise InvalidRun(
            InvalidRunClass.RUNNER_IMPLEMENTATION_FAILURE,
            f"agent did not become ready within {READY_TIMEOUT_SECONDS:.0f}s",
        )

    async def stop(self) -> None:
        if self.process is None or self.process.returncode is not None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=STOP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
        self.ready_file.unlink(missing_ok=True)
