from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from pydantic import JsonValue

from .config import PythonExecutionConfig
from .contracts import Fact


PYTHON_TRANSFORM_API = "transform_with_python"
MAX_INPUT_BYTES = 262_144

# Runs as PID 1 inside the container. Keep a deadline even if the Docker client disconnects.
BOOTSTRAP = """
import json
from pathlib import Path
import subprocess
import sys

job = json.load(sys.stdin)
Path('input.json').write_text(job['input_json'], encoding='utf-8')
Path('script.py').write_text(job['code'], encoding='utf-8')
try:
    result = subprocess.run(
        [sys.executable, '-I', '-B', 'script.py'], stdin=subprocess.DEVNULL, timeout=int(sys.argv[1]),
        env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/job', 'LANG': 'C.UTF-8'},
    )
except subprocess.TimeoutExpired:
    print('Python execution timed out', file=sys.stderr)
    sys.exit(124)
sys.exit(result.returncode)
"""


class PythonExecutionError(ValueError):
    def __init__(self, message: str, *, exit_code: int | None = None, detail: str = ""):
        super().__init__(message)
        self.exit_code = exit_code
        self.detail = detail


class DockerPythonRunner:
    def __init__(self, config: PythonExecutionConfig):
        self.config = config

    @staticmethod
    def prepare_input(facts: list[Fact]) -> str:
        try:
            input_json = json.dumps({"facts": [fact.model_dump(mode="json") for fact in facts]}, allow_nan=False)
        except (ValueError, RecursionError) as error:
            raise PythonExecutionError("Selected facts cannot be encoded as JSON") from error
        if len(input_json) > MAX_INPUT_BYTES:
            raise PythonExecutionError("Python input exceeds 256 KiB. Select a smaller set of facts.")
        return input_json

    async def run(self, run_id: str, artifact_id: str, code: str, input_json: str,
                  required_keys: list[str]) -> dict[str, JsonValue]:
        directory = self.config.artifacts_dir / run_id / artifact_id
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        (directory / "script.py").write_text(code, encoding="utf-8")
        (directory / "input.json").write_text(input_json, encoding="utf-8")
        job = json.dumps({"code": code, "input_json": input_json}).encode()

        stdout, stderr = bytearray(), bytearray()
        try:
            exit_code = await self._execute(artifact_id, job, stdout, stderr)
        finally:
            (directory / "stdout.log").write_bytes(stdout)
            (directory / "stderr.log").write_bytes(stderr)
        if exit_code != 0:
            message = (
                "Python execution timed out" if exit_code == 124 else f"Python sandbox or script exited with status {exit_code}"
            )
            detail = stderr.decode(errors="replace")[-1024:].strip()
            raise PythonExecutionError(message, exit_code=exit_code, detail=detail)

        try:
            result = json.loads(stdout.decode("utf-8"))
            validated = json.dumps(result, ensure_ascii=False, allow_nan=False)
            encoded = (validated + "\n").encode("utf-8")
        except (ValueError, RecursionError) as error:
            raise PythonExecutionError("Python stdout must contain exactly one valid JSON object", exit_code=0) from error
        if not isinstance(result, dict):
            raise PythonExecutionError("Python stdout must contain a JSON object", exit_code=0)
        if missing := [key for key in required_keys if key not in result]:
            raise PythonExecutionError("Python result is missing required keys", exit_code=0, detail=", ".join(missing)[:1024])
        (directory / "result.json").write_bytes(encoded)
        return result

    async def _execute(self, container: str, job: bytes, stdout: bytearray, stderr: bytearray) -> int:
        docker = self._docker_command()
        environment = os.environ.copy()
        for variable in ("DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
            environment.pop(variable, None)
        # Stdin and stdout are the only channels for job data. Keep all container mounts off the host.
        try:
            process = await asyncio.create_subprocess_exec(
                *docker, "run", "--rm", "--interactive", "--name", container, "--pull=never",
                "--network=none", "--read-only", "--ipc=none", "--user=65534:65534", "--cap-drop=ALL",
                "--security-opt=no-new-privileges", "--pids-limit=16", "--memory=128m", "--memory-swap=128m",
                "--cpus=0.5", "--ulimit=core=0", "--log-driver=none",
                "--tmpfs=/job:rw,noexec,nosuid,nodev,size=8m,mode=1777", "--workdir=/job",
                "--env=HTTP_PROXY=", "--env=HTTPS_PROXY=", "--env=FTP_PROXY=", "--env=NO_PROXY=", "--env=ALL_PROXY=",
                "--env=http_proxy=", "--env=https_proxy=", "--env=ftp_proxy=", "--env=no_proxy=", "--env=all_proxy=",
                "--entrypoint=/usr/local/bin/python", "--", self.config.image,
                "-I", "-B", "-c", BOOTSTRAP, str(self.config.timeout_seconds),
                env=environment, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise PythonExecutionError("Docker is required for Python execution") from error
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        tasks = (
            asyncio.create_task(self._send_input(process.stdin, job)),
            asyncio.create_task(self._read_output(process.stdout, stdout)),
            asyncio.create_task(self._read_output(process.stderr, stderr)),
        )
        try:
            async with asyncio.timeout(self.config.timeout_seconds + 5):
                await asyncio.gather(*tasks)
                return await process.wait()
        except (TimeoutError, asyncio.CancelledError, PythonExecutionError, OSError) as error:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._stop_process(process)
            await self._remove_container(docker, environment, container)
            if isinstance(error, TimeoutError):
                raise PythonExecutionError("Python sandbox timed out") from error
            raise

    def _docker_command(self) -> tuple[str, str, str]:
        socket = self.config.docker_socket
        if socket is None:
            candidates = (Path.home() / ".docker/run/docker.sock", Path("/var/run/docker.sock"))
            socket = next((path for path in candidates if path.is_socket()), None)
        if socket is None or not socket.is_socket():
            raise PythonExecutionError(
                "Local Docker socket not available. Start Docker or set python_execution.docker_socket."
            )
        return "docker", "--host", f"unix://{socket.resolve()}"

    @staticmethod
    async def _send_input(stream: asyncio.StreamWriter, job: bytes) -> None:
        try:
            stream.write(job)
            await stream.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            stream.close()

    async def _read_output(self, stream: asyncio.StreamReader, destination: bytearray) -> None:
        while chunk := await stream.read(8192):
            available = self.config.max_output_bytes - len(destination)
            destination.extend(chunk[:available])
            if len(chunk) > available:
                raise PythonExecutionError("Python stdout or stderr exceeded the output limit")

    @staticmethod
    async def _stop_process(process: asyncio.subprocess.Process) -> None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.communicate()

    @classmethod
    async def _remove_container(cls, docker: tuple[str, str, str], environment: dict[str, str], container: str) -> None:
        try:
            cleanup = await asyncio.create_subprocess_exec(
                *docker, "rm", "--force", container, env=environment,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            return
        try:
            await asyncio.wait_for(cleanup.wait(), timeout=5)
        except TimeoutError:
            await cls._stop_process(cleanup)
        except asyncio.CancelledError:
            await cls._stop_process(cleanup)
            raise
