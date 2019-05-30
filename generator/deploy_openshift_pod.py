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
import os
import time
import datetime
from typing import Dict

from kubernetes import config, client
from kubernetes.client.rest import ApiException
from generator.exceptions import GeneratorDeployException
from generator.utils import run_command
logger = logging.getLogger(__name__)


class OpenshiftDeployer(object):

    def __init__(self, volume_dir: str, upstream_name: str, project_name: str, env_dict: Dict = None):
        """

        :param volume_dir:
        :param upstream_name:
        :param project_name:
        :param env_dict:
        """
        self.volume_dir = volume_dir
        self.upstream_name = upstream_name
        self.env_dict = env_dict
        self.project_name = project_name
        timestamp = datetime.datetime.now().strftime("-%Y%M%d-%H%M%S%f")
        self.pod_name = "{upstream_name}-{timestamp}-deployment".format(upstream_name=upstream_name,
                                                                        timestamp=timestamp)
        self.api_token = None
        self.pod_manifest = {
            'apiVersion': 'v1',
            'kind': 'Pod',
            'metadata': {
                'name': self.pod_name
            },
            'spec': {
                'containers': [{
                    'image': self.upstream_name,
                    'name': self.upstream_name,
                    #'env': [
                    #    {'name': 'DOWNSTREAM_IMAGE_NAME', 'value': self.upstream_name},
                    #    {'name': 'UPSTREAM_IMAGE_NAME', 'value': self.upstream_name}
                    #],
                    'volumeMounts': [{
                        'mountPath': volume_dir,
                        'name': "betka-generator"
                    }]
                }],
                'restartPolicy': 'Never',
                'serviceAccountName': 'packit-service',
                'volumes': [{
                    'name': "packit-generator",
                    'persistentVolumeClaim': {
                        'claimName': "claim.packit"
                    }
                }]
            }}

    def setup_kubernetes(self):
        logger.debug("Setup kubernetes")
        config.load_incluster_config()
        self.api_token = run_command(["oc", "whoami", "-t"], output=True).strip()
        configuration = client.Configuration()
        configuration.api_key['authorization'] = self.api_token
        configuration.api_key_prefix['authorization'] = 'Bearer'
        self.api = client.CoreV1Api(client.ApiClient(configuration))

    def get_response_from_pod(self) -> Dict:
        """
        Read info from a pod in a namespace
        :return: JSON Dict
        """
        return self.api.read_namespaced_pod(name=self.pod_name,
                                            namespace=self.project_name)

    def delete_pod(self) -> Dict:
        """
        Delete pod in a namespace
        :return: JSON Dict
        """
        self.api.delete_namespaced_pod(self.pod_name, self.project_name)

    def create_pod(self) -> Dict:
        """
        Create pod in a namespace
        :return: JSON dict
        """
        return self.api.create_namespaced_pod(
                body=self.pod_manifest,
                namespace=self.project_name
            )

    def is_pod_already_deployed(self):
        resp = None
        try:
            resp = self.get_response_from_pod()
        except ApiException as e:
            logger.info(e.status)
            if e.status != 404:
                logger.error(f"Unknown error: {e!r}")
                raise GeneratorDeployException
            else:
                return None
        return resp

    def deploy_pod(self):
        logger.info("Creating POD")
        resp = self.is_pod_already_deployed()

        if not resp:
            logger.info("Pod with name %r does not exist in namespace %r" % (self.pod_name,
                                                                             self.project_name))
            resp = self.create_pod()
            count = 0

            while True:
                logger.debug("Reading POD %r information" % self.pod_name)
                resp = self.get_response_from_pod()
                # Statuses taken from
                # https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-phase
                if resp.status.phase == 'Running':
                    logger.info("All Containers in the Pod have been created. ")
                    count = 0
                if resp.status.phase == 'Failed':
                    logger.info("Container FAILED with failure.")
                    return False
                if resp.status.phase == 'Succeeded':
                    logger.info("All Containers in the Pod have terminated in success.")
                    return True
                # Wait for a second before another POD check
                time.sleep(2)
                count += 1
                # If POD does not start during 10 seconds, then it probaby faild.
                if count > 10:
                    logger.error("Deploying POD FAILED."
                                 "Either it does not start or it does not finished yet"
                                 "Status is: %r" % resp.status)
                    return False
        else:
            logger.error(f"POD with name {self.pod_name!r} "
                         f"already exists in namespace {self.project_name!r}")
            return True

    def deploy_image(self):
        logger.info("Deploying image '%r' into a new POD.", self.upstream_name)
        if 'KUBERNETES_SERVICE_HOST' in os.environ:
            self.setup_kubernetes()
            result = self.deploy_pod()
            self.delete_pod()
            logger.info(f"Pod {self.pod_name!r} was deleted")
            if not result:
                logger.error("Running POD FAILED. Check generator logs for reason.")
                return False
            logger.info("Running POD was successful. "
                        f"Check {self.volume_dir} directory for results.")
            return True
        else:
            logger.warning("Generator IS NOT RUNNING in OpenShift.")
            pass
