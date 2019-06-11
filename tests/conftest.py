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
import time
from pathlib import Path

import pytest
from kubernetes import config, client
from kubernetes.client import V1DeleteOptions
from kubernetes.client.rest import ApiException

from sandcastle.api import Sandcastle
from sandcastle.utils import run_command

NON_EX_IMAGE = "non-ex-image"
PROJECT_NAME = "cyborg"
SANDBOX_IMAGE = "docker.io/usercont/sandcastle"
TEST_IMAGE_NAME = "docker.io/usercont/sandcastle-tests"
POD_NAME = "test-orchestrator"
NAMESPACE = "myproject"


@pytest.fixture()
def init_openshift_deployer():
    return Sandcastle(image_reference=NON_EX_IMAGE, k8s_namespace_name=PROJECT_NAME)


def run_test_within_pod(test_path: str):
    """ run selected test from within an openshift pod """
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
                        "bash",
                        "-c",
                        "ls -lha "
                        "&& id "
                        "&& pytest-3 --collect-only"
                        f"&& pytest-3 -vv -l -p no:cacheprovider {test_path}",
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


def build_now():
    """ build a container image with sandcastle """
    project_root = Path(__file__).parent.parent
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
