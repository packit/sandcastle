.PHONY: check build test-image-build push clean

IMAGE_NAME = docker.io/usercont/sandcastle
TEST_IMAGE_NAME = docker.io/usercont/sandcastle-tests
TEST_TARGET = ./tests

test-image-build: build
	docker build --tag ${TEST_IMAGE_NAME} -f Dockerfile.tests .

# -u to mimic openshift
check-in-container: test-image-build
	docker run --rm \
		--net host \
		-v ~/.kube:/home/sandcastle/.kube:Z \
		-u 123456789 \
		$(TEST_IMAGE_NAME) \
		bash -c './setup_env_in_openshift.sh && cat ~/.kube/config && make check TEST_TARGET=$(TEST_TARGET)'

check:
	pytest-3 --color=yes --showlocals -vv $(TEST_TARGET)

build:
	docker build --tag ${IMAGE_NAME} .

push: build
	docker push ${IMAGE_NAME}

clean:
	find . -name '*.pyc' -delete
