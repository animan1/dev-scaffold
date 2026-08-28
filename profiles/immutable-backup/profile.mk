PROJECT_NAME ?= $(notdir $(CURDIR))
BACKUP_POSTGRES_SOURCE ?= postgres:16.10-bookworm
BACKUP_RESTIC_SOURCE ?= restic/restic:0.19.1
BACKUP_RCLONE_SOURCE ?= rclone/rclone:1.74.4
BACKUP_POSTGRES_IMAGE ?= $(BACKUP_POSTGRES_SOURCE)@sha256:38471f330eb885e04de130b768d6db4e10469e2311879c7e5c699f6d2d8a1c74
BACKUP_RESTIC_IMAGE ?= $(BACKUP_RESTIC_SOURCE)@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510
BACKUP_RCLONE_IMAGE ?= $(BACKUP_RCLONE_SOURCE)@sha256:c61954aaa32328a5486715dd063a81c7879f5195ad3505cd362deddd509dc4a1
BACKUP_IMAGE ?= local/$(PROJECT_NAME)-backup:$(shell git rev-parse HEAD)
BACKUP_DOCKERFILE := profiles/immutable-backup/Dockerfile
BACKUP_COMPOSE_FILE := profiles/immutable-backup/compose.yml
BACKUP_EXERCISE_COMPOSE_FILE := profiles/immutable-backup/exercise.compose.yml
BACKUP_EXERCISE_ROOT ?= $(CURDIR)/.tmp/immutable-backup-exercise
BACKUP_EXERCISE_PROJECT ?= $(PROJECT_NAME)-backup-exercise
BACKUP_DATABASE_SERVICE ?= db
BACKUP_LOCAL_PATH ?= $(CURDIR)/deploy/backups
BACKUP_RCLONE_CONFIG_DIR ?= $(CURDIR)/deploy/rclone
BACKUP_SERVICE ?= backup
BACKUP_SNAPSHOT ?= latest
BACKUP_RESTORE_CONFIRM_VALUE := restore-production-database
BACKUP_BUILD ?= $(MAKE) build-backup-image
export BACKUP_IMAGE BACKUP_LOCAL_PATH BACKUP_RCLONE_CONFIG_DIR

ifeq ($(SCAFFOLD_PROFILE),react-vite)
PROD_COMPOSE_FILES += -f $(BACKUP_COMPOSE_FILE)
BACKUP_COMPOSE = $(COMPOSE_PROD)
BACKUP_WRITER_SERVICES ?= backend monitor backup
else ifeq ($(SCAFFOLD_PROFILE),server-rendered-django)
RELEASE_COMPOSE_FILES += -f $(BACKUP_COMPOSE_FILE)
BACKUP_COMPOSE = $(COMPOSE_RELEASE)
BACKUP_WRITER_SERVICES ?= app monitor backup
else
$(error The immutable-backup profile needs a production Compose contract for SCAFFOLD_PROFILE=$(SCAFFOLD_PROFILE))
endif

BACKUP_RUN = $(BACKUP_COMPOSE) run --rm --no-deps $(BACKUP_SERVICE)
BACKUP_EXERCISE_COMPOSE = COMPOSE_PROJECT_NAME=$(BACKUP_EXERCISE_PROJECT) \
	BACKUP_IMAGE=$(BACKUP_IMAGE) BACKUP_LOCAL_PATH=$(BACKUP_EXERCISE_ROOT)/repository \
	BACKUP_RCLONE_CONFIG_DIR=$(BACKUP_EXERCISE_ROOT)/rclone \
	BACKUP_POSTGRES_IMAGE=$(BACKUP_POSTGRES_IMAGE) BACKUP_REPOSITORY=/backups \
	BACKUP_PASSWORD=exercise-only POSTGRES_DB=backup_exercise \
	POSTGRES_USER=backup_exercise POSTGRES_PASSWORD=exercise-only \
	docker compose --project-directory . -f $(BACKUP_COMPOSE_FILE) \
	-f $(BACKUP_EXERCISE_COMPOSE_FILE)
BACKUP_EXERCISE_RUN = $(BACKUP_EXERCISE_COMPOSE) run --rm --no-deps $(BACKUP_SERVICE)

.PHONY: backup-images-pull
backup-images-pull: ## Pull the versioned backup component images used for dependency review
	docker pull $(BACKUP_POSTGRES_SOURCE)
	docker pull $(BACKUP_RESTIC_SOURCE)
	docker pull $(BACKUP_RCLONE_SOURCE)

.PHONY: backup-images-digests
backup-images-digests: backup-images-pull ## Print candidate immutable backup component digests
	docker image inspect $(BACKUP_POSTGRES_SOURCE) $(BACKUP_RESTIC_SOURCE) \
		$(BACKUP_RCLONE_SOURCE) --format '{{index .RepoDigests 0}}'

.PHONY: build-backup-image
build-backup-image: ## Build the profile-owned backup tool image from immutable inputs
	docker build -f $(BACKUP_DOCKERFILE) \
		--build-arg POSTGRES_IMAGE=$(BACKUP_POSTGRES_IMAGE) \
		--build-arg RESTIC_IMAGE=$(BACKUP_RESTIC_IMAGE) \
		--build-arg RCLONE_IMAGE=$(BACKUP_RCLONE_IMAGE) \
		-t $(BACKUP_IMAGE) .

.PHONY: verify-backup-image
verify-backup-image: build-backup-image ## Verify the exact backup image tool and user contract
	@test "$$(docker image inspect --format='{{.Config.User}}' $(BACKUP_IMAGE))" = "scaffold-backup"
	docker run --rm --entrypoint sh $(BACKUP_IMAGE) -c \
		'pg_dump --version | grep -q "16.10" \
		&& restic version | grep -q "restic 0.19.1" \
		&& rclone version | grep -q "rclone v1.74.4" \
		&& jq --version \
		&& test -x /usr/local/bin/scaffold-backup \
		&& test -s /etc/ssl/certs/ca-certificates.crt'

.PHONY: verify-backup-compose
verify-backup-compose: ## Validate the application-neutral backup worker overlay
	docker compose --project-directory . -f $(BACKUP_COMPOSE_FILE) config --quiet
	$(BACKUP_EXERCISE_COMPOSE) config --quiet

.PHONY: prepare-backup-exercise
prepare-backup-exercise: build-backup-image
	@mkdir -p $(BACKUP_EXERCISE_ROOT)/repository $(BACKUP_EXERCISE_ROOT)/rclone
	$(BACKUP_EXERCISE_COMPOSE) down -v --remove-orphans
	$(BACKUP_EXERCISE_COMPOSE) run --rm --no-deps backup-init
	$(BACKUP_EXERCISE_COMPOSE) run --rm --no-deps --user 0:0 \
		--entrypoint sh $(BACKUP_SERVICE) -c 'find /backups -mindepth 1 -delete'
	$(BACKUP_EXERCISE_COMPOSE) up -d --wait $(BACKUP_DATABASE_SERVICE)

.PHONY: exercise-backup-profile
exercise-backup-profile: prepare-backup-exercise ## Run real backup and isolated restore verification against disposable PostgreSQL
	$(BACKUP_EXERCISE_RUN) once
	$(BACKUP_EXERCISE_RUN) verify
	$(BACKUP_EXERCISE_RUN) snapshots
	$(BACKUP_EXERCISE_COMPOSE) run --rm --no-deps --entrypoint sh \
		$(BACKUP_SERVICE) -c 'test -s /backup-status/last-backup \
		&& test -s /backup-status/last-restore-verification \
		&& test "$$(PGPASSWORD="$${POSTGRES_PASSWORD}" psql \
			--host db --username backup_exercise \
			--dbname backup_exercise --tuples-only --no-align \
			--command "SELECT count(*) FROM pg_database \
			WHERE datname LIKE '\''backup_restore_verify_%'\''")" = 0'
	$(MAKE) down-backup-exercise

.PHONY: logs-backup-exercise
logs-backup-exercise: ## Show disposable backup exercise logs
	$(BACKUP_EXERCISE_COMPOSE) logs --tail=200

.PHONY: down-backup-exercise
down-backup-exercise: ## Remove only the disposable backup exercise data
	$(BACKUP_EXERCISE_COMPOSE) run --rm --no-deps --user 0:0 \
		--entrypoint sh $(BACKUP_SERVICE) -c 'find /backups -mindepth 1 -delete'
	$(BACKUP_EXERCISE_COMPOSE) down -v --remove-orphans

.PHONY: backup-image-versions
backup-image-versions: build-backup-image ## Print the exact tools installed in the backup image
	docker run --rm --entrypoint sh $(BACKUP_IMAGE) -c \
		'pg_dump --version; restic version; rclone version'

.PHONY: backup-database-ready
backup-database-ready:
	$(BACKUP_COMPOSE) up -d --wait $(BACKUP_DATABASE_SERVICE)

.PHONY: backup-storage-ready
backup-storage-ready:
	@mkdir -p $(BACKUP_LOCAL_PATH) $(BACKUP_RCLONE_CONFIG_DIR)
	$(BACKUP_COMPOSE) run --rm --no-deps backup-init

.PHONY: backup-prod
backup-prod: ## Create, retain, prune, and integrity-check an encrypted database backup
	$(BACKUP_BUILD)
	$(MAKE) backup-storage-ready
	$(MAKE) backup-database-ready
	$(BACKUP_RUN) once

.PHONY: verify-backup-prod
verify-backup-prod: ## Restore the latest backup into an isolated temporary database
	$(BACKUP_BUILD)
	$(MAKE) backup-storage-ready
	$(MAKE) backup-database-ready
	$(BACKUP_RUN) verify

.PHONY: inspect-backup-prod
inspect-backup-prod: ## Refresh backup freshness from the existing repository
	$(BACKUP_BUILD)
	$(MAKE) backup-storage-ready
	$(BACKUP_RUN) inspect

.PHONY: snapshots-prod
snapshots-prod: ## List restorable database snapshots without creating a repository
	$(BACKUP_BUILD)
	$(MAKE) backup-storage-ready
	$(BACKUP_RUN) snapshots

.PHONY: up-backup-prod
up-backup-prod: ## Start the scheduled backup and restore-verification worker
	$(BACKUP_BUILD)
	$(MAKE) backup-storage-ready
	$(MAKE) backup-database-ready
	$(BACKUP_COMPOSE) up -d --no-build $(BACKUP_SERVICE)

.PHONY: logs-backup-prod
logs-backup-prod: ## Show recent scheduled backup worker logs
	$(BACKUP_COMPOSE) logs --tail=200 $(BACKUP_SERVICE)

.PHONY: restore-prod
restore-prod: ## Replace the configured database from BACKUP_SNAPSHOT; requires CONFIRM=restore-prod
	@if [[ "$(CONFIRM)" != "restore-prod" ]]; then \
		echo "Refusing to replace the configured database."; \
		echo "Run: make restore-prod CONFIRM=restore-prod BACKUP_SNAPSHOT=$(BACKUP_SNAPSHOT)"; \
		exit 1; \
	fi
	$(BACKUP_BUILD)
	$(MAKE) backup-storage-ready
	$(BACKUP_COMPOSE) stop $(BACKUP_WRITER_SERVICES)
	$(MAKE) backup-database-ready
	$(BACKUP_COMPOSE) run --rm --no-deps \
		-e BACKUP_RESTORE_CONFIRMATION=$(BACKUP_RESTORE_CONFIRM_VALUE) \
		-e BACKUP_RESTORE_SNAPSHOT=$(BACKUP_SNAPSHOT) $(BACKUP_SERVICE) restore
	@echo "Database restore complete; run the consuming project's migration, startup, and smoke procedures."

verify: verify-backup-image verify-backup-compose
