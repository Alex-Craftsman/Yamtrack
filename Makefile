.PHONY: test tests

test:
	cd src && uv run manage.py test --parallel

tests: test
