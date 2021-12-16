# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import time
from subprocess import check_output
from typing import Optional, Any, Dict

import pytest
import urllib3
from kubernetes import client
from kubernetes.client import V1DeleteOptions
from kubernetes.client.rest import ApiException
from kubernetes.config import new_client_from_config

from sandcastle.api import Sandcastle
from sandcastle.utils import run_command, get_timestamp_now, clean_string

NON_EX_IMAGE = "non-ex-image"
PROJECT_NAME = "cyborg"
SANDBOX_IMAGE = "quay.io/packit/sandcastle:prod"
TEST_IMAGE_BASENAME = "sandcastle-tests"
TEST_IMAGE_NAME = f"quay.io/packit/{TEST_IMAGE_BASENAME}"
NAMESPACE = "myproject"
SANDCASTLE_MOUNTPOINT = "/sandcastle"
PACKIT_SRPM_CMD = ["packit", "--debug", "srpm"]


# exterminate!
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@pytest.fixture()
def init_openshift_deployer():
    return Sandcastle(image_reference=NON_EX_IMAGE, k8s_namespace_name=PROJECT_NAME)


def enable_user_access_namespace(user: str, namespace: str):
    c = ["oc", "adm", "-n", namespace, "policy", "add-role-to-user", "edit", user]
    run_command(c)


def print_pod_logs(api: client.CoreV1Api, pod: str, namespace: str):
    print(api.read_namespaced_pod_log(name=pod, namespace=namespace, follow=True))


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
    # this will connect to the cluster you have active right now - see `oc status`
    current_context = check_output(["oc", "config", "current-context"]).strip().decode()
    api_client = new_client_from_config(context=current_context)
    api = client.CoreV1Api(api_client)

    # you need this for minishift; or do `eval $(minishift docker-env) && make build`
    # # FIXME: get this from env or cli (`minishift openshift registry`)
    # also it doesn't work for me right now, unable to reach minishift's docker registry
    # registry = "172.30.1.1:5000"
    # openshift_image_name = f"{registry}/myproject/{TEST_IMAGE_BASENAME}"
    # # {'authorization': 'Bearer pRW5rGmqgBREnCeVweLcHbEhXvluvG1cImWfIrxWJ2A'}
    # api_token = list(api_client.configuration.api_key.values())[0].split(" ")[1]
    # check_call(["docker", "login", "-u", "developer", "-p", api_token, registry])
    # check_call(["docker", "tag", TEST_IMAGE_NAME, openshift_image_name])
    # check_call(["docker", "push", openshift_image_name])

    pod_name = f"test-orchestrator-{get_timestamp_now()}"

    cont_cmd = [
        "bash",
        "-c",
        "~/setup_env_in_openshift.sh "
        "&& ls -lha && pwd && id "
        f"&& pytest-3 -vv -l -p no:cacheprovider {test_path}",
    ]

    container: Dict[str, Any] = {
        "image": TEST_IMAGE_NAME,  # make sure this is correct
        "name": pod_name,
        "tty": True,  # corols
        "command": cont_cmd,
        "imagePullPolicy": "Never",
        "env": [],
    }

    user = f"system:serviceaccount:{NAMESPACE}:default"
    enable_user_access_namespace(user, NAMESPACE)
    test_namespace = None
    if new_namespace:
        test_namespace = f"sandcastle-tests-{get_timestamp_now()}"
        c = ["oc", "new-project", test_namespace]
        run_command(c)
        enable_user_access_namespace(user, test_namespace)
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
            elif info.status.phase == "Failed":
                print_pod_logs(api, pod_name, NAMESPACE)
                raise RuntimeError("The pod failed to start.")
            time.sleep(2.0)
            counter -= 1
        print_pod_logs(api, pod_name, NAMESPACE)
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
        print_pod_logs(api, pod_name, NAMESPACE)
        api.delete_namespaced_pod(pod_name, NAMESPACE, body=V1DeleteOptions())
        if new_namespace:
            run_command(["oc", "delete", "project", test_namespace])
        if with_pv_at:
            api.delete_namespaced_persistent_volume_claim(
                name=claim_name, namespace=NAMESPACE, body=V1DeleteOptions()
            )
