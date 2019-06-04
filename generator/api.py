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

import logging


from kubernetes import config, client
from kubernetes.client import Configuration
from kubernetes.client.apis import core_v1_api
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream
from generator.deploy_openshift_pod import OpenshiftDeployer

logger = logging.getLogger(__name__)


class GeneratorAPI:
    def __init__(self, k8s_namespace_name: str, pod_name: str, image_reference: str):
        self.pod_name = pod_name
        self.k8s_namespace_name = k8s_namespace_name
        self.image_reference = image_reference
        self.api = GeneratorAPI.get_api_client()

    @staticmethod
    def get_api_client() -> client.CoreV1Api:
        logger.debug("Initialize kubernetes client")
        config.load_kube_config()
        c = Configuration()
        c.assert_hostname = False
        Configuration.set_default(c)
        return core_v1_api.CoreV1Api()

    def run_pod(self):
        """
        Run pod in a namespace
        :return: response from the API server
        """
        o = OpenshiftDeployer(
            image_reference=self.image_reference,
            k8s_namespace_name=self.k8s_namespace_name,
            pod_name=self.pod_name,
        )
        return o.run()

    def run_command(self, command: str):
        """
        Run command inside a pod
        The core of this function was taken from
        https://github.com/kubernetes-client/python/blob/master/examples/exec.py
        :param command: to run
        :return: response from the API server
        """
        try:
            self.api.read_namespaced_pod(
                name=self.pod_name, namespace=self.k8s_namespace_name
            )
        except ApiException as ex:
            if ex.status != 404:
                raise

        resp = stream(
            self.api.connect_get_namespaced_pod_exec,
            self.pod_name,
            self.k8s_namespace_name,
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )
        logger.debug(f"Response: {resp}")
        return resp
