.PHONY: check build test-image-build push clean

IMAGE_NAME = docker.io/usercont/sandcastle
TEST_IMAGE_NAME = docker.io/usercont/sandcastle-tests
TEST_TARGET = ./tests

test-image-build: build
	docker build --tag ${TEST_IMAGE_NAME} -f Dockerfile.tests .

check:
	pytest-3 -vv $(TEST_TARGET)

build:
	docker build --tag ${IMAGE_NAME} .

push: build
	docker push ${IMAGE_NAME}

clean:
	find . -name '*.pyc' -delete
