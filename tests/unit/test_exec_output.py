# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
from pathlib import Path

import pytest

from kubernetes.stream.ws_client import STDERR_CHANNEL, STDOUT_CHANNEL

from sandcastle import Sandcastle, SandcastleCommandFailed


class FakeWSClient:
    def __init__(self, error, final_stdout="", final_stderr=""):
        self.error = error
        self.final_stdout = final_stdout
        self.final_stderr = final_stderr
        self.reads = {STDOUT_CHANNEL: 0, STDERR_CHANNEL: 0}
        self.read_all_calls = 0
        self.closed = False
        self._open = True

    def is_open(self):
        return self._open

    def run_forever(self, timeout):
        self._open = False

    def read_channel(self, channel):
        if channel == STDOUT_CHANNEL:
            output = "out\n" if self.reads[channel] == 0 else self.final_stdout
            self.reads[channel] += 1
            return output
        if channel == STDERR_CHANNEL:
            output = "err\n" if self.reads[channel] == 0 else self.final_stderr
            self.reads[channel] += 1
            return output
        return self.error

    def read_all(self):
        output = "err\nout\n" if self.read_all_calls == 0 else ""
        self.read_all_calls += 1
        return output

    def close(self):
        self.closed = True


def make_sandcastle(ws_client):
    sandcastle = Sandcastle.__new__(Sandcastle)
    sandcastle.mapped_dir = None
    sandcastle.is_pod_running = lambda: True
    sandcastle._prepare_exec = lambda *args, **kwargs: (
        Path("/tmp/work"),
        Path("/tmp/script.sh"),
    )
    sandcastle._do_exec = lambda *args, **kwargs: ws_client
    return sandcastle


def test_exec_with_output_separates_channels():
    ws_client = FakeWSClient('{"status":"Success"}')
    sandcastle = make_sandcastle(ws_client)

    output = sandcastle.exec_with_output(["printf", "out"])

    assert output.stdout == "out\n"
    assert output.stderr == "err\n"
    combined_ws_client = FakeWSClient('{"status":"Success"}')
    sandcastle = make_sandcastle(combined_ws_client)
    assert sandcastle.exec(["printf", "out"]) == "err\nout\n"
    assert ws_client.closed
    assert combined_ws_client.closed


def test_exec_with_output_preserves_failure_metadata_and_cleanup():
    metadata = {
        "status": "Failure",
        "details": {"causes": [{"reason": "ExitCode", "message": "7"}]},
    }
    ws_client = FakeWSClient(json.dumps(metadata))
    sandcastle = make_sandcastle(ws_client)

    with pytest.raises(SandcastleCommandFailed) as caught:
        sandcastle.exec_with_output(["false"])

    assert caught.value.rc == 7
    assert caught.value.output == "out\nerr\n"
    assert ws_client.closed


def test_exec_preserves_failure_metadata():
    metadata = {
        "status": "Failure",
        "details": {"causes": [{"reason": "ExitCode", "message": "7"}]},
    }
    ws_client = FakeWSClient(json.dumps(metadata))
    sandcastle = make_sandcastle(ws_client)

    with pytest.raises(SandcastleCommandFailed) as caught:
        sandcastle.exec(["false"])

    assert caught.value.rc == 7
    assert ws_client.closed


def test_exec_with_output_drains_buffered_output_after_close():
    ws_client = FakeWSClient('{"status":"Success"}', final_stdout="late\n")
    sandcastle = make_sandcastle(ws_client)

    output = sandcastle.exec_with_output(["printf", "out"])

    assert output.stdout == "out\nlate\n"
