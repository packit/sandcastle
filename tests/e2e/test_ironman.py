# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.
#
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
import json
import os
from pathlib import Path

import pytest

from sandcastle import Sandcastle, VolumeSpec, SandcastleTimeoutReached, MappedDir
from sandcastle.exceptions import SandcastleCommandFailed
from sandcastle.utils import run_command, get_timestamp_now
from tests.conftest import (
    SANDBOX_IMAGE,
    NAMESPACE,
    run_test_within_pod,
    SANDCASTLE_MOUNTPOINT,
)


def test_basic_e2e_inside():
    o = Sandcastle(
        image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE, pod_name="lllollz"
    )
    try:
        o.run(command=["ls", "-lha"])
    finally:
        o.delete_pod()


def test_run_failure():
    o = Sandcastle(image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE)
    try:
        with pytest.raises(SandcastleCommandFailed) as ex:
            o.run(command=["ls", "/hauskrecht"])
        assert (
            ex.value.output
            == "ls: cannot access '/hauskrecht': No such file or directory\n"
        )
        assert isinstance(ex.value, SandcastleCommandFailed)
        assert "'exit_code': 2" in ex.value.reason
        assert "'reason': 'Error'" in ex.value.reason
        assert ex.value.rc == 2
    finally:
        o.delete_pod()


def test_exec_failure():
    o = Sandcastle(image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE)
    o.run()
    try:
        with pytest.raises(SandcastleCommandFailed) as ex:
            o.exec(command=["ls", "/hauskrecht"])
        assert (
            ex.value.output
            == "ls: cannot access '/hauskrecht': No such file or directory\n"
        )
        assert isinstance(ex.value, SandcastleCommandFailed)
        assert "2" in ex.value.reason
        assert "ExitCode" in ex.value.reason
        assert "NonZeroExitCode" in ex.value.reason
        assert ex.value.rc == 2
    finally:
        o.delete_pod()


@pytest.mark.skipif(
    "KUBERNETES_SERVICE_HOST" not in os.environ,
    reason="Not running in a pod, skipping.",
)
def test_dir_sync(tmpdir):
    p = Path("/asdqwe")
    vs = VolumeSpec(path=str(p), pvc_from_env="SANDCASTLE_PVC")

    o = Sandcastle(
        image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE, volume_mounts=[vs]
    )
    o.run()
    d = p.joinpath("dir")
    d.mkdir()
    d.joinpath("file").write_text("asd")
    try:
        o.exec(command=["bash", "-c", "ls -lha /asdqwe/dir/file"])
        o.exec(command=["bash", "-c", "[[ 'asd' == $(cat /asdqwe/dir/file) ]]"])
        o.exec(command=["bash", "-c", "mkdir /asdqwe/dir/d"])
        o.exec(command=["bash", "-c", "touch /asdqwe/dir/f"])
        assert Path("/asdqwe/dir/d").is_dir()
        assert Path("/asdqwe/dir/f").is_file()
    finally:
        o.delete_pod()


@pytest.mark.skipif(
    "KUBERNETES_SERVICE_HOST" not in os.environ,
    reason="Not running in a pod, skipping.",
)
def test_pod_sa_not_in_sandbox(tmpdir):
    o = Sandcastle(image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE)
    sa_path = "/var/run/secrets/kubernetes.io/serviceaccount"
    with pytest.raises(SandcastleCommandFailed) as e:
        o.run(command=["ls", "-lha", sa_path])
    try:
        assert (
            e.value.output.strip()
            == f"ls: cannot access '{sa_path}': No such file or directory"
        )
        assert e.value.rc == 2
    finally:
        o.delete_pod()


def test_exec_succ_pod(tmpdir):
    o = Sandcastle(image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE)
    # we mimic here that the pod has finished and we are still running commands inside
    o.run(command=["true"])
    try:
        with pytest.raises(SandcastleTimeoutReached) as e:
            o.exec(command=["true"])
        assert "timeout" in str(e.value)
    finally:
        o.delete_pod()


def test_md_multiple_exec(tmpdir):
    p = Path(tmpdir)
    m_dir = MappedDir(str(p), SANDCASTLE_MOUNTPOINT, with_interim_pvc=True)

    p.joinpath("stark").mkdir()
    p.joinpath("qwe").write_text("Hello, Tony!")

    o = Sandcastle(
        image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE, mapped_dir=m_dir
    )
    o.run()
    try:
        out = o.exec(command=["ls", "./qwe"])
        assert "qwe" in out
        o.exec(command=["touch", "./stark/asd"])
        assert p.joinpath("stark/asd").is_file()
        o.exec(command=["touch", "./zxc"])
        assert p.joinpath("zxc").is_file()
    finally:
        o.delete_pod()


def test_file_got_changed(tmpdir):
    m_dir = MappedDir(str(tmpdir), SANDCASTLE_MOUNTPOINT, with_interim_pvc=True)

    p = Path(m_dir.local_dir).joinpath("qwe")
    p.write_text("Hello, Tony!")

    o = Sandcastle(
        image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE, mapped_dir=m_dir
    )
    o.run()
    try:
        o.exec(command=["bash", "-c", "echo '\nHello, Tony Stark!' >>./qwe"])
        assert "Hello, Tony!\nHello, Tony Stark!\n" == p.read_text()
    finally:
        o.delete_pod()


@pytest.mark.parametrize(
    "git_url,branch",
    (
        ("https://github.com/packit-service/hello-world.git", "master"),
        ("https://github.com/TomasTomecek/packit.git", "sandz"),
    ),
)
def test_md_e2e(tmpdir, git_url, branch):
    t = Path(tmpdir)
    m_dir = MappedDir(str(t), SANDCASTLE_MOUNTPOINT, with_interim_pvc=True)

    run_command(["git", "clone", "-b", branch, git_url, t])

    o = Sandcastle(
        image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE, mapped_dir=m_dir
    )
    o.run()
    try:
        o.exec(command=["packit", "srpm"])
        assert list(t.glob("*.src.rpm"))
    finally:
        o.delete_pod()


def test_md_new_namespace(tmpdir):
    t = Path(tmpdir)
    m_dir = MappedDir(str(t), SANDCASTLE_MOUNTPOINT, with_interim_pvc=True)

    d = t.joinpath("dir")
    d.mkdir()
    d.joinpath("file").write_text("asd")

    # running within openshift
    namespace = os.getenv("SANDCASTLE_TESTS_NAMESPACE")
    if not namespace:
        # running on a host - you can't create new projects from inside a pod
        namespace = f"sandcastle-tests-{get_timestamp_now()}"
        c = ["oc", "new-project", namespace]
        run_command(c)

    try:
        o = Sandcastle(
            image_reference=SANDBOX_IMAGE,
            k8s_namespace_name=namespace,
            mapped_dir=m_dir,
        )
        o.run()
        try:
            o.exec(command=["ls", "-lha", f"./dir/file"])
            assert d.joinpath("file").read_text() == "asd"
            cmd = [
                "bash",
                "-c",
                "curl -skL https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT/metrics",
            ]
            out = o.exec(command=cmd)
            j = json.loads(out)
            # a small proof we are safe
            assert j["reason"] == "Forbidden"
        finally:
            o.delete_pod()
    finally:
        if not os.getenv("SANDCASTLE_TESTS_NAMESPACE"):
            run_command(["oc", "delete", "project", namespace])
            run_command(["oc", "project", NAMESPACE])


# To verify this:
# tar: lost+found: Cannot utime: Operation not permitted
# tar: lost+found: Cannot change mode to rwxr-sr-x: Operation not permitted
# tar: Exiting with failure status due to previous errors
def test_lost_found_is_ignored(tmpdir):
    t = Path(tmpdir)
    m_dir = MappedDir(str(t), SANDCASTLE_MOUNTPOINT)

    lf = t.joinpath("lost+found")
    lf.mkdir()
    t.joinpath("file").write_text("asd")

    o = Sandcastle(
        image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE, mapped_dir=m_dir
    )
    o.run()
    try:
        o.exec(command=["ls", "-lha", "./"])
        with pytest.raises(SandcastleCommandFailed) as ex:
            o.exec(command=["ls", f"./lost+found"])
        assert "No such file or directory" in str(ex.value)
    finally:
        o.delete_pod()


def test_changing_mode(tmpdir):
    t = Path(tmpdir)
    m_dir = MappedDir(str(t), SANDCASTLE_MOUNTPOINT)

    fi = t.joinpath("file")
    fi.write_text("asd")
    fi.chmod(mode=0o777)

    o = Sandcastle(
        image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE, mapped_dir=m_dir
    )
    o.run()
    try:
        out = o.exec(command=["stat", "-c", "%a", "./file"]).strip()
        assert "777" == out
        stat_oct = oct(fi.stat().st_mode)[-3:]
        assert stat_oct == "777"
    finally:
        o.delete_pod()


@pytest.mark.parametrize(
    "test_name,kwargs",
    (
        ("test_basic_e2e_inside", None),
        ("test_exec_failure", None),
        ("test_run_failure", None),
        ("test_dir_sync", {"with_pv_at": "/asdqwe"}),
        ("test_pod_sa_not_in_sandbox", None),
        ("test_exec_succ_pod", None),
        ("test_md_multiple_exec", None),
        ("test_file_got_changed", None),
        ("test_md_e2e", None),
        ("test_lost_found_is_ignored", None),
        ("test_md_new_namespace", {"new_namespace": True}),
        ("test_changing_mode", None),
    ),
)
def test_from_pod(build_now, test_name, kwargs):
    """ initiate e2e: spawn a new openshift pod, from which every test case is being run """
    path = f"tests/e2e/test_ironman.py::{test_name}"
    kwargs = kwargs or {}
    run_test_within_pod(path, **kwargs)
