# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT


class SandcastleException(Exception):
    """Something went wrong."""


class SandcastleTimeoutReached(SandcastleException):
    """Timeout was reached while sandboxing"""


class SandcastleExecutionError(SandcastleException):
    """There was an issue during sandbox execution."""


class SandcastleCommandFailed(SandcastleException):
    """The command executed in sandbox failed."""

    def __init__(self, output: str, reason: str, rc: int):
        """
        :param output: output of the command
        :param reason: reason the command failed
        :param rc: return code
        """
        self.output: str = output
        self.reason: str = reason
        self.rc: int = rc

    def __repr__(self):
        return f"SandcastleCommandFailed(reason={self.reason}, rc={self.rc})\n{self.output}"

    def __str__(self):
        return f"Command failed (rc={self.rc}, reason={self.reason})\n{self.output}"
