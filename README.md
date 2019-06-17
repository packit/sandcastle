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
