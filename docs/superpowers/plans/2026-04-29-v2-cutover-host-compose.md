# v2 Cutover: Host Docker-Compose Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move CuliFeed production from the GHCR image-and-SSH-deploy pipeline to a host-resident `docker compose` setup running the unmerged `feat/topic-matching-v2` branch directly, so future updates are `git pull && docker compose restart`.

**Architecture:** Local Docker image build from this branch's `Dockerfile.alpine` (with UID/GID build args matching host user 1001), bind-mount of `/home/claude/culifeed` into `/app`, separate `./data` and `./logs` mounts for persistence, two services (`culifeed-prd` + `sqlitebrowser`) defined in a committed `docker-compose.yml`. v2 enabled via `CULIFEED_FILTERING__USE_EMBEDDING_PIPELINE=true` in `.env.prd`.

**Tech Stack:** Docker, docker compose v2, Alpine Linux base image, Python 3.11, SQLite + sqlite-vec, supervisord (in-container), systemd (for `loginctl enable-linger` so containers survive reboot if started under user services — already handled by Docker daemon).

**Spec:** `docs/superpowers/specs/2026-04-29-v2-cutover-host-compose-design.md`

**Important note about this plan:** This is a deployment cutover, not feature code. "Tests" here are operational verifications (build success, container health, smoke imports, data integrity) rather than pytest cases. Each task still has an explicit verify step before commit/proceed.

**Constraints repeated for every commit:**
- This repo is public — NO `Co-Authored-By` trailers, NO AI attribution.
- Plain commit messages only.

**Execution order:** Phases A → B → C → D in sequence. Phase B operates on a copy of the live DB while the old container keeps serving — no downtime until Phase C. Phase C is the only step that takes the bot offline (~30–60s).

---

## Phase A — Repository preparation (no live impact)

### Task A1: Add UID/GID build args to `Dockerfile.alpine`

**Files:**
- Modify: `Dockerfile.alpine`

- [ ] **Step 1: Read the current user-creation block**

```bash
grep -n "adduser\|addgroup\|culifeed:culifeed" Dockerfile.alpine
```

Expected: existing line uses `RUN adduser -D -s /bin/bash culifeed && mkdir -p /app/logs && chown -R culifeed:culifeed /app`.

- [ ] **Step 2: Replace with parameterized version**

In `Dockerfile.alpine`, replace the existing `RUN adduser ...` block with:

```dockerfile
ARG UID=1000
ARG GID=1000
RUN addgroup -g ${GID} culifeed && \
    adduser -u ${UID} -G culifeed -s /bin/bash -D culifeed && \
    mkdir -p /app/logs && \
    chown -R culifeed:culifeed /app
```

The `ARG` lines must come BEFORE the `RUN`. Defaults of 1000 preserve compatibility with existing image flow if anyone rebuilds without args.

- [ ] **Step 3: Verify default build still works**

```bash
docker build -f Dockerfile.alpine -t culifeed:test-default .
docker run --rm culifeed:test-default id culifeed
```

Expected output: `uid=1000(culifeed) gid=1000(culifeed) groups=1000(culifeed)`

- [ ] **Step 4: Verify parameterized build works**

```bash
docker build -f Dockerfile.alpine --build-arg UID=1001 --build-arg GID=1001 -t culifeed:test-1001 .
docker run --rm culifeed:test-1001 id culifeed
```

Expected output: `uid=1001(culifeed) gid=1001(culifeed) groups=1001(culifeed)`

- [ ] **Step 5: Verify v2 deps importable in the new image**

```bash
docker run --rm culifeed:test-1001 python -c "import sqlite_vec, openai, culifeed; print('ok')"
```

Expected output: `ok` (no traceback).

- [ ] **Step 6: Cleanup test images**

```bash
docker rmi culifeed:test-default culifeed:test-1001
```

- [ ] **Step 7: Commit**

```bash
git add Dockerfile.alpine
git commit -m "build(docker): accept UID/GID build args in alpine image"
```

---

### Task A2: Update `.gitignore` for new files

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Inspect current ignores**

```bash
grep -nE "^\.env|^data|^logs|sqlitebrowser" .gitignore
```

Current: `.env`, `logs/`, `data/`, `.env.local`, `.env.production` are present.

- [ ] **Step 2: Verify `.env.prd` will be ignored**

```bash
echo > .env.prd && git status --porcelain .env.prd
rm .env.prd
```

Expected output: empty (file is ignored). If it shows `?? .env.prd`, add `.env.prd` to `.gitignore`. If empty, the existing `.env` rule is too narrow only matching that exact filename — verify by:

```bash
git check-ignore -v .env.prd
```

If this returns nothing, edit `.gitignore` and add (in the section near other `.env` entries):

```
.env.prd
sqlitebrowser/config/
```

- [ ] **Step 3: Verify `sqlitebrowser/config/` will be ignored**

```bash
mkdir -p sqlitebrowser/config && touch sqlitebrowser/config/test.x && git status --porcelain sqlitebrowser/
rm -rf sqlitebrowser/
```

If `?? sqlitebrowser/` shows, add `sqlitebrowser/config/` to `.gitignore`.

- [ ] **Step 4: Commit (only if changes were needed)**

```bash
git add .gitignore
git commit -m "chore(gitignore): add .env.prd and sqlitebrowser config"
```

If no changes needed (existing rules sufficient), skip this commit.

---

### Task A3: Create `docker-compose.yml` at repo root

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Write the file**

Create `/home/claude/culifeed/docker-compose.yml` with the following content:

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

- [ ] **Step 2: Validate compose syntax**

A real validation requires `.env.prd` to exist. Create a placeholder for validation:

```bash
touch .env.prd
docker compose config > /tmp/compose-rendered.yml
rm .env.prd
```

Expected: command exits 0 and `/tmp/compose-rendered.yml` shows both services with the correct image names, volumes, and env entries (including `CULIFEED_FILTERING__USE_EMBEDDING_PIPELINE=true`).

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "build(compose): add host docker-compose config for v2 cutover"
```

---

### Task A4: Disable auto-deploy on `deploy-production.yml`

**Files:**
- Modify: `.github/workflows/deploy-production.yml`

- [ ] **Step 1: Inspect current `on:` block**

```bash
sed -n '1,20p' .github/workflows/deploy-production.yml
```

Current:
```yaml
on:
  workflow_run:
    workflows: ["Build and Push Docker Image"]
    types: [completed]
    branches: [main]
  workflow_dispatch:
    inputs:
      ...
```

- [ ] **Step 2: Remove the `workflow_run` block**

Edit `.github/workflows/deploy-production.yml`. Replace the entire `on:` block (the `workflow_run` and the `workflow_dispatch` together) with `workflow_dispatch` only:

```yaml
on:
  workflow_dispatch:
    inputs:
      image_tag:
        description: 'Docker image tag to deploy (e.g., latest, latest-alpine, v1.2.0, v1.2.0-alpine)'
        required: true
        default: 'latest-alpine'
        type: string
      variant:
        description: 'Image variant to deploy'
        required: true
        default: 'alpine'
        type: choice
        options:
        - debian
        - alpine
```

- [ ] **Step 3: Remove the `workflow_run`-conditional in the job**

In the same file, find and remove the line `if: ${{ github.event.workflow_run.conclusion == 'success' }}` (currently around line 33). The job should run unconditionally on `workflow_dispatch`.

- [ ] **Step 4: Verify YAML still parses**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/deploy-production.yml'))" && echo OK
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/deploy-production.yml
git commit -m "ci: gate deploy workflow behind manual dispatch only"
```

---

### Task A5: Restrict `docker-build-push.yml` to manual dispatch only

**Files:**
- Modify: `.github/workflows/docker-build-push.yml`

This workflow currently fires on `release: published` and `workflow_dispatch`. Releases are infrequent so the auto-fire risk is low, but per the spec we want manual-only.

- [ ] **Step 1: Inspect current trigger block**

```bash
sed -n '1,15p' .github/workflows/docker-build-push.yml
```

Current:
```yaml
on:
  release:
    types: [published]
  workflow_dispatch:
    inputs:
      tag:
        description: 'Docker tag to build and push'
        ...
```

- [ ] **Step 2: Remove the `release` trigger**

Edit `.github/workflows/docker-build-push.yml`. Replace the `on:` block with `workflow_dispatch` only:

```yaml
on:
  workflow_dispatch:
    inputs:
      tag:
        description: 'Docker tag to build and push'
        required: true
        default: 'manual'
        type: string
```

- [ ] **Step 3: Verify YAML still parses**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/docker-build-push.yml'))" && echo OK
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/docker-build-push.yml
git commit -m "ci: restrict image build workflow to manual dispatch"
```

---

### Task A6: Create `OPERATIONS.md` runbook

**Files:**
- Create: `OPERATIONS.md`

- [ ] **Step 1: Write the runbook**

Create `/home/claude/culifeed/OPERATIONS.md`:

````markdown
# CuliFeed Operations Runbook

CuliFeed runs on this server via `docker compose`, with the source code bind-mounted from this repo. Updates are applied by `git pull && docker compose restart` — no image rebuilds unless `requirements.txt` changes.

## First-time start (after the cutover plan completes)

```bash
cd /home/claude/culifeed
docker compose up -d --build
```

## Daily operations

| Action | Command |
|---|---|
| Pull latest code, restart processes | `git pull && docker compose restart` |
| Update Python dependencies | `docker compose up -d --build` |
| Tail logs (both services) | `docker compose logs -f` |
| Tail bot only | `docker compose exec culifeed-prd tail -f /app/logs/bot.log` |
| Restart only the bot | `docker compose exec culifeed-prd supervisorctl restart culifeed-bot` |
| Restart only the daily scheduler | `docker compose exec culifeed-prd supervisorctl restart culifeed-daily` |
| Service status | `docker compose exec culifeed-prd supervisorctl status` |
| Stop everything | `docker compose down` |
| Open SQLite browser | `https://100.76.118.121:3001` (Tailscale) |

## Rollback paths

### R0 — v2 quality bad, runtime healthy

Disable the embedding pipeline without touching infra. Articles will route through v1 starting on the next pipeline run.

```bash
sed -i 's/USE_EMBEDDING_PIPELINE=true/USE_EMBEDDING_PIPELINE=false/' .env.prd
docker compose restart
```

### R1 — Container won't stay up

Revert to the last known-good GHCR image and the original data dir.

```bash
docker compose down
docker run -d --name culifeed-prd --restart unless-stopped \
  --env-file /home/claude/culifeed/.env.prd \
  -v /home/ubuntu/culifeed/data:/app/data \
  -v /home/ubuntu/culifeed/logs:/app/logs \
  ghcr.io/chiplonton/culifeed:1.4.2-alpine
```

### R2 — DB corruption, restore from backup

```bash
docker compose down
sudo cp /home/claude/culifeed/data/culifeed.db.backup-pre-v2-<timestamp> /home/claude/culifeed/data/culifeed.db
sudo chown claude:claude /home/claude/culifeed/data/culifeed.db
# then continue with R1 if you also need to revert the image
```

## Inspecting v2 audit data

```bash
sqlite3 data/culifeed.db <<SQL
SELECT pipeline_version, llm_decision, COUNT(*)
FROM processing_results
GROUP BY pipeline_version, llm_decision;
SQL
```

Diagnose a single article:

```bash
docker compose exec culifeed-prd python main.py diagnose --db /app/data/culifeed.db <article_id>
```
````

- [ ] **Step 2: Verify file rendered correctly**

```bash
head -30 OPERATIONS.md
```

Expected: top of the runbook visible, no template artifacts.

- [ ] **Step 3: Commit**

```bash
git add OPERATIONS.md
git commit -m "docs: add operations runbook for host docker-compose flow"
```

---

## Phase B — Pre-flight on isolated copy (no live impact)

These tasks operate on a copy of the production DB inside this repo's `data/` directory while the old container keeps serving the live DB at `/home/ubuntu/culifeed/data/`.

### Task B1: Backup the live production DB

**Files:**
- Create: `data/culifeed.db.backup-pre-v2-<timestamp>` (under repo data/, gitignored)

- [ ] **Step 1: Confirm prod DB exists and is readable**

```bash
sudo ls -la /home/ubuntu/culifeed/data/culifeed.db
```

Expected: file present, owned by `ubuntu` or `culifeed`.

- [ ] **Step 2: Create immutable backup**

```bash
mkdir -p /home/claude/culifeed/data
TS=$(date +%s)
sudo cp /home/ubuntu/culifeed/data/culifeed.db /home/claude/culifeed/data/culifeed.db.backup-pre-v2-${TS}
sudo chown claude:claude /home/claude/culifeed/data/culifeed.db.backup-pre-v2-${TS}
chmod 0444 /home/claude/culifeed/data/culifeed.db.backup-pre-v2-${TS}
echo "Backup at /home/claude/culifeed/data/culifeed.db.backup-pre-v2-${TS}"
```

The backup is read-only (`0444`) so we can't accidentally corrupt it.

- [ ] **Step 3: Record backup path for later rollback**

Save the timestamped backup path to a known location:

```bash
ls /home/claude/culifeed/data/culifeed.db.backup-pre-v2-* > /tmp/v2_cutover_backup_path.txt
cat /tmp/v2_cutover_backup_path.txt
```

This path is what R2 (the rollback path) restores from. Record it in your notes; it's not committed.

(No commit — backup is an artifact, not a code change.)

---

### Task B2: Capture production environment variables

**Files:**
- Create: `/home/claude/culifeed/.env.prd` (gitignored)

- [ ] **Step 1: Verify gitignore is in effect**

```bash
git check-ignore -v .env.prd 2>&1 || echo "NOT IGNORED — abort and fix .gitignore in Task A2"
```

Expected: `.gitignore:N:.env<pattern> .env.prd` line shown OR the not-ignored warning. If not ignored, STOP and revisit Task A2.

- [ ] **Step 2: Extract env from running container**

```bash
docker exec culifeed-prd env | grep ^CULIFEED_ > /home/claude/culifeed/.env.prd
chmod 600 /home/claude/culifeed/.env.prd
wc -l /home/claude/culifeed/.env.prd
```

Expected: 9 lines (matches what was previously captured to `/tmp/culifeed_prd.env`).

- [ ] **Step 3: Verify expected keys are present**

```bash
grep -cE "^CULIFEED_TELEGRAM__BOT_TOKEN=|^CULIFEED_AI__OPENAI_API_KEY=|^CULIFEED_AI__GROQ_API_KEY=|^CULIFEED_AI__GEMINI_API_KEY=|^CULIFEED_AI__DEEPSEEK_API_KEY=" /home/claude/culifeed/.env.prd
```

Expected: `5` (all five required keys present).

- [ ] **Step 4: Sanity-check no key values printed**

```bash
sed 's/=.*/=***/' /home/claude/culifeed/.env.prd
```

Expected: list of variable names with values redacted. The redacted view is safe to log; the file itself is `chmod 600`.

(No commit — `.env.prd` is gitignored.)

---

### Task B3: Copy live DB into repo data dir

**Files:**
- Create: `data/culifeed.db` (gitignored)

- [ ] **Step 1: Checkpoint the live container's WAL**

```bash
docker exec culifeed-prd sqlite3 /app/data/culifeed.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

Expected output: `0|0|0` (no failures, all WAL pages flushed).

- [ ] **Step 2: Copy DB file with ownership fix**

```bash
sudo cp /home/ubuntu/culifeed/data/culifeed.db /home/claude/culifeed/data/culifeed.db
sudo chown claude:claude /home/claude/culifeed/data/culifeed.db
ls -la /home/claude/culifeed/data/culifeed.db
```

Expected: file owned by `claude:claude`, size matches the source (~1.4 MB).

- [ ] **Step 3: Verify DB opens cleanly**

```bash
sqlite3 /home/claude/culifeed/data/culifeed.db "SELECT COUNT(*) AS articles FROM articles; SELECT COUNT(*) FROM topics WHERE active=1; SELECT COUNT(*) FROM processing_results;"
```

Expected: row counts roughly match today's state (~738 articles, 13 active topics, ~91 processing_results).

(No commit — `data/` is gitignored.)

---

### Task B4: Apply schema migration on the copy

**Files:**
- Modify (in-place): `data/culifeed.db`

- [ ] **Step 1: Activate venv**

```bash
cd /home/claude/culifeed
source venv/bin/activate
```

If venv is missing: `python3 -m venv venv && source venv/bin/activate && pip install -r requirements-dev.txt`.

- [ ] **Step 2: Run schema migration**

```bash
python -c "from culifeed.database.schema import DatabaseSchema; DatabaseSchema('data/culifeed.db').create_tables(); print('schema migrated')"
```

Expected output: `schema migrated`. Idempotent — adds v2 columns and `topic_embeddings` / `article_embeddings` virtual tables. No error if already applied.

- [ ] **Step 3: Verify v2 columns and tables exist**

```bash
sqlite3 data/culifeed.db ".schema processing_results" | grep -E "pipeline_version|embedding_score|pre_filter_score|llm_decision"
sqlite3 data/culifeed.db ".schema topics" | grep -E "description|embedding_signature"
sqlite3 data/culifeed.db "SELECT name FROM sqlite_master WHERE name IN ('topic_embeddings','article_embeddings')"
```

Expected:
- 4 lines from `processing_results` schema mentioning the v2 columns
- 2 lines from `topics` mentioning the new columns
- 2 rows naming the embedding tables

- [ ] **Step 4: Verify row counts unchanged**

```bash
sqlite3 data/culifeed.db "SELECT COUNT(*) FROM articles; SELECT COUNT(*) FROM channels; SELECT COUNT(*) FROM topics WHERE active=1;"
```

Expected: matches the post-copy counts from B3 step 3 — no data loss.

(No commit — only the gitignored DB changed.)

---

### Task B5: Backfill topic descriptions (one-time, real APIs)

**Files:**
- Modify (in-place): `data/culifeed.db`

This step calls real AI providers. Cost: under $0.01.

- [ ] **Step 1: Source production env**

```bash
set -a && source /home/claude/culifeed/.env.prd && set +a
```

- [ ] **Step 2: Run the backfill script**

```bash
cd /home/claude/culifeed
source venv/bin/activate
python scripts/backfill_topic_descriptions.py --db data/culifeed.db
```

Expected output: prints `Found N topic(s) without descriptions`, then one line per topic with the generated description (truncated to 80 chars). Should complete in 10–30 seconds.

- [ ] **Step 3: Verify descriptions written**

```bash
sqlite3 data/culifeed.db "SELECT COUNT(*) FROM topics WHERE active=1 AND (description IS NULL OR description = '')"
```

Expected: `0` (every active topic now has a description).

- [ ] **Step 4: Spot-check description quality**

```bash
sqlite3 data/culifeed.db "SELECT name, substr(description, 1, 80) FROM topics WHERE active=1 LIMIT 3"
```

Expected: each topic has a coherent, on-topic description (e.g., "AWS Lambda news ..." for a topic about Lambda).

- [ ] **Step 5: Verify idempotency**

```bash
python scripts/backfill_topic_descriptions.py --db data/culifeed.db
```

Expected output: `Found 0 topic(s) without descriptions`. No new API calls made.

(No commit — DB is gitignored.)

---

### Task B6: Backfill v2 historical processing rows (real APIs)

**Files:**
- Modify (in-place): `data/culifeed.db`

This step calls real AI providers and is the biggest cost step. Expected cost: under $0.05. Expected duration: 1–3 minutes for ~700 articles.

- [ ] **Step 1: Source production env (same as B5 if same shell session)**

```bash
set -a && source /home/claude/culifeed/.env.prd && set +a
```

- [ ] **Step 2: Run the backfill**

```bash
cd /home/claude/culifeed
source venv/bin/activate
time python scripts/backfill_v2_processing.py --db data/culifeed.db 2>&1 | tee /tmp/v2_backfill.log
```

Expected output: per-channel progress lines, finishes with no exceptions. Wall-clock 1–3 minutes.

If the script appears to be making thousands of API calls or running >10 minutes, `Ctrl+C` and investigate. Pre-filter survivors should be 70–85% of articles per the snapshot test.

- [ ] **Step 3: Verify v2 rows written with delivered=1**

```bash
sqlite3 data/culifeed.db "SELECT COUNT(*), MIN(delivered), MAX(delivered), COUNT(DISTINCT pipeline_version) FROM processing_results WHERE pipeline_version='v2'"
```

Expected: at least 500 rows; min and max of `delivered` both equal `1`; one distinct `pipeline_version` value.

- [ ] **Step 4: Verify decision breakdown is sensible**

```bash
sqlite3 data/culifeed.db "SELECT llm_decision, COUNT(*) FROM processing_results WHERE pipeline_version='v2' GROUP BY llm_decision"
```

Expected: a mix of `pass`, `fail`, and `skipped`. Most should be `skipped` (low embedding scores), some `pass`, some `fail`. Numbers near today's verification (43 pass / 23 fail / 512 skipped) but exact counts will vary.

- [ ] **Step 5: Verify no NULL audit columns**

```bash
sqlite3 data/culifeed.db "SELECT COUNT(*) FROM processing_results WHERE pipeline_version='v2' AND (pre_filter_score IS NULL OR embedding_score IS NULL OR llm_decision IS NULL)"
```

Expected: `0`.

- [ ] **Step 6: Verify idempotent re-run**

```bash
python scripts/backfill_v2_processing.py --db data/culifeed.db 2>&1 | tail -5
```

Expected output: each channel reports zero new articles to backfill (script does nothing). No new API calls.

(No commit — DB is gitignored.)

---

### Task B7: Pre-flight build and smoke test

**Files:** none

Build the cutover image and smoke-test it before stopping the live container.

- [ ] **Step 1: Build the new image**

```bash
cd /home/claude/culifeed
docker compose build culifeed-prd
```

Expected: build completes without errors. Should reuse cached layers from Task A1's verification builds.

- [ ] **Step 2: Verify the image has the v2 deps**

```bash
docker run --rm culifeed:local python -c "import sqlite_vec, openai, culifeed; print(f'sqlite_vec={sqlite_vec.__version__} openai={openai.__version__}')"
```

Expected: prints the two version strings, no traceback.

- [ ] **Step 3: Verify entrypoint works in dry mode**

```bash
docker run --rm \
  -v /home/claude/culifeed:/app \
  -v /home/claude/culifeed/data:/app/data \
  --env-file /home/claude/culifeed/.env.prd \
  -e CULIFEED_FILTERING__USE_EMBEDDING_PIPELINE=true \
  --entrypoint python \
  culifeed:local \
  -c "from culifeed.database.schema import DatabaseSchema; from culifeed.config.settings import get_settings; s=get_settings(); DatabaseSchema(s.database.path).create_tables(); print('verify_schema:', DatabaseSchema(s.database.path).verify_schema())"
```

Expected output: `verify_schema: True`.

This dry-run uses the same volumes and env the real container will use, but runs only the migration check — no bot starts, no scheduler. If it fails, fix before continuing to Phase C.

- [ ] **Step 4: Verify file ownership inside the container matches host**

```bash
docker run --rm -v /home/claude/culifeed/data:/app/data culifeed:local stat -c "%u:%g %n" /app/data/culifeed.db
```

Expected: `1001:1001 /app/data/culifeed.db`. If different, the chown in Task B3 was missed — go back and re-chown.

(No commit — image is local.)

---

## Phase C — Cutover (downtime starts here)

Total expected downtime from C1 to C3 finishing: 30–60 seconds.

### Task C1: Stop and remove the old container

**Files:** none

- [ ] **Step 1: Note current container state**

```bash
docker ps --filter name=culifeed-prd --format "{{.Status}}\t{{.Image}}"
```

Expected: `Up X hours\tghcr.io/chiplonton/culifeed:1.4.2-alpine`. Record the image name in case of R1 rollback.

- [ ] **Step 2: Stop the container**

```bash
docker stop culifeed-prd
```

Expected: prints `culifeed-prd` and exits 0. Bot offline starting now.

- [ ] **Step 3: Remove the container**

```bash
docker rm culifeed-prd
```

Expected: prints `culifeed-prd`. Necessary because compose will re-create with the same name.

- [ ] **Step 4: Verify gone**

```bash
docker ps -a --filter name=culifeed-prd --format "{{.Names}}\t{{.Status}}"
```

Expected: empty output.

(No commit.)

---

### Task C2: Bring up the new compose stack

**Files:** none (uses the committed compose file)

- [ ] **Step 1: Start the stack**

```bash
cd /home/claude/culifeed
docker compose up -d
```

Expected output: lines like `Container culifeed-prd Started` and `Container sqlitebrowser Started`.

If `--build` is needed (e.g., the image was deleted), use `docker compose up -d --build` instead.

- [ ] **Step 2: Wait for healthcheck to pass**

```bash
for i in 1 2 3 4 5 6; do
  status=$(docker inspect --format '{{.State.Health.Status}}' culifeed-prd 2>/dev/null)
  echo "attempt $i: $status"
  [ "$status" = "healthy" ] && break
  sleep 10
done
```

Expected: status reaches `healthy` within 60s. If it stays `unhealthy` after 6 attempts, jump to Task C3 step 1's failure path.

- [ ] **Step 3: Verify both supervisord processes running**

```bash
docker compose exec culifeed-prd supervisorctl status
```

Expected: two lines, both `RUNNING`:
```
culifeed-bot                     RUNNING   pid X, uptime ...
culifeed-daily                   RUNNING   pid Y, uptime ...
```

(No commit.)

---

### Task C3: Post-cutover verification gates

**Files:** none

Block on each gate. If any fails, follow the rollback action.

- [ ] **Step 1: No fresh errors in logs**

```bash
docker compose logs --tail=200 culifeed-prd | grep -iE "error|traceback|exception" | tail -20
```

Expected: only old/expected log lines (e.g., HTTP 4xx warnings from RSS feeds). Any new `Traceback` or `ERROR` from startup → rollback **R1** (see OPERATIONS.md).

- [ ] **Step 2: Bot responds in Telegram**

In your Telegram client, send `/help` to the bot.

Expected: bot replies within 2–3 seconds with the help text.

If no reply after 30s → check `docker compose exec culifeed-prd supervisorctl tail -f culifeed-bot` for errors. If unrecoverable → **R1**.

- [ ] **Step 3: v2 backfill rows present**

```bash
sqlite3 /home/claude/culifeed/data/culifeed.db "SELECT COUNT(*) FROM processing_results WHERE pipeline_version='v2'"
```

Expected: at least `500`. If `0` → **R2** (the live DB doesn't include the backfill — Phase B was applied to the wrong file).

- [ ] **Step 4: Daily scheduler ready**

```bash
docker compose exec culifeed-prd supervisorctl tail culifeed-daily | tail -20
```

Expected: lines indicating the scheduler service started and is waiting for the next scheduled tick. No tracebacks.

- [ ] **Step 5: sqlitebrowser reachable**

```bash
curl -s -o /dev/null -w "%{http_code}" -k https://100.76.118.121:3001 || echo "non-200"
```

Expected: `200` or `302` (login page). A connection-refused suggests the sidecar didn't start — non-critical, can fix later.

(No commit. Cutover complete if all gates pass.)

---

## Phase D — Post-cutover

### Task D1: First operational restart drill

**Files:** none

Verify the iteration loop works as documented before declaring success.

- [ ] **Step 1: Make a no-op change**

Edit `OPERATIONS.md` and add a single trailing newline (or any whitespace-only change), then save.

- [ ] **Step 2: Restart the stack via the documented command**

```bash
cd /home/claude/culifeed
docker compose restart
```

Expected: containers restart in <10 seconds.

- [ ] **Step 3: Verify processes back up**

```bash
docker compose exec culifeed-prd supervisorctl status
```

Expected: both processes `RUNNING` again, with fresh uptimes.

- [ ] **Step 4: Bot responds again in Telegram**

Send `/help` to the bot.

Expected: reply within a few seconds.

- [ ] **Step 5: Discard the no-op edit**

```bash
git checkout -- OPERATIONS.md
```

(No commit.)

---

### Task D2: Push the branch to origin

**Files:** none

After all the previous tasks committed cleanly to the branch.

- [ ] **Step 1: Inspect commit list**

```bash
git log --oneline main..HEAD | head -40
```

Expected: ~30+ commits including `feat(...)`, `fix(...)`, `build(docker): accept UID/GID build args`, `build(compose): add host docker-compose config`, `ci: gate deploy workflow`, `ci: restrict image build workflow`, `docs: add operations runbook`.

Verify NONE of them carry `Co-Authored-By: Claude` or other AI attribution:

```bash
git log main..HEAD | grep -iE "co-authored-by|claude|generated with" || echo "OK no AI attribution"
```

Expected: `OK no AI attribution`. If any commit has attribution → STOP and amend before pushing.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin feat/topic-matching-v2
```

Expected: branch published to origin. No CI workflow auto-fires (because Task A4 + A5 disabled the auto-triggers).

- [ ] **Step 3: Verify GH Actions did not fire**

Open the repo's Actions tab in a browser. Within 1 minute, confirm:
- No new `Deploy to Production` workflow run from this push
- No new `Build and Push Docker Image` workflow run from this push

Expected: only manual past runs visible. No fresh entries.

(No commit.)

---

### Task D3: Tag the cutover for future reference

**Files:** none

- [ ] **Step 1: Tag the cutover commit**

```bash
TS=$(date +%Y%m%d-%H%M)
git tag -a "v2-cutover-${TS}" -m "v2 embedding pipeline cutover to host docker-compose"
git push origin "v2-cutover-${TS}"
```

Expected: tag created and pushed.

This gives a clean reference point for "what was running at cutover" — useful for diffing if v2 issues surface later.

(No commit beyond the tag.)

---

## Done criteria

- [ ] All Phase A, B, C, D tasks marked complete
- [ ] `docker compose ps` shows `culifeed-prd` and `sqlitebrowser` both healthy
- [ ] Bot answers `/help` in Telegram
- [ ] Tomorrow's daily scheduler tick produces v2 rows with `pipeline_version='v2'` and delivers PASS articles to the channel
- [ ] No `Co-Authored-By` lines in any commit on this branch
- [ ] Branch pushed; GH Actions did not auto-fire

If everything above is true: cutover is complete. Continue iteration via the documented `git pull && docker compose restart` loop. Run shadow validation for ~7 days before deciding whether to merge `feat/topic-matching-v2` to `main`.

---

## What to do if something goes badly wrong mid-plan

| Where | Symptom | Action |
|---|---|---|
| Phase A (any task) | Tests fail / commit can't be made cleanly | Fix or skip the task; old container is still serving, no urgency |
| Phase B (B5/B6) | API costs surprise high | Ctrl+C, examine `/tmp/v2_backfill.log`, do not proceed to Phase C |
| Phase B (any) | DB corruption signal | Stop. The live DB is untouched; just delete the working copy and re-do B3 |
| Phase C step 1 | `docker stop` hangs >30s | `docker kill culifeed-prd` is acceptable; old container had supervisord doing graceful shutdown |
| Phase C step 2 | New stack won't come up | **R1** rollback: `docker compose down`, then `docker run` the old GHCR image with old volumes (see OPERATIONS.md) |
| Phase C step 3 | Bot doesn't respond | Check logs; if Telegram conflict ("terminated by other getUpdates"), confirm old container fully removed (`docker ps -a`); else **R1** |
| Phase D | Post-cutover quality bad | **R0** flag flip, observe for an hour, then decide |
