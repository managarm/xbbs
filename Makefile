.POSIX:

help:
	@echo "Available targets:"
	@echo "    help  - this message"
	@echo "    flake - runs the flake8 linter"

flake:
	flake8 --ignore=E402 xbbs

.PHONY: flake help
