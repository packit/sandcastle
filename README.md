# Sandcastle

Run untrusted code in a castle (OpenShift pod), which stands in a sandbox.


## Usage

The prerequisite is that you're logged in an OpenShift cluster:
```
$ oc status
 In project Local Project (myproject) on server https://localhost:8443
```

The simplest use case is to invoke a command in a new openshift pod:

```python
from sandcastle import Sandcastle

s = Sandcastle(
    image_reference="docker.io/this-is-my/image:latest",
    k8s_namespace_name="myproject"
)
output = s.run(command=["ls", "-lha"])
```

These things will happen:

* A new pod is created, using the image set in `image_reference`.
* The library actively waits for the pod to finish.
* If the pod terminates with a return code greater than 0, an exception is raised.
* Output of the command is return from the `.run()` method.


### Sharing data between sandbox and current pod

This library allows you to share volumes between the pod it is running in and between sandbox.

There is a dedicated class and an interface to access this functionality:
* `VolumeSpec` class
* `volume_mounts` kwarg of Sandcastle constructor

An example is worth of thousand words:
```python
from pathlib import Path
from sandcastle import Sandcastle, VolumeSpec

# the expectation is that volume assigned to PVC set
# via env var SANDCASTLE_PVC is mounted in current pod at /path
vs = VolumeSpec(path="/path", pvc_from_env="SANDCASTLE_PVC")

s = Sandcastle(
    image_reference="docker.io/this-is-my/image:latest",
    k8s_namespace_name="myproject",
    volume_mounts=[vs]
)
s.run()
s.exec(command=["bash", "-c", "ls -lha /path"])    # will be empty
s.exec(command=["bash", "-c", "mkdir /path/dir"])  # will create a dir
assert Path("/path/dir").is_dir()                  # should pass
```


## Developing sandcastle

In order to develop this project (and run tests), there are several requirements which need to be met.

0. Build container images using makefile target `make test-image-build`.

1. An openshift cluster and be logged into it

   Which means that running `oc status` should yield the cluster where you want to run the tests.

   The e2e test `test_from_pod` builds current codebase and runs the other e2e
   tests in a pod: to verify the E2E functionality. This expects that the
   openshift cluster is deployed in your current environment, meaning that
   openshift can access your local container images in your dockerd. Otherwise the
   image needs to be pushed somewhere so openshift can access it.


2. Docker binary and docker daemon running. This is implied from the first point.
