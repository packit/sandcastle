# Generator used by packit-service

Generator is used for deploying one POD from another POD.
The aim is to separate `packit-service` and generation sources in order
to be secure.

## How to use generator

```python
from generator.deploy_openshift_pod import OpenshiftDeployer
od = OpenshiftDeployer(volume_dir="/tmp", upstream_name="colin", project_name="packit")
od.deploy_image()

```