# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Kube objects generation.
"""
from pathlib import Path
from typing import List, Union, Optional

from sandcastle.utils import clean_string, get_timestamp_now


class PVC:
    def __init__(
        self,
        path: Union[str, Path],
        claim_name: str = None,
        access_modes: List[str] = None,
        storage_size: str = "3Gi",
        storage_class: Optional[str] = None,
        appcode: Optional[str] = None,
    ):
        self.path = str(path)
        base = f"sandcastle-{clean_string(self.path)}-{get_timestamp_now()}"
        self.claim_name = claim_name or f"{base}-pvc"
        self.volume_name = f"{base}-vol"
        self.access_modes = access_modes or ["ReadWriteOnce"]
        self.storage_size = storage_size
        self.storage_class = storage_class
        self.appcode = appcode

    def to_dict(self):
        return {
            "kind": "PersistentVolumeClaim",
            "spec": {
                "accessModes": self.access_modes,
                "resources": {"requests": {"storage": self.storage_size}},
                "storageClassName": self.storage_class,
            },
            "apiVersion": "v1",
            "metadata": {
                "name": self.claim_name,
                "annotations": {
                    # after deleting »the PVC«, the data are deleted
                    "kubernetes.io/reclaimPolicy": "Delete",
                },
                "labels": {
                    "paas.redhat.com/appcode": self.appcode,
                },
            },
        }
