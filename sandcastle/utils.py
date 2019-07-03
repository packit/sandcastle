# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
import datetime
import logging
import os
import shlex
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def run_command(cmd, cwd=None, output=False):
    if not isinstance(cmd, list):
        cmd = shlex.split(cmd)

    cwd = cwd or os.getcwd()

    logger.debug("%s", cmd)

    shell = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        cwd=cwd,
        universal_newlines=True,
    )

    if shell.stdout:
        logger.info("%s", shell.stdout)

    if shell.returncode != 0:
        logger.error(f"{shell.stderr}")
        raise Exception(f"{shell.args!r} failed with {shell.stderr!r}")
    else:
        success = True

    if not output:
        return success

    return shell.stdout


class GeneratorFormatter(logging.Formatter):
    def format(self, record):
        if record.levelno == logging.INFO:
            self._style._fmt = "%(message)s"
        elif record.levelno > logging.INFO:
            self._style._fmt = "%(levelname)-8s %(message)s"
        else:  # debug
            self._style._fmt = (
                "%(asctime)s.%(msecs).03d %(filename)-17s %(levelname)-6s %(message)s"
            )
        return logging.Formatter.format(self, record)


def set_logging(
    logger_name="sandcastle",
    level=logging.INFO,
    handler_class=logging.StreamHandler,
    handler_kwargs=None,
    date_format="%H:%M:%S",
):
    """
    Set personal logger for this library.
    :param logger_name: str, name of the logger
    :param level: int, see logging.{DEBUG,INFO,ERROR,...}: level of logger and handler
    :param handler_class: logging.Handler instance, default is StreamHandler (/dev/stderr)
    :param handler_kwargs: dict, keyword arguments to handler's constructor
    :param date_format: str, date style in the logs
    """
    if level != logging.NOTSET:
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)

        # do not readd handlers if they are already present
        if not [x for x in logger.handlers if isinstance(x, handler_class)]:
            handler_kwargs = handler_kwargs or {}
            handler = handler_class(**handler_kwargs)
            handler.setLevel(level)

            formatter = GeneratorFormatter(None, date_format)
            handler.setFormatter(formatter)
            logger.addHandler(handler)


def get_timestamp_now() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S%f")


def clean_string(s: str) -> str:
    """
    a DNS-1123 subdomain must consist of lower case alphanumeric characters, '-' or '.',
    and must start and end with an alphanumeric character (
    e.g. 'example.com', regex used for validation is
    '[a-z0-9]( [-a-z0-9]*[a-z0-9])?(\\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*'

    'must be no more than 63 characters'
    """
    return (
        s.replace("/", "-")
        .replace(":", "-")
        .replace("_", "-")
        .replace(".", "-")
        .replace("--", "-")[-63:]
    )


def purge_dir_content(di: Path):
    """ remove everything in the dir but not the dir itself """
    dir_items = list(di.iterdir())
    if dir_items:
        logger.info("the dir is not empty")
        logger.debug("content of the dir: %s" % dir_items)
    for item in dir_items:
        if item.is_file():
            item.unlink()
        else:
            shutil.rmtree(item)
