SCAFFOLD_ROOT := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
include $(SCAFFOLD_ROOT).scaffold-profile
include $(SCAFFOLD_ROOT)profiles/common.mk

.PHONY: profiles
profiles:
	@python3 scripts/profile_metadata.py --application '$(SCAFFOLD_PROFILE)' \
		--backup '$(SCAFFOLD_BACKUP_PROFILE)' --applications $(CI_PROFILES) \
		--backups $(CI_BACKUP_PROFILES)

include $(SCAFFOLD_ROOT)profiles/$(SCAFFOLD_PROFILE)/profile.mk


.PHONY: check format-check lint typecheck migrations-check framework-check test format
check: ## Run all source gates without deployment or application startup
	$(MAKE) format-check
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) migrations-check
	$(MAKE) framework-check
	$(MAKE) test COVERAGE=1

LOCAL_TLS ?= 0
up-prod: prepare-prod

.PHONY: prepare-prod
prepare-prod: ## Prepare and validate private production inputs without starting services
	@PROD_ENV_FILE="$(PROD_ENV_FILE)" bash "$(SCAFFOLD_ROOT)deploy/setup-env.sh"
	@set -o pipefail; prepare_interactive=0; \
		if [ -t 0 ]; then prepare_interactive=1; fi; \
		if [ "$(SCAFFOLD_BACKUP_PROFILE)" != "none" ]; then \
			PREPARE_INTERACTIVE=$$prepare_interactive PROD_ENV_FILE="$(PROD_ENV_FILE)" \
			python3 "$(SCAFFOLD_ROOT)scripts/validate_prod_config.py" --backup-path || exit 1; \
		fi; \
		$(PROD_CONFIG) config --format json 2>/dev/null | \
		PREPARE_INTERACTIVE=$$prepare_interactive PROD_ENV_FILE="$(PROD_ENV_FILE)" HOST_INGRESS="$(HOST_INGRESS)" HOST_INGRESS_HOST="$(PREPARE_PUBLIC_HOST)" \
		python3 "$(SCAFFOLD_ROOT)scripts/validate_prod_config.py" "$(PROD_APPLICATION_SERVICE)"
	# Self-signed certificate renewal is explicitly local-only.
	@if [ "$(HOST_INGRESS)" = "1" ]; then \
		echo "Host ingress owns TLS; skipping application certificate generation"; \
	elif [ "$(LOCAL_TLS)" = "1" ]; then \
		if [ ! -f deploy/nginx/certs/server.key ] || \
			! openssl x509 -in deploy/nginx/certs/server.crt -noout -checkend 86400 >/dev/null 2>&1; then \
			umask 077; mkdir -p deploy/nginx/certs; \
			openssl req -x509 -nodes -newkey rsa:2048 \
				-keyout deploy/nginx/certs/server.key -out deploy/nginx/certs/server.crt \
				-days 365 -subj '/CN=localhost' \
				-addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' || exit 1; \
		fi; \
		echo 'Local TLS certificate ready for localhost and 127.0.0.1'; \
	else \
		test -f deploy/nginx/certs/server.key && \
			openssl x509 -in deploy/nginx/certs/server.crt -noout -checkend 0 >/dev/null || \
			{ echo 'Install valid production TLS inputs, or use LOCAL_TLS=1 for the laptop dummy'; exit 1; }; \
	fi

.PHONY: migrations migrate superuser shell
migrations: ## Create migrations for intentional model changes
	$(DJANGO_DEV_MANAGE) makemigrations

migrate: ## Apply development database migrations
	$(DJANGO_DEV_MANAGE) migrate

superuser: migrate ## Migrate, then create an interactive Django superuser (development only)
	$(DJANGO_DEV_MANAGE) createsuperuser

shell: ## Open the Django shell (development only)
	$(DJANGO_DEV_MANAGE) shell

ifneq ($(SCAFFOLD_BACKUP_PROFILE),none)
include $(SCAFFOLD_ROOT)profiles/$(SCAFFOLD_BACKUP_PROFILE)/profile.mk
SELECTED_OPTIONAL_PROFILE_MAKEFILES := $(SELECTED_OPTIONAL_PROFILE_MAKEFILES) $(lastword $(MAKEFILE_LIST))
endif

include $(SCAFFOLD_ROOT)profiles/release-transport.mk
