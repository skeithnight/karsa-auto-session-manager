# Karsa ASM — common commands
INFRA = docker compose -f docker-compose.infra.yml
APPS  = docker compose -f docker-compose.apps.yml
ALL   = docker compose -f docker-compose.infra.yml -f docker-compose.apps.yml

.PHONY: up down rebuild restart-apps logs logs-infra

up:  ## Start infra + apps (first time / cold start)
	$(ALL) up -d

down:  ## Stop everything (preserves volumes)
	$(ALL) down

rebuild:  ## Rebuild apps only (infra untouched)
	$(APPS) up -d --build

restart-apps:  ## Restart apps without rebuild
	$(APPS) restart

logs:  ## Tail app logs
	$(APPS) logs -f --tail=50

logs-infra:  ## Tail infra logs
	$(INFRA) logs -f --tail=50
