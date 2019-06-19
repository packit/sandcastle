.PHONY: check build test-image-build exec-test push clean

IMAGE_NAME = docker.io/usercont/sandcastle
TEST_IMAGE_NAME = docker.io/usercont/sandcastle-tests
TEST_TARGET = ./tests

check: exec-test

test-image-build: build
	docker build --tag ${TEST_IMAGE_NAME} -f Dockerfile.tests .

exec-test:
	pytest-3 $(TEST_TARGET)

build:
	docker build --tag ${IMAGE_NAME} .

push: build
	docker push ${IMAGE_NAME}

clean:
	find . -name '*.pyc' -delete
