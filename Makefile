# Public commands are defined in docs/makefile-design.md. Keep this dispatcher
# free of profile-specific recipes: every application uses the same operations.
SHELL := /bin/bash
.DEFAULT_GOAL := help
include .scaffold-profile

PROJECT_NAME ?= $(notdir $(CURDIR))
SCAFFOLD_BACKUP_PROFILE ?= none
CI_BACKUP_PROFILES ?= immutable-backup
export PROJECT_NAME SCAFFOLD_PROFILE SCAFFOLD_BACKUP_PROFILE CI_PROFILES CI_BACKUP_PROFILES
export COVERAGE DIFF_BASE RELEASE_REVISION RELEASE_IDENTITY RELEASE_PLATFORM RELEASE_IMAGE_PREFIX
export PROD_ENV_FILE DEPLOYMENT_STATE_DIR MONITORING CONFIRM BACKUP_SNAPSHOT BACKUP_SOURCE_ENV SELECT ENV_FILE
export RELEASE_BACKEND_SBOM RELEASE_WEB_SBOM

COMMANDS := help profiles setup hooks build publish download download-latest check \
	format-check lint typecheck migrations-check framework-check test image-check \
	deps-lock migrations format clean
ENVIRONMENTS := dev prod ephemeral
ENV_COMMANDS := prepare up restart rollback down rm backup snapshots restore migrate \
	superuser verify monitor-check backup-test integration-test config history ps logs shell console
SERVICE_COMMANDS := up down restart ps logs shell
TARGETS := $(COMMANDS) ci-ephemeral $(foreach env,$(ENVIRONMENTS),$(addsuffix -$(env),$(ENV_COMMANDS)))
SERVICE_TARGETS := $(foreach env,$(ENVIRONMENTS),$(addsuffix -$(env)-%,$(SERVICE_COMMANDS)))

.PHONY: $(TARGETS) .scaffold-force
$(TARGETS):
	@python3 scripts/scaffold.py "$@"

$(SERVICE_TARGETS): .scaffold-force
	@python3 scripts/scaffold.py "$@"

.scaffold-force:
