PROJECT_NAME ?= $(notdir $(CURDIR))
BACKUP_POSTGRES_SOURCE ?= postgres:16.10-bookworm
BACKUP_RESTIC_SOURCE ?= restic/restic:0.19.1
BACKUP_RCLONE_SOURCE ?= rclone/rclone:1.74.4
BACKUP_POSTGRES_IMAGE ?= $(BACKUP_POSTGRES_SOURCE)@sha256:38471f330eb885e04de130b768d6db4e10469e2311879c7e5c699f6d2d8a1c74
BACKUP_RESTIC_IMAGE ?= $(BACKUP_RESTIC_SOURCE)@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510
BACKUP_RCLONE_IMAGE ?= $(BACKUP_RCLONE_SOURCE)@sha256:c61954aaa32328a5486715dd063a81c7879f5195ad3505cd362deddd509dc4a1
BACKUP_IMAGE ?= local/$(PROJECT_NAME)-backup:$(shell git rev-parse HEAD)
BACKUP_DOCKERFILE := profiles/immutable-backup/Dockerfile

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
		&& test -s /etc/ssl/certs/ca-certificates.crt'

.PHONY: backup-image-versions
backup-image-versions: build-backup-image ## Print the exact tools installed in the backup image
	docker run --rm --entrypoint sh $(BACKUP_IMAGE) -c \
		'pg_dump --version; restic version; rclone version'

verify: verify-backup-image
