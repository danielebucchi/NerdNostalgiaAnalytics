.PHONY: build build-lite up up-all down restart logs logs-lite shell shell-lite test ps clean

# --- Build ---
build:           ## Build the main bot image
	docker compose build bot

build-lite:      ## Build both bot + lite images
	docker compose --profile lite build

# --- Run ---
up:              ## Start only the main bot
	docker compose up -d bot

up-all:          ## Start main + lite bots
	docker compose --profile lite up -d

down:            ## Stop everything
	docker compose --profile lite down

restart:         ## Restart the main bot
	docker compose restart bot

restart-lite:    ## Restart the lite bot
	docker compose restart bot-lite

# --- Inspect ---
logs:            ## Follow main bot logs
	docker compose logs -f bot

logs-lite:       ## Follow lite bot logs
	docker compose logs -f bot-lite

ps:              ## List running services
	docker compose ps

shell:           ## Open a shell inside the running main bot
	docker compose exec bot bash

shell-lite:      ## Open a shell inside the running lite bot
	docker compose exec bot-lite bash

# --- Test ---
# pytest is a dev dep, not baked into the prod image. Prefer the local venv;
# fall back to ad-hoc install inside a one-off container otherwise.
test:            ## Run pytest (uses .venv if present, else container)
	@if [ -d .venv ]; then \
		.venv/bin/python -m pytest; \
	else \
		docker compose run --rm --no-deps bot sh -c "pip install -q pytest pytest-asyncio && python -m pytest"; \
	fi

# --- Maintenance ---
clean:           ## Remove containers + images (keeps volumes)
	docker compose --profile lite down --rmi local

help:            ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
