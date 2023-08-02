# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Kube objects generation.
"""
from pathlib import Path
from typing import List, Union

from sandcastle.utils import clean_string, get_timestamp_now


class PVC:
    def __init__(
        self,
        path: Union[str, Path],
        claim_name: str = None,
        access_modes: List[str] = None,
        storage_size: str = "3Gi",
    ):
        self.path = str(path)
        base = f"sandcastle-{clean_string(self.path)}-{get_timestamp_now()}"
        self.claim_name = claim_name or f"{base}-pvc"
        self.volume_name = f"{base}-vol"
        self.access_modes = access_modes or ["ReadWriteOnce"]
        self.storage_size = storage_size

    def to_dict(self):
        return {
            "kind": "PersistentVolumeClaim",
            "spec": {
                "accessModes": self.access_modes,
                "resources": {"requests": {"storage": self.storage_size}},
                # aws-ebs is managed by us, costs less, smaller throughput
                # aws-efs* is managed by AWS, is faster
                "storageClassName": "aws-ebs",
            },
            "apiVersion": "v1",
            "metadata": {
                "name": self.claim_name,
                "annotations": {
                    # after deleting »the PVC«, the data are deleted
                    "kubernetes.io/reclaimPolicy": "Delete",
                },
                "labels": {
                    # TODO: adjust after deploying the prod if need be…
                    "paas.redhat.com/appcode": "PCKT-002",
                },
            },
        }
