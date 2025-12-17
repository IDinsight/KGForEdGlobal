#!make

.DEFAULT_GOAL := help
.PHONY: clean clean-docker clean-docker-container fresh-env help install lint test test-alembic uv-check-updates uv-lock uv-sync uv-update-deps

# Put it first so that "make" without argument is like "make help".
help: ## Display available commands
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[32m%-20s\033[0m %s\n", $$1, $$2}'

########## COLOR CODES FOR OUTPUT ##########
RED := $(shell tput setaf 1)
GREEN := $(shell tput setaf 2)
YELLOW := $(shell tput setaf 3)
BLUE := $(shell tput setaf 4)
RESET := $(shell tput sgr0)

########## GLOBALS ##########
SHELL := /bin/bash
PROJECT_NAME = SenegalKG

########## HELPERS ##########
VENV ?= .venv
PYTHON = $(VENV)/bin/python
UV = uv
UV_PIP = $(UV) pip

export-test-env = set -a && source ./tests/test.env && set +a &&

########## CLEAN UP ##########
# Delete all compiled Python files.
clean: ## Remove compiled Python files, caches, and old virtual environments
	@echo "$(BLUE)Cleaning backend project files...$(RESET)"
	@find . -type d -name "__pycache__" -exec rm -rf {} +
	@rm -rf $(VENV) dist build *.egg-info .pytest_cache .coverage htmlcov
	@echo "$(GREEN)Cleanup complete.$(RESET)"

########## BACKEND ENVIRONMENT SETUP ##########
install: ## Create venv (if missing) and install ALL deps (core + [dev,docs]).
	@echo "$(YELLOW)Creating fresh backend environment: $(PROJECT_NAME)$(RESET)"
	@$(UV) venv
	@$(PYTHON) -m ensurepip --upgrade
	@$(UV_PIP) sync ../cicd/requirements/requirements.txt ../cicd/requirements/requirements_dev.txt ../cicd/requirements/requirements_docs.txt
	@echo "$(GREEN)Backend environment setup complete.$(RESET)"
	@echo "$(GREEN)Run 'source .venv/bin/activate' to activate your environment.$(RESET)"

fresh-env: clean install ## Create a fresh backend environment

########## LINTING ##########
lint: lint-isort lint-black lint-ruff lint-interrogate lint-mypy lint-pylint lint-cloc ## Run all linter and formatter checks

lint-isort: ## Sort imports
	@echo "$(BLUE)Running isort on backend...$(RESET)"
	@$(VENV)/bin/isort src/ tests/

lint-black: ## Format code with Black
	@echo "$(BLUE)Running black on backend...$(RESET)"
	@$(VENV)/bin/black src/ tests/

lint-ruff: ## Run Ruff checks
	@echo "$(BLUE)Running ruff on backend...$(RESET)"
	@$(VENV)/bin/ruff check src/
	@$(VENV)/bin/ruff check tests/

lint-interrogate: ## Run Interrogate for docstring coverage
	@echo "$(BLUE)Running interrogate on backend...$(RESET)"
	@$(VENV)/bin/interrogate -v src/ tests/

lint-mypy: ## Run MyPy for type checking
	@echo "$(BLUE)Running mypy on backend...$(RESET)"
	@$(VENV)/bin/mypy src/ --explicit-package-bases --ignore-missing-imports --show-error-codes
	@$(VENV)/bin/mypy tests/ --explicit-package-bases --ignore-missing-imports --show-error-codes

lint-pylint: ## Run Pylint for code quality checks
	@echo "$(BLUE)Running pylint on backend...$(RESET)"
	@$(VENV)/bin/pylint src/
	@$(VENV)/bin/pylint tests/

lint-cloc: ## Run CLOC for code statistics
	@echo "$(BLUE)Running cloc on backend...$(RESET)"
	@cloc src/
	@cloc tests/

########## UV ##########
uv-check-updates: ## Show outdated packages in the current venv
	@echo "$(YELLOW)Checking for outdated packages...$(RESET)"
	@$(UV_PIP) list --outdated
	@echo "$(GREEN)Finished check!$(RESET)"

uv-lock: ## Lock and update dependencies as specified in pyproject.toml
	@echo "$(YELLOW)Locking dependencies...$(RESET)"
	@$(UV_PIP) compile pyproject.toml --output-file ../cicd/requirements/requirements.txt --generate-hashes
	@$(UV_PIP) compile pyproject.toml --extra dev --output-file ../cicd/requirements/requirements_dev.txt --generate-hashes
	@$(UV_PIP) compile pyproject.toml --extra docs --output-file ../cicd/requirements/requirements_docs.txt --generate-hashes
	@$(UV) lock
	@echo "$(GREEN)Dependencies locked.$(RESET)"

uv-sync: ## Sync venv to match current lockfile
	@echo "$(YELLOW)Syncing venv to match current lockfile...$(RESET)"
	@$(UV_PIP) sync ../cicd/requirements/requirements.txt ../cicd/requirements/requirements_dev.txt
	@echo "$(GREEN)venv synced with current lockfile!$(RESET)"

uv-update-deps: ## Update everything to the newest versions allowed by pyproject.toml
	@echo "$(YELLOW)Exporting dependencies to requirements files...$(RESET)"
	@$(UV_PIP) install -r ../cicd/requirements/requirements.txt -r ../cicd/requirements/requirements_dev.txt --upgrade
	@echo "$(GREEN)Dependencies exported.$(RESET)"

########## DOCKER ##########
# WARNING: this deletes **all** local Docker data. Use with caution!
clean-docker: ## Remove every container, image, volume, network, build cache & history
	@echo "$(RED)Stopping and deleting all containers...$(RESET)"
	-@docker stop $$(docker ps -q) 2>/dev/null || true
	-@docker rm -f $$(docker ps -aq) 2>/dev/null || true
	@echo "$(RED)Tearing down every docker-compose project...$(RESET)"
	-@docker compose ls | tail -n +2 | awk '{print $$1}' | while read -r p; do \
		docker compose -p $$p down --rmi all --volumes --remove-orphans ; \
	done
	@echo "$(RED)Pruning dangling images, networks, and anonymous volumes...$(RESET)"
	-@docker system prune -a --volumes -f
	@echo "$(RED)Force deleting all named volumes...$(RESET)"
	-@docker volume rm -f $$(docker volume ls -q) 2>/dev/null || true
	@echo "$(RED)Cleaning BuildKit layer caches for every builder...$(RESET)"
	-@for b in $$(docker buildx ls --format '{{.Name}}'); do \
		docker buildx prune -af --builder $$b ; \
	done 2>/dev/null || true
	@echo "$(RED)Removing Buildx 'Builds' history records shown in Docker Desktop...$(RESET)"
	-@for b in $$(docker buildx ls --format '{{.Name}}'); do \
		docker buildx history rm --all --builder $$b ; \
	done 2>/dev/null || true
	@echo "Docker Desktop is now squeaky-clean ✔"

clean-docker-container = \
	@echo "$(RED)Stopping and removing container: $(1)...$(RESET)"; \
	docker stop $(1) || true; \
	docker rm $(1) || true; \
	docker system prune -f

########## TEST SETUP ##########
test: up-test run-tests down-test  ## Run backend tests
test-alembic: up-test run-tests-alembic down-test  ## Run alembic tests

up-test: up-pgvector up-redis

up-pgvector: ## Set up pg-vector container
	$(call clean-docker-container,pg-vector-test)
	@sleep 2
	@echo "$(GREEN)Starting a new pg-vector container...$(RESET)"
	@$(export-test-env) \
	docker run \
		--name pg-vector-test \
		-p $$POSTGRES_PORT:5432 \
		-e POSTGRES_PASSWORD \
		-e POSTGRES_USER \
		-e POSTGRES_DB \
		-d $(DOCKER_PG_VECTOR_IMAGE)
	@sleep 2
	@echo "$(GREEN)Running database migrations...$(RESET)"
	@$(export-test-env) \
	uv run alembic upgrade head

up-redis:
	$(call clean-docker-container,redis-test)
	@sleep 2
	@echo "$(GREEN)Starting a new Redis container...$(RESET)"
	@$(export-test-env) \
	docker run \
		--name redis-test \
     	-p $$REDIS_PORT:6379 \
	 	-d $(DOCKER_REDIS_IMAGE)

run-tests:
	@$(export-test-env) \
	$(UV) run pytest -rsPQ -m "not alembic" --cov-report term-missing --cov-config=./pyproject.toml --cov=. tests

# NB: `pytest-alembic` requires the DB engine to point an empty database. Thus, alembic
# tests should be run in isolation from other tests that may populate the DB.
run-tests-alembic:
	@$(export-test-env) \
	python -m pytest -rPQ -m "alembic" --cov-report term-missing --cov-config=./pyproject.toml --cov=. tests/test_alembic_migrations.py

########## TEST TEARDOWN ##########
down-test: down-pgvector down-redis ## Tear down all local test containers

down-pgvector: ## Tear down pg-vector container
	$(call clean-docker-container,pg-vector-test)

down-redis:
	$(call clean-docker-container,redis-test)
