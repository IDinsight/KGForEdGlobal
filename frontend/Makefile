
#!make

.DEFAULT_GOAL := help
.PHONY: clean fresh-env help lint

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

########## CLEAN UP ##########
# Delete all compiled Python files.
clean: ## Remove compiled files, caches, and testing artifacts
	@echo "$(BLUE)Cleaning frontend project files...$(RESET)"
	@+rm -rf \
		.cache \
		.eslintcache \
		.next \
		.parcel-cache \
		.playwright-cache \
		.stylelintcache \
		.svelte-kit \
		.turbo \
		.vite \
		.vitest \
		build \
		coverage \
		dist \
		node_modules/.vitest \
		playwright/.cache
	@pnpm store prune
	@echo "$(GREEN)Cleanup complete.$(RESET)"

########## FRONTEND ENVIRONMENT SETUP ##########
install: ## Create frontend environment (if missing) and install ALL dependencies
	@echo "$(YELLOW)Creating fresh frontend environment: $(PROJECT_NAME)$(RESET)"
	@pnpm install
	@echo "$(GREEN)Frontend environment setup complete.$(RESET)"

fresh-env: clean install ## Create a fresh frontend environment

########## LINTING ##########
lint: lint-prettier lint-eslint lint-typecheck lint-cloc ## Run all linter and formatter checks

lint-prettier: ## Run Prettier checks
	@echo "$(BLUE)Running prettier on frontend...$(RESET)"
	@pnpm prettier --write "{src,tests}/**/*.{js,ts,jsx,tsx,mjs,cjs}"
	@echo "$(GREEN)Linting and formatting complete.$(RESET)"

lint-eslint: ## Run ESLint checks
	@echo "$(BLUE)Running eslint on frontend...$(RESET)"
	@pnpm eslint --config eslint.config.mjs --fix --format stylish --no-error-on-unmatched-pattern "{src,tests}/**/*.{js,ts,jsx,tsx,mjs,cjs}"
	@echo "$(GREEN)Linting and formatting complete.$(RESET)"

lint-typecheck: ## Run TypeScript type checks
	@echo "$(BLUE)Running TypeScript type checks on frontend...$(RESET)"
	@pnpm tsc --noEmit
	@echo "$(GREEN)Type checking complete.$(RESET)"

lint-cloc: ## Run CLOC for code statistics
	@echo "$(BLUE)Running cloc on frontend...$(RESET)"
	@cloc src/
	@cloc tests/
