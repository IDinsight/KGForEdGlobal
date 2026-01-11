# SenegalKG

<!-- Badges -->
<p style="text-align: center;">
  <a href="https://github.com/econchick/interrogate">
    <img src="./interrogate_badge.svg" alt="Docstring coverage: interrogate">
  </a>
  &nbsp;
  <a href="https://github.com/pylint-dev/pylint">
    <img src="https://img.shields.io/badge/linting-pylint-yellowgreen" alt="Linting: pylint">
  </a>
</p>

## Table of Contents

- [Setup Instructions](#setup-instructions)
- [Local Startup Instructions](#local-startup-instructions)
- [Local Clean up Instructions](#local-clean-up-instructions)
- [The Pipeline](#the-pipeline)

## Setup Instructions

1. Install [direnv](https://direnv.net/docs/installation.html).
2. If you are using `zsh`, then add `eval "$(direnv hook zsh)` to the end of your `~/.zshrc` file. If you are using `bash`, then add `eval "$(direnv hook bash)"` to the end of your `~/.bashrc` (or `~/.bash_profile`) file. Ensure you reload the file by running `source ~/.zshrc` or `source ~/.bashrc` (or `source ~/.bash_profile`).
3. Install the latest version of [uv](https://docs.astral.sh/uv/) using: `curl -LsSf https://astral.sh/uv/install.sh | sh`
4. Run `git clone git@github.com:IDinsight/SenegalKG.git` and cd into the root directory of the repo.
5. In the root `.envrc` file, ensure `PROJECT_ENV` is set to `local`.
6. Copy the **root** `.template.env` to `.env` and update the following environment variables in `.env`:
    1. `OPENAI_API_KEY`: Your OpenAI API key.
    2. `PATHS_PROJECT_DIR`: The absolute path to the root directory of the project.
7. Copy the **root** `.template.env.local` to `.env.local`.
8. Allow `direnv` to load the root environment variables by running `direnv allow`.
9. cd into the backend directory of the repo and:
    1. Copy `.template.env.local` to `.env.local`.
    2. Allow `direnv` to load the backend environment variables by running `direnv allow`.

## Local Startup Instructions

1. cd into the `backend` directory of the repo and:
    1. Run `make fresh-env`. This will create a new virtual environment for the backend and install all dependencies.
    2. Run `source .venv/bin/activate`: This will activate the virtual environment created by `make fresh-env`.

## Local Clean up Instructions

1. In the backend directory, run `deactivate`. This will exit out of the virtual environment created by `uv`.

## The Pipeline

The SenegalKG pipeline currently converts a raw curriculum PDF document from non-U.S.
countries into a knowledge graph that follows the [Learning Commons ontology](https://docs.learningcommons.org/knowledge-graph/v1-2-0/understanding-knowledge-graph/about-knowledge-graph)
through the following steps:

### Step 1: Structural Per-Page Intermediate Representation (IR) Data Extraction From PDF

### Step 2: Verifying Continuity of Extracted Page IR JSONs

### Step 3: Stitching Single Document IR JSON From (Verified) Per-Page IR JSONs

### Step 4: Canonical Intermediate Representation

### Step 5: Knowledge Graph Construction
