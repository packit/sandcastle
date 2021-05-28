.PHONY: check build test-image-build push clean

IMAGE_NAME = quay.io/packit/sandcastle
TEST_IMAGE_NAME = quay.io/packit/sandcastle-tests
TEST_TARGET = ./tests
CONTAINER_ENGINE ?= $(shell command -v podman 2> /dev/null || echo docker)

build-test-image: build
	$(CONTAINER_ENGINE) build --tag ${TEST_IMAGE_NAME} -f Dockerfile.tests .

# -u to mimic openshift
check-in-container:
	$(CONTAINER_ENGINE) run --rm \
		--net host \
		-v ~/.kube:/home/sandcastle/.kube:Z \
		-u 123456789 \
		$(TEST_IMAGE_NAME) \
		bash -c './setup_env_in_openshift.sh && cat ~/.kube/config && make check TEST_TARGET=$(TEST_TARGET)'

check:
	pytest-3 --color=yes --showlocals -vv $(TEST_TARGET)

build:
	$(CONTAINER_ENGINE) build --tag ${IMAGE_NAME} .

push: build
	$(CONTAINER_ENGINE) push ${IMAGE_NAME}

clean:
	find . -name '*.pyc' -delete
