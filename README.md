# Generator used by packit-service

Run untrusted code in a sandbox - an OpenShift pod.


## Usage

The simplest use case is to invoke a command in a new openshift pod:

```python
from generator.deploy_openshift_pod import OpenshiftDeployer

od = OpenshiftDeployer(
    image_reference="docker.io/this-is-my/image:latest",
    k8s_namespace_name="myproject"
)
output = od.run(command=["ls", "-lha"])
```

These things will happen:

* A new pod is created, using the image set in `image_reference`.
* The library actively waits for the pod to finish.
* If the pod terminates with a return code greater than 0, an exception is raised.
* Output of the command is return from the `.run()` method.


### Share data with sandbox

You may be interested in sharing data from your current environment inside the sandbox.

```python
from generator.deploy_openshift_pod import OpenshiftDeployer

map = MappedDir()
map.local_dir = "/path/to/local/dir"
map.path = "/tmp/dir"

od = OpenshiftDeployer(
    image_reference="docker.io/this-is-my/image:latest",
    k8s_namespace_name="myproject",
    mapped_dirs=[map]
)
od.run()
od.exec(["bash", "-c", "cd /tmp/dir && ls -lha"])
```

Notes:
* You need to run commands inside the pod using exec, because the mapped dirs
  are copied into the sandbox instead using a volume.
* You should place the mapped dir inside /tmp since you don't have perms to
  write anywhere else.
