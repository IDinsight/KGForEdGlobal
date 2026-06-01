#!make

.DEFAULT_GOAL := help
.PHONY: clean-docker clean-docker-container help

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
PROJECT_NAME = SenegalKG
SHELL := /bin/bash

########## ENVIRONMENT VARIABLES FROM .env ##########
ifneq (,$(wildcard .env))  # Referenced globally inside the Makefile
  include .env
  export
endif

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
