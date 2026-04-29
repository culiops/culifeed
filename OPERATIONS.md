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
