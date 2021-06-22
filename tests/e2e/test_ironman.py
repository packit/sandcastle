# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import json
import os
from pathlib import Path
from shutil import rmtree, copy2

import pytest
from flexmock import flexmock

from sandcastle import (
    Sandcastle,
    VolumeSpec,
    SandcastleTimeoutReached,
    MappedDir,
)
from sandcastle.exceptions import SandcastleCommandFailed
from sandcastle.utils import run_command, get_timestamp_now
from tests.conftest import (
    SANDBOX_IMAGE,
    NAMESPACE,
    run_test_within_pod,
    SANDCASTLE_MOUNTPOINT,
    PACKIT_SRPM_CMD,
)


def purge_dir_content(di: Path):
    """remove everything in the dir but not the dir itself"""
    dir_items = list(di.iterdir())
    if dir_items:
        print(f"Removing {di} content: {[i.name for i in dir_items]}")
    for item in dir_items:
        # symlink pointing to a dir is also a dir and a symlink
        if item.is_file() or item.is_symlink():
            item.unlink()
        else:
            rmtree(item)


def test_exec_env():
    o = Sandcastle(image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE)
    o.run()
    try:
        env_list = o.exec(
            command=["bash", "-c", "env"], env={"A": None, "B": "", "C": "c", "D": 1}
        )
        assert "A=\n" in env_list
        assert "B=\n" in env_list
        assert "C=c\n" in env_list
        assert "D=1\n" in env_list
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


@pytest.mark.parametrize(
    "cmd,should_fail",
    (
        (["ls", "/hauskrecht"], True),
        (["ls", "/haus*ht"], True),
        (["ls", "/etc/*wd"], True),
        (["ls", "/etc/passwd"], False),
        (["bash", "-c", "ls /etc/passwd"], False),
        (["bash", "-c", "ls /etc/*wd"], False),
    ),
)
def test_exec(cmd, should_fail):
    o = Sandcastle(image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE)
    o.run()
    try:
        if should_fail:
            with pytest.raises(SandcastleCommandFailed) as ex:
                o.exec(command=cmd)
            assert "No such file or directory\n" in ex.value.output
            assert "ls: cannot access " in ex.value.output
            assert isinstance(ex.value, SandcastleCommandFailed)
            assert "2" in ex.value.reason
            assert "ExitCode" in ex.value.reason
            assert "NonZeroExitCode" in ex.value.reason
            assert ex.value.rc == 2
        else:
            assert o.exec(command=cmd)
    finally:
        o.delete_pod()


@pytest.mark.skipif(
    "KUBERNETES_SERVICE_HOST" not in os.environ,
    reason="Not running in a pod, skipping.",
)
def test_dir_sync(tmp_path):
    p = Path("/asdqwe")
    vs = VolumeSpec(path=p, pvc_from_env="SANDCASTLE_PVC")

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
def test_pod_sa_not_in_sandbox(tmp_path):
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


def test_exec_succ_pod(tmp_path):
    o = Sandcastle(image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE)
    # we mimic here that the pod has finished and we are still running commands inside
    o.run(command=["true"])
    try:
        with pytest.raises(SandcastleTimeoutReached) as e:
            o.exec(command=["true"])
        assert "timeout" in str(e.value)
    finally:
        o.delete_pod()


def test_timeout(tmp_path: Path):
    """
    make sure exec runs are handled well when the pod times out
    and we provide output of the command in the exception
    """
    tmp_path.joinpath("test").write_text("test")
    m_dir = MappedDir(tmp_path, SANDCASTLE_MOUNTPOINT, with_interim_pvc=True)
    o = Sandcastle(
        image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE, mapped_dir=m_dir
    )
    # we are going to trick sandcastle into thinking we are using the default command
    # but we are not, b/c we don't want to wait 30 minutes for it time out in CI
    o.set_pod_manifest(["sleep", "7"])
    flexmock(Sandcastle).should_receive("set_pod_manifest").and_return(None).once()
    o.run()
    try:
        # sadly, openshift does not tell us in any way that the container finished
        # and that's why our exec got killed
        with pytest.raises(SandcastleCommandFailed) as e:
            # run a long running command and watch it get killed
            o.exec(command=["bash", "-c", "while true; do date; sleep 1; done"])
        assert "Command failed" in str(e.value)
        assert e.value.rc == 137
        assert e.value.output  # we wanna be sure there was some output
    finally:
        o.delete_pod()


@pytest.mark.parametrize(
    "cmd,should_fail",
    (
        (["ls", "/hauskrecht"], True),
        (["ls", "/haus*ht"], True),
        (["ls", "/etc/*wd"], True),
        (["ls", "/etc/passwd"], False),
        (["bash", "-c", "ls /etc/passwd"], False),
        (["bash", "-c", "ls /etc/*wd"], False),
    ),
)
def test_md_exec(tmp_path, cmd, should_fail):
    """
    make sure commands are exec'd properly in the sandbox with mapped dirs
    this is what we use in p-s with RW vols
    """
    # something needs to be inside
    tmp_path.joinpath("dummy.file").write_text("something")
    m_dir = MappedDir(tmp_path, SANDCASTLE_MOUNTPOINT, with_interim_pvc=True)

    o = Sandcastle(
        image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE, mapped_dir=m_dir
    )
    o.run()
    try:
        if should_fail:
            with pytest.raises(SandcastleCommandFailed) as ex:
                o.exec(command=cmd)
            assert "No such file or directory\n" in ex.value.output
            assert "ls: cannot access " in ex.value.output
            assert isinstance(ex.value, SandcastleCommandFailed)
            assert "2" in ex.value.reason
            assert "ExitCode" in ex.value.reason
            assert "NonZeroExitCode" in ex.value.reason
            assert ex.value.rc == 2
        else:
            o.exec(command=cmd)
    finally:
        o.delete_pod()


def test_md_multiple_exec(tmp_path):
    tmp_path.joinpath("stark").mkdir()
    tmp_path.joinpath("qwe").write_text("Hello, Tony!")
    m_dir = MappedDir(tmp_path, SANDCASTLE_MOUNTPOINT, with_interim_pvc=True)

    o = Sandcastle(
        image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE, mapped_dir=m_dir
    )
    o.run()
    try:
        out = o.exec(command=["ls", "./qwe"])
        assert "qwe" in out
        o.exec(command=["touch", "./stark/asd"])
        assert tmp_path.joinpath("stark/asd").is_file()
        o.exec(command=["touch", "./zxc"])
        zxc = tmp_path.joinpath("zxc")
        assert zxc.is_file()
        zxc.write_text("vbnm")
        assert "vbnm" == o.exec(command=["cat", "./zxc"])
        assert o.exec(command=["pwd"], cwd="stark/").rstrip("\n").endswith("/stark")
    finally:
        o.delete_pod()


def test_file_got_changed(tmp_path):
    m_dir = MappedDir(tmp_path, SANDCASTLE_MOUNTPOINT, with_interim_pvc=True)

    p = m_dir.local_dir.joinpath("qwe")
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


def test_command_long_output(tmp_path):
    o = Sandcastle(image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE)
    o.run()
    command = ["cat", "/etc/services"]
    try:
        out = o.exec(command=command)
    finally:
        o.delete_pod()
    # random strings from /etc/services: beginning, middle, end
    assert "ssh" in out
    assert "7687/tcp" in out
    assert "RADIX" in out


def test_user_is_set(tmp_path):
    """
    verify that $HOME is writable and commands are executed
    using a user which has an passwd entry
    """
    o = Sandcastle(image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE)
    o.run()
    try:
        assert o.exec(command=["getent", "passwd", "sandcastle"]).startswith(
            "sandcastle:x:"
        )
        assert o.exec(command=["id", "-u", "-n"]).strip() == "sandcastle"
        assert o.exec(
            command=[
                "bash",
                "-c",
                "touch ~/.i.want.to.write.to.home "
                "&& ls -l /home/sandcastle/.i.want.to.write.to.home",
            ]
        )
    finally:
        o.delete_pod()


@pytest.mark.parametrize(
    "git_url,branch,command",
    (
        (
            "https://github.com/packit/hello-world.git",
            "main",
            PACKIT_SRPM_CMD,
        ),
        ("https://github.com/packit/ogr.git", "main", PACKIT_SRPM_CMD),
        (
            "https://github.com/cockpit-project/cockpit-podman.git",
            "master",
            # this downloads megabytes of npm modules
            # and verifies we can run npm in sandcastle
            PACKIT_SRPM_CMD,
        ),
    ),
)
def test_md_e2e(tmp_path, git_url, branch, command):
    # running in k8s
    if "KUBERNETES_SERVICE_HOST" in os.environ:
        t = Path(SANDCASTLE_MOUNTPOINT, f"clone-{get_timestamp_now()}")
    else:
        t = tmp_path
    m_dir = MappedDir(t, SANDCASTLE_MOUNTPOINT, with_interim_pvc=True)

    run_command(["git", "clone", "-b", branch, git_url, t])

    o = Sandcastle(
        image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE, mapped_dir=m_dir
    )
    o.run()
    try:
        output = o.exec(command=command)
        print(output)
        assert list(t.glob("*.src.rpm"))
        o.exec(command=["packit", "--help"])

        with pytest.raises(SandcastleCommandFailed) as ex:
            o.exec(command=["bash", "-c", "echo 'I quit!'; exit 120"])
        e = ex.value
        assert "I quit!" in e.output
        assert 120 == e.rc
        assert "command terminated with non-zero exit code" in e.reason
    finally:
        o.delete_pod()


def test_md_new_namespace(tmp_path):
    m_dir = MappedDir(tmp_path, SANDCASTLE_MOUNTPOINT, with_interim_pvc=True)

    d = tmp_path.joinpath("dir")
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
            o.exec(command=["ls", "-lha", "./dir/file"])
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
def test_lost_found_is_ignored(tmp_path):
    tmp_path.joinpath("lost+found").mkdir()
    tmp_path.joinpath("file").write_text("asd")
    m_dir = MappedDir(tmp_path, SANDCASTLE_MOUNTPOINT)
    o = Sandcastle(
        image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE, mapped_dir=m_dir
    )

    o.run()
    try:
        o.exec(command=["ls", "-lha", "./"])
        with pytest.raises(SandcastleCommandFailed) as ex:
            o.exec(command=["ls", "./lost+found"])
        assert "No such file or directory" in str(ex.value)
    finally:
        o.delete_pod()


def test_changing_mode(tmp_path):
    # running in k8s
    if "KUBERNETES_SERVICE_HOST" in os.environ:
        t = Path(SANDCASTLE_MOUNTPOINT)
    else:
        t = tmp_path
    m_dir = MappedDir(t, SANDCASTLE_MOUNTPOINT)

    fi = t.joinpath("file")
    fi.write_text("asd")
    fi.chmod(mode=0o777)
    fi2 = t.joinpath("file2")
    fi2.write_text("qwe")
    fi2.chmod(mode=0o755)
    di = t.joinpath("dir")
    di.mkdir()
    di.chmod(mode=0o775)

    o = Sandcastle(
        image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE, mapped_dir=m_dir
    )
    o.run()
    try:
        out = o.exec(command=["stat", "-c", "%a", "./file"]).strip()
        assert "777" == out
        stat_oct = oct(fi.stat().st_mode)[-3:]
        assert stat_oct == "777"

        out = o.exec(command=["stat", "-c", "%a", "./file2"]).strip()
        assert "755" == out
        stat_oct = oct(fi2.stat().st_mode)[-3:]
        assert stat_oct == "755"

        out = o.exec(command=["stat", "-c", "%a", "./dir"]).strip()
        assert "775" == out
        stat_oct = oct(di.stat().st_mode)[-3:]
        assert stat_oct == "775"
    finally:
        purge_dir_content(t)
        o.delete_pod()


def test_packit_usecase(tmp_path: Path):
    """invoke sandcastle the same way packit invokes it"""
    tmp_vol_name = "foobor"
    tmp_vol_target_path = "/bort-simpson"
    tmp_dir = tmp_path.joinpath("dir")
    tmp_dir.mkdir()

    # let's put some files in there
    for fi in Path("/etc/systemd/").glob("*.conf"):
        copy2(fi, tmp_dir)

    md = MappedDir(
        local_dir=tmp_path,
        path=SANDCASTLE_MOUNTPOINT,
        with_interim_pvc=True,
    )
    vols = [VolumeSpec(path=tmp_vol_target_path, volume_name=tmp_vol_name)]
    s = Sandcastle(
        image_reference=SANDBOX_IMAGE,
        k8s_namespace_name=NAMESPACE,
        mapped_dir=md,
        volume_mounts=vols,
    )
    s.run()
    try:
        # making sure the files were correctly copied to the mapped dir
        out = s.exec(
            command=[
                "bash",
                "-c",
                f"ls -1 {SANDCASTLE_MOUNTPOINT}/quay-io-packit-sandcastle*/dir/",
            ]
        )
        # making sure we can create files in the temporary volume
        s.exec(command=["cp", "-a", "/etc/bashrc", tmp_vol_target_path])
    finally:
        s.delete_pod()
    assert "system.conf" in out


@pytest.mark.parametrize(
    "test_name,kwargs",
    (
        ("test_exec", None),
        ("test_exec_env", None),
        ("test_md_exec", None),
        ("test_run_failure", None),
        ("test_dir_sync", {"with_pv_at": "/asdqwe"}),
        ("test_pod_sa_not_in_sandbox", None),
        ("test_exec_succ_pod", None),
        ("test_timeout", None),
        ("test_md_multiple_exec", None),
        ("test_file_got_changed", None),
        ("test_md_e2e", {"with_pv_at": SANDCASTLE_MOUNTPOINT}),
        ("test_lost_found_is_ignored", None),
        ("test_md_new_namespace", {"new_namespace": True}),
        ("test_changing_mode", {"with_pv_at": SANDCASTLE_MOUNTPOINT}),
        ("test_command_long_output", None),
        ("test_user_is_set", None),
    ),
)
def test_from_pod(test_name, kwargs):
    """initiate e2e: spawn a new openshift pod, from which every test case is being run"""
    path = f"tests/e2e/test_ironman.py::{test_name}"
    kwargs = kwargs or {}
    run_test_within_pod(path, **kwargs)
