# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock
from kubernetes.client.models.v1_pod import V1Pod
from kubernetes.client.models.v1_pod_status import V1PodStatus

from kubernetes.client.rest import ApiException
from sandcastle import Sandcastle, SandcastleExecutionError


@pytest.fixture()
def pod_not_deployed():
    return {
        "kind": "Status",
        "apiVersion": "v1",
        "metadata": {},
        "status": "Failure",
        "message": 'pods "sandcastle" not found',
        "reason": "NotFound",
        "details": {"name": "sandcastle", "kind": "pods"},
        "code": 404,
    }


@pytest.fixture()
def pod_json_deployed():
    return {
        "kind": "Pod",
        "apiVersion": "v1",
        "metadata": {
            "name": "sandcastle-5-qh85r",
            "generateName": "sandcastle-5-",
            "namespace": "PROJECT_NAME",
            "selfLink": "/api/v1/namespaces/PROJECT_NAME/pods/sandcastle-5-qh85r",
            "uid": "6d5ad24a-81d6-11e9-a2fa-fa163ed2928c",
            "resourceVersion": "488154842",
            "creationTimestamp": "2019-05-29T05:55:56Z",
            "labels": {
                "deployment": "sandcastle-5",
                "deploymentconfig": "sandcastle",
                "io.openshift.tags": "sandcastle",
            },
            "annotations": {
                "openshift.io/deployment-config.latest-version": "5",
                "openshift.io/deployment-config.name": "sandcastle",
                "openshift.io/deployment.name": "sandcastle-5",
                "openshift.io/scc": "restricted",
            },
            "ownerReferences": [
                {
                    "apiVersion": "v1",
                    "kind": "ReplicationController",
                    "name": "sandcastle-5",
                    "uid": "f4204b91-5c38-11e9-ac31-fa163ed2928c",
                    "controller": "true",
                    "blockOwnerDeletion": "true",
                }
            ],
        },
        "spec": {
            "volumes": [
                {
                    "name": "packit-sandcastle",
                    "persistentVolumeClaim": {"claimName": "claim.sandcastle"},
                }
            ],
            "containers": [
                {
                    "name": "sandcastle",
                    "image": "quay.io/packit/sandcastle",
                    "env": [
                        {
                            "name": "NAMESPACE",
                            "valueFrom": {
                                "configMapKeyRef": {"name": "common", "key": "project"}
                            },
                        }
                    ],
                    "resources": {
                        "limits": {"cpu": "400m", "memory": "800Mi"},
                        "requests": {"cpu": "200m", "memory": "400Mi"},
                    },
                    "volumeMounts": [
                        {
                            "name": "packit-sandcastle",
                            "mountPath": "/tmp/packit-sandcastle",
                            "readOnly": False,
                        }
                    ],
                    "terminationMessagePath": "/dev/termination-log",
                    "terminationMessagePolicy": "File",
                    "imagePullPolicy": "Always",
                }
            ],
            "restartPolicy": "Always",
            "terminationGracePeriodSeconds": 30,
            "dnsPolicy": "ClusterFirst",
            "nodeSelector": {"region": "compute"},
            "serviceAccountName": "sandcastle",
            "serviceAccount": "sandcastle",
            "imagePullSecrets": [{"name": "sandcastle-dockercfg-4bqcm"}],
            "schedulerName": "default-scheduler",
            "tolerations": [
                {
                    "key": "node.kubernetes.io/memory-pressure",
                    "operator": "Exists",
                    "effect": "NoSchedule",
                }
            ],
        },
        "status": {
            "phase": "Running",
            "startTime": "2019-05-29T05:55:56Z",
            "containerStatuses": [
                {
                    "name": "sandcastle",
                    "state": {"running": {"startedAt": "2019-05-29T05:56:29Z"}},
                    "lastState": {},
                    "ready": "true",
                    "restartCount": 0,
                    "image": "quay.io/packit/sandcastle:latest",
                    "imageID": "docker-pullable://quay.io/packit/"
                    "sandcastle@sha256:51289119edf387c47ed149"
                    "eb3382c23f4115bc343adcaaa6e1731d269b6ec70a",
                    "containerID": "docker://201ad777bb6d36077590fed8796"
                    "bcd6170a62833c124467a1ffa2af4c60f1272",
                }
            ],
            "qosClass": "Burstable",
        },
    }


@pytest.fixture()
def pod_already_deployed(pod_json_deployed):
    raise ApiException(status=200, reason="Already exists")


@pytest.fixture()
def create_pod():
    return {}


@pytest.fixture()
def delete_pod():
    return {}


def test_is_pod_already_deployed(init_openshift_deployer):
    od = init_openshift_deployer
    flexmock(od).should_receive("get_pod").and_raise(
        ApiException(status=200, reason="POD already exists")
    )
    with pytest.raises(SandcastleExecutionError):
        od.is_pod_already_deployed()


def test_pod_not_deployed(init_openshift_deployer, pod_not_deployed):
    od = init_openshift_deployer
    flexmock(od).should_receive("get_pod").and_return(pod_not_deployed)
    assert od.is_pod_already_deployed()


def test_pod_scheduled(init_openshift_deployer, pod_not_deployed):
    od = init_openshift_deployer
    flexmock(od).should_receive("get_pod").and_raise(
        ApiException(
            status=404,
        )
    ).and_return(V1Pod(status=V1PodStatus(phase="Running"))).and_return(
        V1Pod(status=V1PodStatus(phase="Succeeded"))
    )
    flexmock(od.api).should_receive("create_namespaced_pod").and_raise(
        ApiException(status="403")
    ).and_return(1)
    od.deploy_pod(["false"])


@pytest.mark.parametrize(
    "env_dict,env_image_vars",
    [
        (
            {"DOWNSTREAM_IMAGE": "FOOBAR"},
            [{"name": "DOWNSTREAM_IMAGE", "value": "FOOBAR"}],
        ),
        ({}, []),
        ({"NAME": ""}, [{"name": "NAME", "value": ""}]),
        ({"NAME": None}, [{"name": "NAME", "value": ""}]),
        ({"NAME": [("a", "b")]}, [{"name": "NAME", "value": "[('a', 'b')]"}]),
    ],
)
def test_env_image_vars(env_dict, env_image_vars):
    assert Sandcastle.build_env_image_vars(env_dict=env_dict) == env_image_vars


def test_manifest(init_openshift_deployer):
    sandcastle: Sandcastle = init_openshift_deployer
    assert sandcastle
    KEY = "UPSTREAM_NAME"
    VALUE = "COLIN"
    expected_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": sandcastle.pod_name},
        "spec": {
            "automountServiceAccountToken": False,
            "containers": [
                {
                    "image": sandcastle.image_reference,
                    "name": sandcastle.pod_name,
                    "env": [{"name": KEY, "value": VALUE}],
                    "imagePullPolicy": "IfNotPresent",
                    "resources": {
                        "limits": {"memory": "768Mi"},
                        "requests": {"memory": "512Mi"},
                    },
                }
            ],
            "restartPolicy": "Never",
        },
    }
    sandcastle.env_vars = {KEY: VALUE}
    sandcastle.set_pod_manifest()
    assert sandcastle.pod_manifest == expected_manifest
