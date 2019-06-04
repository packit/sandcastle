.PHONY: build test-build test-in-container push exec-test clean

IMAGE_NAME = docker.io/usercont/packit-generator
TEST_IMAGE_NAME = docker.io/usercont/packit-generator-test
TEST_TARGET = ./tests

test-image-build: build
	docker build --tag ${TEST_IMAGE_NAME} -f Dockerfile.tests .

test-in-container: test-build
	docker run --rm \
	        --name=packit-generator-test \
			$(TEST_IMAGE_NAME) \
			make exec-test

exec-test:
	pytest $(TEST_TARGET)

build:
	docker build --tag ${IMAGE_NAME} .

push: build
	docker push ${IMAGE_NAME}

clean:
	find . -name '*.pyc' -delete
