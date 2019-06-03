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

Our team have met to discuss volumes and sharing data b/w containers & pods.
We came up with these solutions:

* `oc cp` - copy data using the `cp` command: very simple

* controller pod - dynamically create and deploy pods
  with volumes - very flexible, pretty complex

* claim - utilize persistent volume claim feature of k8s: let pods claim volumes
"""

import datetime
import logging
import os
import time
from typing import Dict, List, Optional

from kubernetes import config, client
from kubernetes.client import V1DeleteOptions, V1Pod
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream

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

        # regex used for validation is '[a-z0-9]([-a-z0-9]*[a-z0-9])?
        self.cleaned_name = (
            image_reference.replace("/", "-").replace(":", "-").replace(".", "-")
        )
        if pod_name:
            self.pod_name = pod_name
        else:
            timestamp = datetime.datetime.now().strftime("%Y%M%d-%H%M%S%f")
            self.pod_name = f"{self.cleaned_name}-{timestamp}"

        self.api: client.CoreV1Api = self.get_api_client()
        self.volume_mounts: List[VolumeSpec] = volume_mounts or []
        self.mapped_dirs: List[MappedDir] = mapped_dirs or []

    def create_pod_manifest(self, command: Optional[List] = None) -> dict:
        env_image_vars = self.build_env_image_vars(self.env_vars)
        # this is broken down for sake of mypy
        container = {
            "image": self.image_reference,
            "name": self.pod_name,
            "env": env_image_vars,
            "imagePullPolicy": "IfNotPresent",
            # "volumeMounts": [
            #     {"mountPath": self.volume_dir, "name": "packit-generator"}
            # ],
        }
        spec = {
            "containers": [container],
            "restartPolicy": "Never",
            # "volumes": [
            #     {
            #         "name": "packit-generator",
            #         "persistentVolumeClaim": {"claimName": "claim.packit"},
            #     }
            # ],
        }
        pod_manifest = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": self.pod_name},
            "spec": spec,
        }
        if command:
            container["command"] = command
        if self.service_account_name:
            spec["serviceAccountName"] = self.service_account_name
        if self.volume_mounts:
            volume_mounts: List[Dict] = []
            container["volumeMounts"] = volume_mounts
            for vol in self.volume_mounts:
                volume_mounts.append({"mountPath": vol.path, "name": vol.name})
            volumes: List[Dict] = []
            spec["volumes"] = volumes
            for vol in self.volume_mounts:
                if vol.pvc:
                    di = {
                        "name": vol.name,
                        "persistentVolumeClaim": {"claimName": vol.pvc},
                    }
                elif vol.name:
                    # will this work actually if the volume is created up front?
                    di = {"name": vol.name}
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
            env_image_vars.append({"name": str(key), "value": str(value) or ""})
        return env_image_vars

    @staticmethod
    def get_api_client() -> client.CoreV1Api:
        """
        Obtain API client for kubenernetes; if running in a pod,
        load service account identity, otherwise load kubeconfig
        """
        logger.debug("Initialize kubernetes client")
        if "KUBERNETES_SERVICE_HOST" in os.environ:
            logger.info("loading incluster config")
            config.load_incluster_config()
        else:
            logger.info("loading kubeconfig")
            config.load_kube_config()
        configuration = client.Configuration()
        assert configuration.api_key
        # example exec.py sets this:
        # https://github.com/kubernetes-client/python/blob/master/examples/exec.py#L61
        configuration.assert_hostname = False
        client.Configuration.set_default(configuration)
        return client.CoreV1Api()

    def get_response_from_pod(self) -> V1Pod:
        """
        Read info from a pod in a namespace
        :return: JSON Dict
        """
        return self.api.read_namespaced_pod(
            name=self.pod_name, namespace=self.k8s_namespace_name
        )

    def delete_pod(self):
        """
        Delete the sandbox pod.

        :return: response from the API server
        """
        try:
            self.api.delete_namespaced_pod(
                self.pod_name, self.k8s_namespace_name, body=V1DeleteOptions()
            )
        except ApiException as e:
            logger.debug(e)
            if e.status != 404:
                raise

    def create_pod(self, pod_manifest: Dict) -> Dict:
        """
        Create pod in a namespace
        :return: response from the API server
        """
        return self.api.create_namespaced_pod(
            body=pod_manifest, namespace=self.k8s_namespace_name
        )

    def is_pod_already_deployed(self) -> bool:
        """
        Check if the pod is already present.
        """
        try:
            self.get_response_from_pod()
            return True
        except ApiException as e:
            logger.debug(e)
            if e.status != 404:
                logger.error(f"Unknown error: {e!r}")
                raise GeneratorDeployException(f"Something's wrong with the pod': {e}")
            return False

    def copy_path_to_pod(self, map: MappedDir):
        """ copy local path inside the pod """
        # Copy /tmp/foo local file to /tmp/bar
        # in a remote pod in namespace <some-namespace>:
        #   oc cp /tmp/foo <some-namespace>/<some-pod>:/tmp/bar
        target = f"{self.k8s_namespace_name}/{self.pod_name}:{map.path}"
        logger.info(f"copy {map.local_dir} -> {target}")
        # if you're interested: the way openshift does this is that creates a tarball locally
        # and streams it via exec into the container to a pod process
        run_command(["oc", "cp", map.local_dir, target])

    def copy_path_from_pod(self, map: MappedDir):
        """ copy remote path content locally """
        # Copy /tmp/foo from a remote pod to /tmp/bar locally
        #   oc cp <some-namespace>/<some-pod>:/tmp/foo /tmp/bar
        target = f"{self.k8s_namespace_name}/{self.pod_name}:{map.path}"
        logger.info(f"copy {target} -> {map.local_dir}")
        run_command(["oc", "cp", target, map.local_dir])

    def deploy_pod(self, command: Optional[List] = None):
        """
        Deploy pod into openshift. If it exists already, remove it.
        Once it starts, sync mapped dirs inside. If the command is set, wait for it to finish.
        """
        logger.info("Deploying pod %s", self.pod_name)
        if self.is_pod_already_deployed():
            self.delete_pod()

        pod_manifest = self.create_pod_manifest(command=command)
        self.create_pod(pod_manifest)

        # wait for the pod to start
        count = 0
        logger.debug("pod = %r" % self.pod_name)
        while True:
            resp = self.get_response_from_pod()
            # https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-phase
            if resp.status.phase != "Pending":
                logger.info("pod is no longed pending - status: %s", resp.status.phase)
                break
            time.sleep(1)
            count += 1
            if count > 30:
                logger.error(
                    "The pod did not start on time, " "status = %r" % resp.status
                )
                raise RuntimeError(
                    "The pod did not start in 30 seconds: something's wrong."
                )

        if resp.status.phase == "Failed":
            raise RuntimeError("Pod failed, please check logs.")

        if self.mapped_dirs and command:
            raise RuntimeError(
                "Since you set your own command, we cannot sync the local dir"
                " inside because there is a race condition between the pod start"
                " and the copy process. Please use exec instead."
            )

        if command:
            # wait for the pod to finish since the command is set
            while True:
                resp = self.get_response_from_pod()
                # https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-phase
                if resp.status.phase == "Failed":
                    logger.info(
                        "The pod has failed execution: you should "
                        "inspect logs or check `oc describe`"
                    )
                    break
                if resp.status.phase == "Succeeded":
                    logger.info("All Containers in the pod have finished successfully.")
                    for m_dir in self.mapped_dirs:
                        self.copy_path_to_pod(m_dir)
                    break
                # TODO: can we use watch instead?
                time.sleep(1)

    def run(self, command: Optional[List] = None):
        """
        deploy a pod; if a command is set, we wait for it to finish,
        if the command is not set, sleep process runs in the pod and you can use exec
        to run commands inside

        :param command: command to run in the pod, if None, run sleep
        """
        self.deploy_pod(command=command)
        logger.info("Sandbox pod is deployed.")

    def exec(self, command: List[str]):
        """
        exec a command in a running pod; if any mapped dirs are set,
        sync the dirs before and after the execution

        :param command: command to run
        """
        # https://github.com/kubernetes-client/python/blob/master/examples/exec.py
        for m_dir in self.mapped_dirs:
            self.copy_path_to_pod(m_dir)
        # FIXME: we're unable to get RC of the exec
        response = stream(
            self.api.connect_get_namespaced_pod_exec,
            self.pod_name,
            self.k8s_namespace_name,
            # async_req=True,
            command=command,
            stderr=True,
            stdout=True,
            tty=False,
        )
        logger.debug("exec response = %r" % response)
        for m_dir in self.mapped_dirs:
            self.copy_path_from_pod(m_dir)
