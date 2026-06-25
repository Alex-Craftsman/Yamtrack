DOCKER_TEST_IMAGE ?= yamtrack-test
TEST_ARGS ?= --parallel

.PHONY: test tests docker-test docker-tests

test:
	cd src && uv run manage.py test $(TEST_ARGS)

tests: test

docker-test:
	docker build --target test -t $(DOCKER_TEST_IMAGE) .
	docker run --rm $(DOCKER_TEST_IMAGE) python manage.py test $(TEST_ARGS)

docker-tests: docker-test
