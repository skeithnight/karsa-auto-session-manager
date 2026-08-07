# Karsa ASM — common commands
INFRA = docker compose -f docker-compose.infra.yml
APPS  = docker compose -f docker-compose.apps.yml
ALL   = docker compose -f docker-compose.infra.yml -f docker-compose.apps.yml

.PHONY: up down rebuild restart-apps logs logs-infra cleanup disk-check db-maintenance db-backup

up:  ## Start infra + apps (first time / cold start)
	$(INFRA) up -d
	$(APPS) up -d

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

cleanup:  ## Prune Docker disk usage (safe — unused only)
	./scripts/docker_cleanup.sh

disk-check:  ## Alert if disk usage > 80%
	@./scripts/docker_cleanup.sh --check-only

db-maintenance:  ## Daily DB backup + cleanup (retention: 30d candles, 7d signals)
	./scripts/db_maintenance.sh

db-backup:  ## Backup DB only (no cleanup)
	./scripts/db_maintenance.sh --backup-only
