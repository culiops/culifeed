# v2 Cutover: Host Docker-Compose Deployment

**Date:** 2026-04-29
**Branch:** `feat/topic-matching-v2`
**Status:** Design approved, pending implementation

## Goal

Cut CuliFeed production over from the GHCR image-and-SSH-deploy pipeline to a host-resident `docker compose` setup that runs the unmerged `feat/topic-matching-v2` branch directly. Keep Docker as the runtime (preserves supervisord, env injection, restart policy, sqlitebrowser sidecar) but eliminate the build-push-deploy cycle so we can iterate by `git pull && docker compose restart`.

## Non-goals

- Permanent abandonment of the Docker image release flow (keep the Dockerfile and workflows; we just stop auto-firing them).
- Migrating off SQLite or restructuring data directories beyond what's needed for the move.
- v2 algorithm work (already done on this branch and verified against today's snapshot).

## Architecture

Two services managed by one compose file, committed at the repo root.

| Component | Old | New |
|---|---|---|
| Image source | GHCR `ghcr.io/chiplonton/culifeed:1.4.2-alpine` | Local build from this branch's `Dockerfile.alpine` |
| Compose file | `/home/ubuntu/culifeed/docker-compose.yml` (host home dir) | `/home/claude/culifeed/docker-compose.yml` (repo root) |
| Source code | Baked into image | Bind-mounted from `/home/claude/culifeed` |
| Data | `/home/ubuntu/culifeed/data/culifeed.db` | `/home/claude/culifeed/data/culifeed.db` |
| Container UID | `culifeed` uid 1000 | `culifeed` uid 1001 (matches host `claude`) |
| v2 toggle | n/a | `CULIFEED_FILTERING__USE_EMBEDDING_PIPELINE=true` in env file |
| Update flow | git push → GH builds image → SSH deploy | `git pull && docker compose restart` |

## Components

### 1. `docker-compose.yml` (new, repo root)

```yaml
services:
  culifeed-prd:
    build:
      context: .
      dockerfile: Dockerfile.alpine
      args:
        UID: 1001
        GID: 1001
    image: culifeed:local
    container_name: culifeed-prd
    restart: unless-stopped
    env_file:
      - .env.prd
    environment:
      - TZ=UTC
      - CULIFEED_FILTERING__USE_EMBEDDING_PIPELINE=true
    volumes:
      - .:/app
      - ./data:/app/data
      - ./logs:/app/logs
    healthcheck:
      test: ["CMD", "python", "-c", "import sys; sys.exit(0)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  sqlitebrowser:
    image: linuxserver/sqlitebrowser
    container_name: sqlitebrowser
    restart: unless-stopped
    ports:
      - "100.76.118.121:3001:3001"
    volumes:
      - ./sqlitebrowser/config:/config
      - ./data:/data
    environment:
      - PUID=1001
      - PGID=1001
      - TZ=Etc/UTC
```

The `.:/app` mount makes host edits visible to the container immediately. The deeper `./data:/app/data` mount overrides the source-mount path for the DB so it's always at the host data dir.

### 2. `Dockerfile.alpine` UID build args

Replace the existing user-creation block with one that honors `UID`/`GID` build args:

```dockerfile
ARG UID=1000
ARG GID=1000
RUN addgroup -g ${GID} culifeed && adduser -u ${UID} -G culifeed -D culifeed
```

Default values (1000) preserve compatibility with the old image flow if anyone rebuilds without args.

### 3. `.env.prd` (new, gitignored)

Populated from the running container's environment via `docker exec culifeed-prd env | grep ^CULIFEED_ > .env.prd && chmod 600 .env.prd`. Contains the 9 production variables (Telegram bot token, AI provider keys, SaaS settings). Should NOT be committed.

`.gitignore` must already cover `.env*`, `data/`, `logs/`, `sqlitebrowser/config/`. Verify and add anything missing.

### 4. GitHub Actions

Both workflows continue to exist but are gated behind `workflow_dispatch` only — no auto-firing on push to `main` or on prior workflow completions.

- `deploy-production.yml`: comment out the `on.workflow_run` block, keep `workflow_dispatch`.
- `docker-build-push.yml`: same treatment to save CI minutes (the images would go unused).

These changes land on `feat/topic-matching-v2` so when we eventually merge, the disabled state lands on `main`.

### 5. `OPERATIONS.md` (new)

Short runbook documenting:
- Pulling updates: `git pull && docker compose restart`
- Dependency update: `docker compose up -d --build`
- Inspecting: `docker compose logs -f culifeed-prd`, `docker compose exec culifeed-prd supervisorctl status`
- Rollback paths (R0/R1/R2 below)
- v2 flag flip command

## Cutover sequence

Each step is reversible until step 7. Steps 1–6 happen while the old container is still serving.

1. **Backup** the live DB: `sudo cp /home/ubuntu/culifeed/data/culifeed.db /home/claude/culifeed/data/culifeed.db.backup-pre-v2-$(date +%s)`
2. **Checkpoint and copy** the live DB: `docker exec culifeed-prd sqlite3 /app/data/culifeed.db "PRAGMA wal_checkpoint(TRUNCATE);"` then `sudo cp /home/ubuntu/culifeed/data/culifeed.db /home/claude/culifeed/data/culifeed.db && sudo chown claude:claude /home/claude/culifeed/data/culifeed.db`
3. **Capture env**: `docker exec culifeed-prd env | grep ^CULIFEED_ > /home/claude/culifeed/.env.prd && chmod 600 /home/claude/culifeed/.env.prd`
4. **Schema migrate** the copy: `python -c "from culifeed.database.schema import DatabaseSchema; DatabaseSchema('data/culifeed.db').create_tables()"` (idempotent, adds v2 columns and vec0 tables)
5. **Topic description backfill**: `python scripts/backfill_topic_descriptions.py --db data/culifeed.db`
6. **v2 historical backfill**: `python scripts/backfill_v2_processing.py --db data/culifeed.db` (writes v2 rows with `delivered=1` so the scheduler won't resend old articles)
7. **Stop old**: `docker stop culifeed-prd && docker rm culifeed-prd` — start of downtime window
8. **Pre-flight smoke** the new image: `docker build -f Dockerfile.alpine --build-arg UID=1001 -t culifeed:local . && docker run --rm culifeed:local python -c "import sqlite_vec, openai, culifeed; print('ok')"`
9. **Bring up new**: `cd /home/claude/culifeed && docker compose up -d`
10. **Verify** (see gates below). If any gate fails → rollback (R1).
11. **Disable GH Actions** by committing the `workflow_dispatch`-only changes from component 4.

Total expected downtime: 30–60s (steps 7–9).

## Verification gates (post-cutover)

Block on these in order. Failure → rollback path noted.

| # | Check | Failure → |
|---|---|---|
| 1 | `docker compose ps` shows both services `healthy` within 2 minutes | R1 |
| 2 | `docker compose logs --tail=200 culifeed-prd` has no fresh `ERROR`/`Traceback` after startup | R1 |
| 3 | Telegram bot replies to `/help` in operator's chat | R1 |
| 4 | `sqlite3 data/culifeed.db "SELECT COUNT(*) FROM processing_results WHERE pipeline_version='v2'"` ≥ 500 (backfill rows present; today's verification produced 578) | R2 |
| 5 | After next scheduler tick (or manual trigger): new articles get fresh v2 rows; PASS rows have `delivered=0` and get pushed to Telegram | R0 if quality bad |

## Error handling and rollback

**Risks ranked by blast radius:**

1. **Bot polling conflict** — Telegram permits one long-polling client. The compose `container_name: culifeed-prd` clashing with a still-running old container will surface immediately. Step 7 must finish before step 9.
2. **New container fails to start** — caught by the pre-flight smoke (step 8) before cutover. If it still fails after `compose up`, rollback R1.
3. **DB corruption** — migration runs on the copy at `/home/claude/culifeed/data/`, never on the live DB until step 7. R2 restores from backup.
4. **v2 produces wrong matches** — backfill rows are `delivered=1` so they can't re-deliver. Bad live matches → R0 flips the flag.
5. **Permission mismatch** — UID alignment (1001) and explicit `chown` should prevent it; if it surfaces, R1.

**Rollback paths (fastest first):**

- **R0 — flag flip:** `sed -i 's/EMBEDDING_PIPELINE=true/EMBEDDING_PIPELINE=false/' .env.prd && docker compose restart`. Back on v1 path inside the new container in ~5s. Use when v2 quality is bad but the runtime is healthy.
- **R1 — revert to old image:** `docker compose down && docker run -d --name culifeed-prd --restart unless-stopped --env-file /home/claude/culifeed/.env.prd -v /home/ubuntu/culifeed/data:/app/data -v /home/ubuntu/culifeed/logs:/app/logs ghcr.io/chiplonton/culifeed:1.4.2-alpine`. ~20s.
- **R2 — restore DB from backup, then R1:** `docker compose down && sudo cp /home/claude/culifeed/data/culifeed.db.backup-pre-v2-<ts> /home/ubuntu/culifeed/data/culifeed.db` then R1.

## Open questions

- Sqlitebrowser sidecar: copy `sqlitebrowser/config/` from `/home/ubuntu/culifeed/sqlitebrowser/config/`, or let it initialize fresh? Decide during implementation.
- Whether to delete the `/home/ubuntu/culifeed/` working directory after cutover or leave as immediate fallback. Recommend leaving in place for at least the shadow-validation week.

## Out of scope

- Decoupling the bot and scheduler into separate containers (they share supervisord today; not changing that).
- Adding observability/metrics beyond what already exists in container logs.
- Adjusting the v2 embedding threshold based on shadow-validation findings (separate follow-up after the cutover proves stable).
