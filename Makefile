# Karsa ASM — common commands
KARSA_SERVICES = karsa-data-engine karsa-live karsa-shadow karsa-backtest karsa-commander

.PHONY: rebuild up down logs restart-karsa

rebuild:  ## Rebuild karsa services only (keeps 9router/infra running)
	docker compose up -d --build $(KARSA_SERVICES)

up:  ## Start all services (no rebuild)
	docker compose up -d

down:  ## Stop all services
	docker compose down

logs:  ## Tail karsa service logs
	docker compose logs -f --tail=50 $(KARSA_SERVICES)

restart-karsa:  ## Restart karsa services without rebuild
	docker compose restart $(KARSA_SERVICES)
