# build an image using current state of this code
# create pod to run pod
# check the pod
# pod
import os
import time
from pathlib import Path

import pytest
from kubernetes import config, client
from kubernetes.client import V1DeleteOptions
from kubernetes.client.rest import ApiException

from generator.deploy_openshift_pod import OpenshiftDeployer, VolumeSpec
from generator.utils import run_command


GENERATOR_IMAGE_NAME = "docker.io/usercont/sandbox"
POD_NAME = "generator-pod"
NAMESPACE = "myproject"


def build_now():
    project_root = Path(__file__).parent.parent.parent
    run_command(["docker", "build", "-t", GENERATOR_IMAGE_NAME, str(project_root)])


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
                    "image": GENERATOR_IMAGE_NAME,
                    "name": POD_NAME,
                    "tty": True,  # corols
                    "command": [
                        "pytest-3",
                        "-vv",
                        "-l",
                        "-p",
                        "no:cacheprovider",
                        "-k",
                        test_name,
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
            if info.status.phase != "Running":
                break
            time.sleep(2.0)
            counter -= 1
        print(
            api.read_namespaced_pod_log(name=POD_NAME, namespace=NAMESPACE, follow=True)
        )
    finally:
        api.delete_namespaced_pod(POD_NAME, NAMESPACE, body=V1DeleteOptions())


@pytest.mark.skipif(
    "KUBERNETES_SERVICE_HOST" not in os.environ,
    reason="Not running in a pod, skipping.",
)
def test_basic_e2e_inside():
    """ this part is meant to run inside an openshift pod """
    o = OpenshiftDeployer(
        image_reference="fedora:30", k8s_namespace_name=NAMESPACE, pod_name="lllollz"
    )
    o.run(command=["ls", "-lha"])


@pytest.mark.skipif(
    "KUBERNETES_SERVICE_HOST" not in os.environ,
    reason="Not running in a pod, skipping.",
)
def test_local_path_e2e_inside():
    """ this part is meant to run inside an openshift pod """
    v = VolumeSpec()
    v.local_dir = "/tmp/asd/"
    v.path = "/asd"

    p = Path(v.local_dir)
    p.mkdir()
    p.joinpath("qwe").write_text("Hello, Tony!")

    o = OpenshiftDeployer(
        image_reference="fedora:30",
        k8s_namespace_name=NAMESPACE,
        pod_name="lllollz2",
        volume_mounts=[v],
    )
    o.run(command=["ls", "/asd/qwe"])


@pytest.mark.parametrize(
    "test_name", ("test_basic_e2e_inside", "test_local_path_e2e_inside")
)
def test_basic_e2e(test_name):
    """ the most simple e2e test """
    build_now()
    deploy(test_name)

    # wait for the to finish
