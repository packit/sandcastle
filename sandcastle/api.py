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
import shlex
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Union, Tuple

from kubernetes.client import (
    V1DeleteOptions,
    V1Pod,
    Configuration,
    CoreV1Api,
    ApiClient,
)
from kubernetes.client.rest import ApiException
from kubernetes.config import load_incluster_config, load_kube_config
from kubernetes.stream import stream
from kubernetes.stream.ws_client import ERROR_CHANNEL, WSClient

from sandcastle.constants import (
    WEBSOCKET_CALL_TIMEOUT,
    RETRY_INIT_WS_CLIENT_MAX,
    RETRY_CREATE_POD_MAX,
)
from sandcastle.exceptions import (
    SandcastleCommandFailed,
    SandcastleExecutionError,
    SandcastleTimeoutReached,
    SandcastleException,
)
from sandcastle.kube import PVC
from sandcastle.utils import (
    get_timestamp_now,
    clean_string,
    run_command,
)

logger = logging.getLogger(__name__)


class MappedDir:
    """
    Copy local directory to the pod using `oc cp`
    """

    def __init__(
        self,
        local_dir: Union[Path, str],
        path: Union[Path, str],
        with_interim_pvc: bool = True,
    ):
        """
        :param local_dir: path within the sandbox where the directory should be copied
        :param path: copy this local directory to sandbox (using `oc cp`)
        :param with_interim_pvc: create interim Persistent Volume Claim in the sandbox
               where the data will be copied
        """
        self.path: Path = Path(path)
        self.local_dir: Path = Path(local_dir)
        self.with_interim_pvc: bool = with_interim_pvc


class VolumeSpec:
    """
    Define volume configuration for the sandbox pod.
    Either volume_name or Persistent Volume Claim are required.
    """

    def __init__(
        self,
        path: Union[Path, str],
        volume_name: str = "",
        pvc: str = "",
        pvc_from_env: str = "",
    ):
        """
        :param path: path within the sandbox where the volume is meant to be mounted
        :param volume_name: name of the volume to use
        :param pvc: use and existing PersistentVolumeClaim
        :param pvc_from_env: pick up pvc name from an env var;
               priority: env var > pvc kwarg
        """
        self.name: str = volume_name
        self.path: Path = Path(path)
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
        mapped_dir: Optional[MappedDir] = None,
    ):
        """
        :param image_reference: the pod will use this image
        :param k8s_namespace_name: name of the namespace to deploy into
        :param env_vars: additional environment variables to set in the pod
        :param pod_name: name the pod like this, if not specified, generate something long and ugly
        :param working_dir: path within the pod where we run commands by default
        :param service_account_name: run the pod using this service account
        :param volume_mounts: set these volume mounts in the sandbox
        :param mapped_dir, a mapping between a local dir which should be copied
               to the sandbox, and then copied back once all the work is done
               when this is set, working_dir args is being ignored and sandcastle invokes
               all exec commands in the working dir of the mapped dir
        """
        self.image_reference: str = image_reference
        self.service_account_name: Optional[str] = service_account_name

        self.env_vars = env_vars
        self.k8s_namespace_name = k8s_namespace_name

        # regex used for validation is '[a-z0-9]([-a-z0-9]*[a-z0-9])?
        self.cleaned_name = clean_string(image_reference)
        self.pod_name = pod_name or f"{self.cleaned_name}-{get_timestamp_now()}"

        self.working_dir = working_dir
        if working_dir and mapped_dir:
            logger.warning("Ignoring working_dir because mapped_dir is set.")
            self.working_dir = None
        self.api: CoreV1Api = self.get_api_client()
        self.volume_mounts: List[VolumeSpec] = volume_mounts or []
        self.mapped_dir: MappedDir = mapped_dir
        self.pvc: Optional[PVC] = None
        self.pod_manifest: Dict = {}

    # TODO: refactor into a pod class
    def set_pod_manifest(self, command: Optional[List] = None):
        env_image_vars = self.build_env_image_vars(self.env_vars)
        # this is broken down for sake of mypy
        container = {
            "image": self.image_reference,
            "name": self.pod_name,
            "env": env_image_vars,
            "imagePullPolicy": "IfNotPresent",
            # we may be tempted to enable tty in the pod to get pretty terminal output
            # but it's not as simple as that, for example
            # npm loves to make beautiful terminal output full of colors and sunshine
            # which would look hideous in ASCII logs (terminal escape sequences),
            # therefore only allocate tty if you are debugging something locally by
            # invoking real shell inside, tty should be set to false by default
            # TODO: making this configurable would be the best
            # "tty": True,
            "resources": {
                # 512/768 may feel like it's too much, but!
                # git-clone needs a lot of memory
                # nodejs is hungry, especially when npm compiles stuff
                # https://developer.ibm.com/languages/node-js/articles/nodejs-memory-management-in-container-environments/#
                "limits": {"memory": "768Mi"},
                "requests": {"memory": "512Mi"},
            },
        }
        spec = {
            "containers": [container],
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
        }
        self.pod_manifest = {
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

        if self.mapped_dir and self.mapped_dir.with_interim_pvc:
            self.pvc = PVC(path=self.mapped_dir.path)
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
                volume_mounts.append({"mountPath": str(vol.path), "name": local_name})
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

    @staticmethod
    def build_env_image_vars(env_dict: Dict) -> List:
        if not env_dict:
            return []
        return [
            {"name": str(key), "value": str(value) if value else ""}
            for key, value in env_dict.items()
        ]

    @staticmethod
    def get_api_client() -> CoreV1Api:
        """
        Obtain API client for kubenernetes; if running in a pod,
        load service account identity, otherwise load kubeconfig
        """
        logger.debug("Initialize kubernetes client")
        configuration = Configuration()
        if "KUBERNETES_SERVICE_HOST" in os.environ:
            logger.info("loading incluster config")
            load_incluster_config(client_configuration=configuration)
        else:
            logger.info("loading kubeconfig")
            load_kube_config(client_configuration=configuration)
        if not configuration.api_key:
            raise SandcastleException("No api_key, can't access any cluster.\n")
        return CoreV1Api(ApiClient(configuration=configuration))

    def is_pod_running(self) -> bool:
        """
        is the pod running: Phase == Running?
        :return: True if it is, False if it's not
        """
        return self.get_pod().status.phase == "Running"

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
            # delete PVC first because it has a bigger prio to be deleted
            # lingering PVCs are affecting quota
            if self.pvc:
                pvc_status = self.api.delete_namespaced_persistent_volume_claim(
                    self.pvc.claim_name,
                    namespace=self.k8s_namespace_name,
                    body=V1DeleteOptions(grace_period_seconds=0),
                )
                logger.debug(f"PVC deletion status = {pvc_status}")
        except ApiException as e:
            logger.debug(e)
            if e.status != 404:
                raise
        try:
            status = self.api.delete_namespaced_pod(
                self.pod_name,
                self.k8s_namespace_name,
                body=V1DeleteOptions(grace_period_seconds=0),
            )
            logger.debug(f"Pod deletion status = {status}")
        except ApiException as e:
            logger.debug(e)
            if e.status != 404:
                raise

    def create_pod(self, pod_manifest: Dict) -> Dict:
        """
        Create pod in a namespace

        :return: response from the API server
        """
        # if we hit timebound quota, let's try RETRY_CREATE_POD_MAX times with expo backoff
        # 2 ** 7 = 128 = 2 minutes
        # 2 ** 8 = 256 = 4 minutes
        # in total we try for ~8 minutes
        for idx in range(1, RETRY_CREATE_POD_MAX):
            try:
                logger.debug(f"Creating sandbox pod via kubernetes API, try {idx}")
                return self.api.create_namespaced_pod(
                    body=pod_manifest, namespace=self.k8s_namespace_name
                )
            except ApiException as ex:
                logger.info(f"Unable to create the pod: {ex}")
                # reproducer for this is to set memory quota for your cluster:
                # https://docs.openshift.com/online/pro/dev_guide/compute_resources.html#dev-memory-requests
                exc_str = str(ex)
                # there is no documentation to say what's inside the exception
                #   [2021-02-08 10:22:56,070: INFO/ForkPoolWorker-1] Unable to create the pod: (403)
                #   HTTP response headers: HTTPHeaderDict({'Cache-Control': 'no-store', ...
                #   HTTP response body: {"kind":"Status","status":"Failure",
                #   "message":"pods \"docker-io-usercont-sandcastle-prod-...\" is forbidden...
                #     "code":403}
                if "403" in exc_str:  # forbidden
                    sleep_time = 2 ** idx
                    logger.debug(f"Trying again in {sleep_time}s")
                    time.sleep(sleep_time)
                else:
                    raise
        raise SandcastleException("Unable to schedule the sandbox pod.")

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
        if self.mapped_dir and command:
            raise SandcastleException(
                "Since you set your own command, we cannot sync the local dir"
                " inside because there is a race condition between the pod start"
                " and the copy process. Please use exec instead."
            )

        logger.info("Deploying pod %s", self.pod_name)
        if self.is_pod_already_deployed():
            self.delete_pod()

        self.set_pod_manifest(command=command)
        self.create_pod(self.pod_manifest)

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
            if count > 600:
                logger.error(
                    "The pod did not start on time, " "status = %r" % resp.status
                )
                raise RuntimeError(
                    "The pod did not start in 600 seconds: something's wrong."
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
        """provide logs from the pod"""
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

    def _do_exec(
        self, command: List[str], preload_content=True
    ) -> Union[WSClient, str]:
        for i in range(RETRY_INIT_WS_CLIENT_MAX):
            try:
                s = stream(
                    self.api.connect_get_namespaced_pod_exec,
                    self.pod_name,
                    self.k8s_namespace_name,
                    command=command,
                    stdin=False,
                    stderr=True,
                    stdout=True,
                    tty=False,
                    _preload_content=preload_content,  # <<< we need a client object
                    _request_timeout=WEBSOCKET_CALL_TIMEOUT,
                )
                logger.debug("we have successfully initiated the kube api client")
                return s
            except ApiException as ex:
                # in packit-service prod, occasionally 'No route to host' happens here
                # let's try to repeat the request
                logger.warning("exception while initiating WS Client: %s", ex)
                time.sleep(2 * i + 1)
                continue
        raise SandcastleException("Unable to connect to the kubernetes API server.")

    def _prepare_exec(
        self,
        command: List[str],
        target_dir: Optional[Path] = None,
        env: Optional[Dict] = None,
        cwd: Union[str, Path] = None,
    ) -> Tuple[Path, Path]:
        """
        wrap a command for exec into a script file and place it to sandbox

        :param command: command to wrap
        :param target_dir: a dir in the sandbox where the script is supposed to be copied
        :param env: env vars to set in the script.sh so they are available to the "command"
        :param cwd: run the command in this directory, defaults to a mapped dir
               or a temporary directory if mapped_dir is not set
        :return: (path to the sync'd dir within sandbox, path to the script within sandbox)
        """
        cmd_str = " ".join(f"{shlex.quote(c)}" for c in command)

        root_target_dir = Path(target_dir or self._do_exec(["mktemp", "-d"]).strip())
        # this is where the content of mapped_dir will be
        unique_dir = root_target_dir / self.pod_name
        script_name = "script.sh"

        # set -e - fail the script when a command fails
        script_template = (
            "#!/bin/bash\n"
            "set -eu\n"
            "source /home/sandcastle/setup_env_in_openshift.sh\n"
        )
        cwd = cwd or ""
        script_template += f"mkdir -p {unique_dir}/{cwd}\ncd {unique_dir}/{cwd}\n"
        if env:
            for k, v in env.items():
                v = v or ""  # if v == None, we want "" instead of "None"
                script_template += f'export {k}="{v}"\n'

        script_template += f"exec {cmd_str}\n"
        logger.debug("mapped dir command: %r", script_template)
        with tempfile.TemporaryDirectory() as tmpdir:
            local_script_path = Path(tmpdir, script_name)
            local_script_path.write_text(script_template)
            # no_perms: rsync fails trying to update root_target_dir permissions
            self._copy_path_to_pod(local_script_path, root_target_dir, no_perms=True)
        target_script_path = root_target_dir / script_name
        return unique_dir, target_script_path

    def exec(
        self,
        command: List[str],
        env: Optional[Dict] = None,
        cwd: Union[str, Path] = None,
    ) -> str:
        """
        exec a command in a running pod

        :param command: command to run
        :param env: a Dict with env vars to set for the exec'd command
        :param cwd: run the command in this subdirectory of a mapped dir,
               defaults to a mapped dir or a temporary directory if mapped_dir is not set
        :returns logs
        """
        if not self.mapped_dir and cwd:
            raise SandcastleException(
                "The cwd argument only works with a mapped dir - "
                "please set a mapped dir or change directory in the command you provide."
            )
        # we need to check first if the pod is running; otherwise we'd get a nasty 500
        if not self.is_pod_running():
            raise SandcastleTimeoutReached(
                "You have reached a timeout: the pod is no longer running."
            )
        logger.info("command = %s", command)

        target_dir = None if not self.mapped_dir else Path(self.mapped_dir.path)
        unique_dir, target_script_path = self._prepare_exec(
            command, target_dir=target_dir, env=env, cwd=cwd
        )
        command = ["bash", str(target_script_path)]
        if self.mapped_dir:
            self._copy_path_to_pod(self.mapped_dir.local_dir, unique_dir)
        # https://github.com/kubernetes-client/python/blob/master/examples/exec.py
        # https://github.com/kubernetes-client/python/issues/812#issuecomment-499423823
        # FIXME: refactor this junk into a dedicated function, ideally to _do_exec
        ws_client: WSClient = self._do_exec(command, preload_content=False)
        try:
            # https://github.com/packit-service/sandcastle/issues/23
            # even with a >0 number or ==0, select tends to block
            response = ""
            errors = ""
            while ws_client.is_open():
                ws_client.run_forever(timeout=WEBSOCKET_CALL_TIMEOUT)
                errors += ws_client.read_channel(ERROR_CHANNEL)
                logger.debug("%s", errors)
                # read_all would consume ERR_CHANNEL, so read_all needs to be last
                response += ws_client.read_all()
            if errors:
                # errors = '{"metadata":{},"status":"Success"}'
                j = json.loads(errors)
                status = j.get("status", None)
                if status == "Success":
                    logger.info("exec command succeeded, yay!")
                    self._copy_mdir_from_pod(unique_dir)
                elif status == "Failure":
                    logger.info("exec command failed")
                    logger.debug(j)
                    logger.info(f"output:\n{response}")
                    # the timeout could have been reached here which means
                    # the pod is not running, so we are not able `oc rsync` things from inside:
                    # we won't be needing the data any more since p-s halts execution
                    # after a failure in action, we only do this b/c it's the right thing to do
                    # for use cases outside p-s
                    try:
                        self._copy_mdir_from_pod(unique_dir)
                    except SandcastleException:
                        # yes, we eat the exception because the one raised below
                        # is much more important since it contains metadata about what happened;
                        # logs will contain info about what happened while trying to copy things
                        pass

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
        finally:
            ws_client.close()

        logger.debug("exec response = %r" % response)
        return response

    def _copy_path_to_pod(
        self, local_path: Path, pod_dir: Path, no_perms: bool = False
    ):
        """
        copy local_path (dir or file) inside pod

        :param local_path: path to a local file or a dir
        :param pod_dir: Directory within the pod where the content of local_path is extracted
        :param no_perms: If true, do not transfer permissions

        https://www.openshift.com/blog/transferring-files-in-and-out-of-containers-in-openshift-part-1-manually-copying-files
        """
        if local_path.is_dir():
            exclude = "--exclude=lost+found"  # can't touch that
            include = "--include=[]"  # default
        elif local_path.is_file():
            exclude = "--exclude=*"  # everything
            include = f"--include={local_path.name}"  # only the file
            local_path = local_path.parent
        else:
            raise SandcastleException(f"{local_path} is neither a dir nor a file")

        cmd = [
            "oc",
            "rsync",
            exclude,
            include,
            "--quiet=true",  # avoid huge logs
            f"--namespace={self.k8s_namespace_name}",
            f"{local_path}/",  # ??? rsync doesn't work without the trailing /
            f"{self.pod_name}:{pod_dir}",
        ]
        if no_perms:
            cmd += ["--no-perms"]
        run_command(cmd)

    def _copy_path_from_pod(self, local_dir: Path, pod_dir: Path):
        """
        copy content of a dir from pod to local dir

        :param local_dir: path to the local dir
        :param pod_dir: path within the pod
        """
        try:
            run_command(
                [
                    "oc",
                    "rsync",
                    "--delete",  # delete files in local_dir which are not in pod_dir
                    "--quiet=true",  # avoid huge logs
                    f"--namespace={self.k8s_namespace_name}",
                    f"{self.pod_name}:{pod_dir}/",  # trailing / to copy only content of dir
                    f"{local_dir}",
                ]
            )
        except Exception as ex:
            # There is a race condition in k8s that it tells the pod is running even
            # though it already killed an exec session and hence we couldn't copy
            # anything from the pod
            if not self.is_pod_running() or "not available in container" in str(ex):
                logger.warning(
                    "The pod is not running while we tried to copy data out of it."
                )
                raise SandcastleException(
                    "Cannot copy data from the sandbox - the pod is not running."
                )
            raise

    def _copy_mdir_from_pod(self, unique_dir: Path):
        """process mapped_dir after we are done execing"""
        if self.mapped_dir:
            logger.debug("mapped_dir is set, let's sync the dir back and fix modes")
            self._copy_path_from_pod(
                local_dir=self.mapped_dir.local_dir, pod_dir=unique_dir
            )
