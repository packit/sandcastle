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

* claim - utilize persistent volume claim feature of k8s: let pods claim volumes
  and then attach the claimed volume to sandbox pod

* `oc cp` - copy data using the `cp` command: very simple (alternatively,
  run rsync server in the sandbox) -- this is problematic because oc-cp
  is implemented using tar and you need to take care of source and destination
  paths - if they exist, tar complains; there is also a problem that you
  can't use workingDir in pod, because the path with the data likely
  does not exist yet

* controller pod - dynamically create and deploy pods
  with volumes - very flexible, pretty complex
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Union

from kubernetes import config, client
from kubernetes.client import V1DeleteOptions, V1Pod
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream
from kubernetes.stream.ws_client import ERROR_CHANNEL, WSClient

from sandcastle.exceptions import (
    SandcastleCommandFailed,
    SandcastleExecutionError,
    SandcastleTimeoutReached,
)
from sandcastle.kube import PVC
from sandcastle.utils import (
    get_timestamp_now,
    clean_string,
    run_command,
    purge_dir_content,
)

logger = logging.getLogger(__name__)


class MappedDir:
    """
    Copy local directory to the pod using `oc cp`
    """

    def __init__(self, local_dir: str, path: str, with_interim_pvc: bool):
        # path within the sandbox where the directory should be copied
        self.path = Path(path)
        # copy this local directory to sandbox (using `oc cp`)
        self.local_dir = Path(local_dir)
        self.with_pvc = with_interim_pvc


class VolumeSpec:
    """
    Define volume configuration for the sandbox pod.
    Either volume_name or Persistent Volume Claim are required.
    """

    def __init__(
        self, path: str, volume_name: str = "", pvc: str = "", pvc_from_env: str = ""
    ):
        """
        :param path: path within the sandbox where the volume is meant to be mounted
        :param volume_name: name of the volume to use
        :param pvc: use and existing PersistentVolumeClaim
        :param pvc_from_env: pick up pvc name from an env var;
               priority: env var > pvc kwarg
        """
        self.name: str = volume_name
        self.path: str = path
        self.pvc: str = pvc
        if pvc_from_env:
            self.pvc = os.getenv(pvc_from_env)


class Sandcastle(object):
    def __init__(
        self,
        image_reference: str,
        k8s_namespace_name: str,
        env_vars: Optional[Dict] = None,
        pod_name: Optional[str] = None,
        working_dir: Optional[str] = None,
        service_account_name: Optional[str] = None,
        volume_mounts: Optional[List[VolumeSpec]] = None,
        mapped_dirs: Optional[List[MappedDir]] = None,
    ):
        """
        :param image_reference: the pod will use this image
        :param k8s_namespace_name: name of the namespace to deploy into
        :param env_vars: additional environment variables to set in the pod
        :param pod_name: name the pod like this, if not specified, generate something long and ugly
        :param working_dir: path within the pod where we run commands by default
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
        self.cleaned_name = clean_string(image_reference)
        if pod_name:
            self.pod_name = pod_name
        else:
            self.pod_name = f"{self.cleaned_name}-{get_timestamp_now()}"

        self.working_dir = working_dir
        self.api: client.CoreV1Api = self.get_api_client()
        self.volume_mounts: List[VolumeSpec] = volume_mounts or []
        self.mapped_dirs: List[MappedDir] = mapped_dirs or []
        self.pvc: Optional[PVC] = None

    # TODO: refactor into a pod class
    def create_pod_manifest(self, command: Optional[List] = None) -> dict:
        env_image_vars = self.build_env_image_vars(self.env_vars)
        # this is broken down for sake of mypy
        container = {
            "image": self.image_reference,
            "name": self.pod_name,
            "env": env_image_vars,
            "imagePullPolicy": "IfNotPresent",
        }
        spec = {
            "containers": [container],
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
        }
        pod_manifest = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": self.pod_name},
            "spec": spec,
        }
        if self.working_dir:
            container["workingDir"] = self.working_dir
        if command:
            container["command"] = command
        if self.service_account_name:
            spec["serviceAccountName"] = self.service_account_name

        for md in self.mapped_dirs:
            if md.with_pvc:
                self.pvc = PVC(path=md.path)
                self.api.create_namespaced_persistent_volume_claim(
                    namespace=self.k8s_namespace_name, body=self.pvc.to_dict()
                )
                self.volume_mounts.append(
                    VolumeSpec(
                        path=self.pvc.path,
                        volume_name=self.pvc.volume_name,
                        pvc=self.pvc.claim_name,
                    )
                )

        if self.volume_mounts:
            volume_mounts: List[Dict] = []
            container["volumeMounts"] = volume_mounts
            volumes: List[Dict] = []
            spec["volumes"] = volumes
            for vol in self.volume_mounts:
                # local name b/w vol definition and container def
                local_name = vol.name or clean_string(vol.pvc)
                volume_mounts.append({"mountPath": vol.path, "name": local_name})
                if vol.pvc:
                    di = {
                        "name": local_name,
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
            if value:
                value = str(value)
            else:
                value = ""
            env_image_vars.append({"name": str(key), "value": value})
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

    def get_pod(self) -> V1Pod:
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
            if self.pvc:
                self.api.delete_namespaced_persistent_volume_claim(
                    self.pvc.claim_name,
                    namespace=self.k8s_namespace_name,
                    body=V1DeleteOptions(),
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
            self.get_pod()
            return True
        except ApiException as e:
            logger.debug(e)
            if e.status == 403:
                logger.error("we are not allowed to get info about the pod")
                logger.info("exception = %r", e)
                raise SandcastleExecutionError(
                    "We are not allowed to get information about pods: "
                    "please make sure that the identity you're using can "
                    "access resources in the API server."
                )
            if e.status != 404:
                logger.error(f"Unknown error: {e!r}")
                raise SandcastleExecutionError(f"Something's wrong with the pod': {e}")
            return False

    @staticmethod
    def get_rc_from_v1pod(resp: V1Pod) -> int:
        try:
            return resp.status.container_statuses[0].state.terminated.exit_code
        except (AttributeError, IndexError) as ex:
            logger.error("status has incorrect structure: %r", ex)
            return 999

    def deploy_pod(self, command: Optional[List] = None):
        """
        Deploy a pod and babysit it. If it exists already, remove it.
        """
        if self.mapped_dirs and command:
            raise RuntimeError(
                "Since you set your own command, we cannot sync the local dir"
                " inside because there is a race condition between the pod start"
                " and the copy process. Please use exec instead."
            )

        logger.info("Deploying pod %s", self.pod_name)
        if self.is_pod_already_deployed():
            self.delete_pod()

        pod_manifest = self.create_pod_manifest(command=command)
        self.create_pod(pod_manifest)

        # wait for the pod to start
        count = 0
        logger.debug("pod = %r" % self.pod_name)
        while True:
            resp = self.get_pod()
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
            # > resp.status.container_statuses[0].state
            # {'running': None,
            #  'terminated': {'container_id': 'docker://f3828...
            #                 'exit_code': 2,
            #                 'finished_at': datetime.datetime(2019, 6, 7,...
            #                 'message': None,
            #                 'reason': 'Error',
            #                 'signal': None,
            #                 'started_at': datetime.datetime(2019, 6, 7,...
            #  'waiting': None}

            raise SandcastleCommandFailed(
                output=self.get_logs(),
                reason=str(resp.status),
                rc=self.get_rc_from_v1pod(resp),
            )

        if command:
            # wait for the pod to finish since the command is set
            while True:
                resp = self.get_pod()
                # https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-phase
                if resp.status.phase == "Failed":
                    logger.info(
                        "The pod has failed execution: you should "
                        "inspect logs or check `oc describe`"
                    )
                    raise SandcastleCommandFailed(
                        output=self.get_logs(),
                        reason=str(resp.status),
                        rc=self.get_rc_from_v1pod(resp),
                    )
                if resp.status.phase == "Succeeded":
                    logger.info("All Containers in the pod have finished successfully.")
                    break
                # TODO: can we use watch instead?
                time.sleep(1)

    def get_logs(self) -> str:
        """ provide logs from the pod """
        return self.api.read_namespaced_pod_log(
            name=self.pod_name, namespace=self.k8s_namespace_name
        )

    def run(self, command: Optional[List] = None):
        """
        deploy a pod; if a command is set, we wait for it to finish,
        if the command is not set, sleep process runs in the pod and you can use exec
        to run commands inside

        :param command: command to run in the pod, if None, run sleep
        """
        self.deploy_pod(command=command)
        logger.info("Sandbox pod is deployed.")
        return self.get_logs()

    def _do_exec(
        self, command: List[str], preload_content=True
    ) -> Union[WSClient, str]:
        return stream(
            self.api.connect_get_namespaced_pod_exec,
            self.pod_name,
            self.k8s_namespace_name,
            command=command,
            stdin=False,
            stderr=True,
            stdout=True,
            tty=False,
            _preload_content=preload_content,  # <<< we need a client object
        )

    def exec(self, command: List[str]) -> str:
        """
        exec a command in a running pod

        :param command: command to run
        :returns logs
        """
        # we need to check first if the pod is running; otherwise we'd get a nasty 500
        pod = self.get_pod()
        if pod.status.phase != "Running":
            raise SandcastleTimeoutReached(
                "You have reached a timeout: the pod is no longer running."
            )
        for m_dir in self.mapped_dirs:
            self._copy_path_to_pod(m_dir)
        # https://github.com/kubernetes-client/python/blob/master/examples/exec.py
        # https://github.com/kubernetes-client/python/issues/812#issuecomment-499423823
        ws_client: WSClient = self._do_exec(command, preload_content=False)
        ws_client.run_forever(timeout=60)
        errors = ws_client.read_channel(ERROR_CHANNEL)
        # read_all would consume ERR_CHANNEL, so read_all needs to be last
        response = ws_client.read_all()
        if errors:
            # errors = '{"metadata":{},"status":"Success"}'
            j = json.loads(errors)
            status = j.get("status", None)
            if status == "Success":
                logger.info("exec command succeeded, yay!")
                for m_dir in self.mapped_dirs:
                    self._copy_path_from_pod(m_dir)
            elif status == "Failure":
                logger.info("exec command failed")
                for m_dir in self.mapped_dirs:
                    self._copy_path_from_pod(m_dir)
                # ('{"metadata":{},"status":"Failure","message":"command terminated with '
                #  'non-zero exit code: Error executing in Docker Container: '
                #  '1","reason":"NonZeroExitCode","details":{"causes":[{"reason":"ExitCode","message":"1"}]}}')
                causes = j.get("details", {}).get("causes", [])
                rc = 999
                for c in causes:
                    if c.get("reason", None) == "ExitCode":
                        try:
                            rc = int(c.get("message", None))
                        except ValueError:
                            rc = 999
                raise SandcastleCommandFailed(output=response, reason=errors, rc=rc)
            else:
                logger.warning(
                    "exec didn't yield the metadata we expect, mighty suspicious, %s",
                    errors,
                )

        logger.debug("exec response = %r" % response)
        return response

    def _copy_path_to_pod(self, map: MappedDir):
        """ copy local path inside the pod """
        # Copy /tmp/foo local file to /tmp/bar
        # in a remote pod in namespace <some-namespace>:
        #   oc cp /tmp/foo <some-namespace>/<some-pod>:/tmp/bar
        target = f"{self.k8s_namespace_name}/{self.pod_name}:{map.path}"
        logger.info(f"copy {map.local_dir} -> {target}")
        # if you're interested: the way openshift does this is that creates a tarball locally
        # and streams it via exec into the container to a pod process
        for item in map.local_dir.iterdir():
            # we are doing this insanity because of two things:
            #   1. tar creates a root dir in the tarball, so we would need to cd into it
            #   2. this way we can set working_dir to map.path in the pod
            logger.debug(f"copy item {item} -> {target}")
            run_command(["oc", "cp", item, target])

    def _copy_path_from_pod(self, map: MappedDir):
        """ copy remote path content locally """
        # Copy /tmp/foo from a remote pod to /tmp/bar locally
        #   oc cp <some-namespace>/<some-pod>:/tmp/foo /tmp/bar
        target = f"{self.k8s_namespace_name}/{self.pod_name}:{map.path}"

        # temp_dir = Path(tempfile.mkdtemp())
        # logger.info(f"copy {target} -> {temp_dir}")
        # run_command(["oc", "cp", target, temp_dir])

        purge_dir_content(map.local_dir)

        logger.info(f"copy {target} -> {map.local_dir}")
        run_command(["oc", "cp", target, map.local_dir])
