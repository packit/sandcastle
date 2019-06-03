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

"""
# Design

## Volumes

Our team have met to discuss volumes and sharing data b/w containers & pods. We came up with these solutions:

* `oc cp` - copy data using the `cp` command: very simple

* controller pod - dynamically create and deploy pods with volumes - very flexible, pretty complex

* claim - utilize persistent volume claim feature of k8s: let pods claim volumes
"""

import datetime
import logging
import os
import time
from typing import Dict, List, Optional

from kubernetes import config, client
from kubernetes.client import V1DeleteOptions
from kubernetes.client.rest import ApiException

from generator.exceptions import GeneratorDeployException
from generator.utils import run_command

logger = logging.getLogger(__name__)


class VolumeSpec:
    """
    Dataclass which defines volume configuration for the sandbox pod
    """

    # name of the volume to use
    name: str
    # path within the sandbox where the volume is meant to be mounted
    path: str
    # use and existing PersistentVolumeClaim
    pvc: str = ""


class MappedDir:
    """
    Copy local directory to the pod using `oc cp`
    """
    # path within the sandbox where the directory should be copied
    path: str
    # copy this local directory to sandbox (using `oc cp`)
    local_dir: str = ""


class OpenshiftDeployer(object):
    def __init__(
        self,
        image_reference: str,
        k8s_namespace_name: str,
        env_vars: Optional[Dict] = None,
        pod_name: Optional[str] = None,
        service_account_name: Optional[str] = None,
        volume_mounts: Optional[List[VolumeSpec]] = None,
        mapped_dirs: Optional[List[MappedDir]] = None,
    ):
        """
        :param image_reference: the pod will use this image
        :param k8s_namespace_name: name of the namespace to deploy into
        :param env_vars: additional environment variables to set in the pod
        :param pod_name: name the pod like this, if not specified, generate something long and ugly
        :param service_account_name: run the pod using this service account
        :param volume_mounts: set these volume mounts in the sandbox
        :param mapped_dirs, a list of mappings between a local dir which should be copied
                            to the sandbox, and then copied back once all the works is done
        """
        self.image_reference: str = image_reference
        self.service_account_name: Optional[str] = service_account_name

        self.env_vars = env_vars
        self.k8s_namespace_name = k8s_namespace_name

        self.cleaned_name = image_reference.replace("/", "-").replace(":", "-")
        if pod_name:
            self.pod_name = pod_name
        else:
            timestamp = datetime.datetime.now().strftime("-%Y%M%d-%H%M%S%f")
            self.pod_name = f"{self.cleaned_name}-{timestamp}"

        self.api: client.CoreV1Api = self.get_api_client()
        self.volume_mounts = volume_mounts
        self.mapped_dirs: List[MappedDir] = mapped_dirs or []

    def create_pod_manifest(self, command: Optional[List] = None) -> dict:
        env_image_vars = self.build_env_image_vars(self.env_vars)
        pod_manifest = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": self.pod_name},
            "spec": {
                "containers": [
                    {
                        "image": self.image_reference,
                        "name": self.pod_name,
                        "env": env_image_vars,
                        "pullPolicy": "IfNotPresent",
                        # "volumeMounts": [
                        #     {"mountPath": self.volume_dir, "name": "packit-generator"}
                        # ],
                    }
                ],
                "restartPolicy": "Never",
                # "volumes": [
                #     {
                #         "name": "packit-generator",
                #         "persistentVolumeClaim": {"claimName": "claim.packit"},
                #     }
                # ],
            },
        }
        if command:
            pod_manifest["spec"]["containers"][0]["command"] = command
        if self.service_account_name:
            pod_manifest["spec"]["serviceAccountName"] = self.service_account_name
        if self.volume_mounts:
            volume_mounts = []
            pod_manifest["spec"]["containers"][0]["volumeMounts"] = volume_mounts
            for vol in self.volume_mounts:
                volume_mounts.append({"mountPath": vol.path, "name": vol.name})
            volumes = []
            pod_manifest["spec"]["volumes"] = volumes
            for vol in self.volume_mounts:
                if vol.pvc:
                    di = {
                        "name": vol.name,
                        "persistentVolumeClaim": {"claimName": vol.pvc},
                    }
                elif vol.name:
                    # will this work actually if the volume is created up front?
                    di = {
                        "name": vol.name,
                    }
                else:
                    raise RuntimeError(
                        "Please specified either volume.pvc or volume.name"
                    )
                volumes.append(di)

        return pod_manifest

    @staticmethod
    def build_env_image_vars(env_dict: Dict) -> List:
        env_image_vars = []
        if not env_dict:
            return []
        for key, value in env_dict.items():
            env_image_vars.append({"name": key, "value": value or ""})
        return env_image_vars

    @staticmethod
    def get_api_client() -> client.CoreV1Api:
        logger.debug("Initialize kubernetes client")
        config.load_incluster_config()
        configuration = client.Configuration()
        assert configuration.api_key
        return client.CoreV1Api(client.ApiClient(configuration))

    def get_response_from_pod(self) -> Dict:
        """
        Read info from a pod in a namespace
        :return: JSON Dict
        """
        return self.api.read_namespaced_pod(
            name=self.pod_name, namespace=self.k8s_namespace_name
        )

    def delete_pod(self) -> Dict:
        """
        Delete the sandbox pod.

        :return: response from the API server
        """
        try:
            return self.api.delete_namespaced_pod(
                self.pod_name, self.k8s_namespace_name, body=V1DeleteOptions()
            )
        except ApiException as ex:
            if ex.status != 404:
                raise

    def create_pod(self, pod_manifest: Dict) -> Dict:
        """
        Create pod in a namespace
        :return: response from the API server
        """
        return self.api.create_namespaced_pod(
            body=pod_manifest, namespace=self.k8s_namespace_name
        )

    def is_pod_already_deployed(self):
        """
        Check if pod is already deployed
        :return:
        """
        try:
            resp = self.get_response_from_pod()
            return resp
        except ApiException as e:
            logger.info(e.status)
            if e.status != 404:
                logger.error(f"Unknown error: {e!r}")
                raise GeneratorDeployException(f"The pod is in an incorrect state: {e}")
            else:
                return None

    def deploy_pod(self, command: Optional[List] = None):
        """
        Deploy POD in namespace
        :return:
        """
        logger.info("Creating POD")
        resp = self.is_pod_already_deployed()

        if not resp:
            logger.info(
                "Pod with name %r does not exist in namespace %r"
                % (self.pod_name, self.k8s_namespace_name)
            )
            pod_manifest = self.create_pod_manifest(command=command)
            resp = self.create_pod(pod_manifest)
            count = 0

            for m_dir in self.mapped_dirs:
                # Copy /tmp/foo local file to /tmp/bar
                # in a remote pod in namespace <some-namespace>:
                #   oc cp /tmp/foo <some-namespace>/<some-pod>:/tmp/bar
                target = f"{self.k8s_namespace_name}/{self.pod_name}:{m_dir.path}"
                run_command(["oc", "cp", m_dir.local_dir, target])
            while True:
                logger.debug("Reading POD %r information" % self.pod_name)
                resp = self.get_response_from_pod()
                # Statuses taken from
                # https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-phase
                if resp.status.phase == "Running":
                    logger.info("All Containers in the Pod have been created. ")
                    count = 0
                if resp.status.phase == "Failed":
                    logger.info("Container FAILED with failure.")
                    return False
                if resp.status.phase == "Succeeded":
                    logger.info("All Containers in the Pod have terminated in success.")
                    return True
                # Wait for a second before another POD check
                time.sleep(2)
                count += 1
                # If POD does not start during 10 seconds, then it probaby faild.
                if count > 10:
                    logger.error(
                        "Deploying POD FAILED."
                        "Either it does not start or it does not finished yet"
                        "Status is: %r" % resp.status
                    )
                    return False
        else:
            logger.error(
                f"POD with name {self.pod_name!r} "
                f"already exists in namespace {self.k8s_namespace_name!r}"
            )
            return True

    def run(self, command: Optional[List] = None):
        logger.info("Deploying image '%r' into a new POD.", self.pod_name)
        # FIXME: make this configurable, enable running on host directly
        if "KUBERNETES_SERVICE_HOST" in os.environ:
            result = self.deploy_pod(command=command)
            self.delete_pod()
            logger.info(f"Pod {self.pod_name!r} was deleted")
            if not result:
                logger.error("Running POD FAILED. Check generator logs for reason.")
                return False
            logger.info("Sandbox finished successfully.")
            return True
        else:
            logger.warning("Generator IS NOT RUNNING in OpenShift.")
            return False
