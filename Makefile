.PHONY: image-build test-build test-in-container image-push exec-test clean

IMAGE_NAME = docker.io/usercont/packit-generator
TEST_IMAGE_NAME = packit-generator-test
TEST_TARGET = ./tests

test-build: image-build
	docker build --tag ${TEST_IMAGE_NAME} -f Dockerfile.tests .

test-in-container: test-build
	docker run --rm \
	        --name=packit-generator \
			$(TEST_IMAGE_NAME) \
			make exec-test

exec-test:
	pytest $(TEST_TARGET)

image-build:
	docker build --tag ${IMAGE_NAME} .

image-push: image-build
	docker push ${IMAGE_NAME}

clean:
	find . -name '*.pyc' -delete
