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
from typing import Optional, Any, Dict

import pytest
import urllib3
from kubernetes import config, client
from kubernetes.client import V1DeleteOptions
from kubernetes.client.rest import ApiException

from sandcastle.api import Sandcastle
from sandcastle.utils import run_command, get_timestamp_now, clean_string

NON_EX_IMAGE = "non-ex-image"
PROJECT_NAME = "cyborg"
SANDBOX_IMAGE = "docker.io/usercont/sandcastle"
TEST_IMAGE_NAME = "docker.io/usercont/sandcastle-tests"
NAMESPACE = "myproject"
SANDCASTLE_MOUNTPOINT = "/sandcastle"


# exterminate!
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@pytest.fixture()
def init_openshift_deployer():
    return Sandcastle(image_reference=NON_EX_IMAGE, k8s_namespace_name=PROJECT_NAME)


@pytest.fixture(scope="session")
def build_now():
    """ build a container image with current sandcastle checkout """
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


# TODO: refactor into a class
def run_test_within_pod(
    test_path: str, with_pv_at: Optional[str] = None, new_namespace: bool = False
):
    """
    run selected test from within an openshift pod

    :param test_path: relative path to the test
    :param with_pv_at: path to PV within the pod
    :param new_namespace: create new namespace and pass it via env var
    """
    config.load_kube_config()
    configuration = client.Configuration()
    assert configuration.api_key
    api = client.CoreV1Api(client.ApiClient(configuration))

    pod_name = f"test-orchestrator-{get_timestamp_now()}"

    cont_cmd = [
        "bash",
        "-c",
        "ls -lha "
        "&& id "
        "&& pytest-3 --collect-only"
        f"&& pytest-3 -vv -l -p no:cacheprovider {test_path}",
    ]

    container: Dict[str, Any] = {
        "image": TEST_IMAGE_NAME,
        "name": pod_name,
        "tty": True,  # corols
        "command": cont_cmd,
        "imagePullPolicy": "Never",
        "env": [],
    }

    test_namespace = None
    if new_namespace:
        test_namespace = f"sandcastle-tests-{get_timestamp_now()}"
        c = ["oc", "new-project", test_namespace]
        run_command(c)
        c = [
            "oc",
            "adm",
            "-n",
            test_namespace,
            "policy",
            "add-role-to-user",
            "edit",
            f"system:serviceaccount:{NAMESPACE}:default",
        ]
        run_command(c)
        container["env"] += [
            {"name": "SANDCASTLE_TESTS_NAMESPACE", "value": test_namespace}
        ]

    spec = {"containers": [container], "restartPolicy": "Never"}

    pod_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": pod_name},
        "spec": spec,
    }
    if with_pv_at:
        cleaned_test_name = clean_string(test_path)
        ts = get_timestamp_now()
        volume_name = f"{cleaned_test_name}-{ts}-vol"[-63:]
        claim_name = f"{cleaned_test_name}-{ts}-pvc"[-63:]
        container["env"] = [{"name": "SANDCASTLE_PVC", "value": claim_name}]
        pvc_dict = {
            "kind": "PersistentVolumeClaim",
            "spec": {
                "accessModes": ["ReadWriteMany"],
                "resources": {"requests": {"storage": "1Gi"}},
            },
            "apiVersion": "v1",
            "metadata": {"name": claim_name},
        }
        api.create_namespaced_persistent_volume_claim(NAMESPACE, pvc_dict)
        container["volumeMounts"] = [{"mountPath": with_pv_at, "name": volume_name}]
        spec["volumes"] = [
            {"name": volume_name, "persistentVolumeClaim": {"claimName": claim_name}}
        ]
    try:
        api.delete_namespaced_pod(pod_name, NAMESPACE, body=V1DeleteOptions())
    except ApiException as ex:
        if ex.status != 404:
            raise

    try:
        api.create_namespaced_pod(body=pod_manifest, namespace=NAMESPACE)
        counter = 15
        while True:
            if counter < 0:
                raise RuntimeError("Pod did not start on time.")
            info = api.read_namespaced_pod(pod_name, NAMESPACE)
            if info.status.phase == "Running":
                break
            time.sleep(2.0)
            counter -= 1
        print(
            api.read_namespaced_pod_log(name=pod_name, namespace=NAMESPACE, follow=True)
        )
        counter = 15
        while True:
            if counter < 0:
                raise RuntimeError("Pod did not finish on time.")
            info = api.read_namespaced_pod(pod_name, NAMESPACE)
            if info.status.phase == "Succeeded":
                break
            if info.status.phase == "Failed":
                raise RuntimeError("Test failed")
            time.sleep(2.0)
            counter -= 1
    finally:
        print(
            api.read_namespaced_pod_log(name=pod_name, namespace=NAMESPACE, follow=True)
        )
        api.delete_namespaced_pod(pod_name, NAMESPACE, body=V1DeleteOptions())
        if new_namespace:
            run_command(["oc", "delete", "project", test_namespace])
        if with_pv_at:
            api.delete_namespaced_persistent_volume_claim(
                name=claim_name, namespace=NAMESPACE, body=V1DeleteOptions()
            )
