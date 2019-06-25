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
        storage_size: str = "1Gi",
    ):
        self.path = str(path)
        base = f"sandcastle-{clean_string(self.path)}-{get_timestamp_now()}"
        self.claim_name = claim_name or f"{base}-pvc"
        self.volume_name = f"{base}-vol"
        self.access_modes = access_modes or ["ReadWriteOnce"]
        self.storage_size = storage_size

    def to_dict(self):
        pvc_dict = {
            "kind": "PersistentVolumeClaim",
            "spec": {
                "accessModes": self.access_modes,
                "resources": {"requests": {"storage": self.storage_size}},
            },
            "apiVersion": "v1",
            "metadata": {"name": self.claim_name},
        }
        return pvc_dict
