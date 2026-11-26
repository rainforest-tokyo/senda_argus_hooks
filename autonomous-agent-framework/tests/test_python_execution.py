import asyncio
import os
from uuid import uuid4

import pytest

from senda_autonomous_agent import PythonExecutionConfig
from senda_autonomous_agent.python_execution import DockerPythonRunner, PythonExecutionError


pytestmark = pytest.mark.skipif(os.getenv("SENDA_TEST_DOCKER") != "1", reason="Enable to use local Docker")


def test_python_container_has_no_external_route_and_read_only_root(tmp_path):
    script = """
import errno
import json
import os
import socket

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
    try:
        probe.connect(('192.0.2.1', 9))
    except OSError as exc:
        offline = exc.errno == errno.ENETUNREACH
    else:
        offline = False
print(json.dumps({'offline': offline, 'read_only': bool(os.statvfs('/').f_flag & os.ST_RDONLY)}))
"""
    runner = DockerPythonRunner(PythonExecutionConfig(artifacts_dir=tmp_path))
    result = asyncio.run(runner.run("run_test", f"python_{uuid4().hex}", script, '{"facts": []}', ["offline"]))
    assert result == {"offline": True, "read_only": True}


@pytest.mark.parametrize(("script", "message"), [
    ("import time; time.sleep(30)", "timed out"),
    ("print('a' * 16384, flush=True)", "exceeded the output limit"),
])
def test_python_runner_limits_time_and_output(tmp_path, script, message):
    runner = DockerPythonRunner(PythonExecutionConfig(artifacts_dir=tmp_path, timeout_seconds=2, max_output_bytes=1024))
    artifact_id = f"python_{uuid4().hex}"
    with pytest.raises(PythonExecutionError, match=message):
        asyncio.run(runner.run("run_test", artifact_id, script, '{"facts": []}', []))
    directory = tmp_path / "run_test" / artifact_id
    assert (directory / "script.py").read_text() == script
    assert (directory / "stdout.log").stat().st_size <= 1024
    assert not (directory / "result.json").exists()
