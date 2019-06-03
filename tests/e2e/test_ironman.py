# build an image using current state of this code
# create pod to run pod
# check the pod
# pod
import time
from pathlib import Path

import pytest
from kubernetes import config, client
from kubernetes.client import V1DeleteOptions
from kubernetes.client.rest import ApiException

from generator.deploy_openshift_pod import OpenshiftDeployer, MappedDir
from generator.utils import run_command

SANDBOX_IMAGE = "docker.io/usercont/packit-generator"
TEST_IMAGE_NAME = "docker.io/usercont/packit-generator-test"
POD_NAME = "test-orchestrator"
NAMESPACE = "myproject"


def build_now():
    project_root = Path(__file__).parent.parent.parent
    run_command(
        [
            "docker",
            "build",
            "-t",
            TEST_IMAGE_NAME,
            "-f",
            "Dockerfile.tests",
            str(project_root),
        ]
    )


def deploy(test_name: str):
    config.load_kube_config()
    configuration = client.Configuration()
    assert configuration.api_key
    api = client.CoreV1Api(client.ApiClient(configuration))
    pod_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": POD_NAME},
        "spec": {
            "containers": [
                {
                    "image": TEST_IMAGE_NAME,
                    "name": POD_NAME,
                    "tty": True,  # corols
                    "command": [
                        "pytest-3",
                        "-vv",
                        "-l",
                        "-p",
                        "no:cacheprovider",
                        "-k",
                        f"tests/e2e/test_ironman.py::{test_name}",
                    ],
                    "imagePullPolicy": "Never",
                }
            ],
            "restartPolicy": "Never",
        },
    }
    try:
        api.delete_namespaced_pod(POD_NAME, NAMESPACE, body=V1DeleteOptions())
    except ApiException as ex:
        if ex.status != 404:
            raise

    try:
        api.create_namespaced_pod(body=pod_manifest, namespace=NAMESPACE)
        counter = 15
        while True:
            if counter < 0:
                raise RuntimeError("Pod did not start on time.")
            info = api.read_namespaced_pod(POD_NAME, NAMESPACE)
            if info.status.phase == "Running":
                break
            time.sleep(2.0)
            counter -= 1
        print(
            api.read_namespaced_pod_log(name=POD_NAME, namespace=NAMESPACE, follow=True)
        )
        counter = 15
        while True:
            if counter < 0:
                raise RuntimeError("Pod did not finish on time.")
            info = api.read_namespaced_pod(POD_NAME, NAMESPACE)
            if info.status.phase == "Succeeded":
                break
            if info.status.phase == "Failed":
                raise RuntimeError("Test failed")
            time.sleep(2.0)
            counter -= 1
    finally:
        print(
            api.read_namespaced_pod_log(name=POD_NAME, namespace=NAMESPACE, follow=True)
        )
        api.delete_namespaced_pod(POD_NAME, NAMESPACE, body=V1DeleteOptions())


def test_basic_e2e_inside():
    """ this part is meant to run inside an openshift pod """
    o = OpenshiftDeployer(
        image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE, pod_name="lllollz"
    )
    try:
        o.run(command=["ls", "-lha"])
    finally:
        o.delete_pod()


def test_local_path_e2e_inside_w_exec(tmpdir):
    m_dir = MappedDir()
    m_dir.local_dir = str(tmpdir.mkdir("stark"))
    m_dir.path = "/tmp/stark"

    p = Path(m_dir.local_dir)
    p.joinpath("qwe").write_text("Hello, Tony!")

    o = OpenshiftDeployer(
        image_reference=SANDBOX_IMAGE, k8s_namespace_name=NAMESPACE, mapped_dirs=[m_dir]
    )
    o.run()
    try:
        o.exec(command=["ls", "/tmp/stark/qwe"])
    finally:
        o.delete_pod()


@pytest.mark.parametrize(
    "test_name", ("test_basic_e2e_inside", "test_local_path_e2e_inside")
)
def test_basic_e2e(test_name):
    """ initiate e2e: spawn a new openshift pod, from which every test case is being run """
    build_now()
    deploy(test_name)
