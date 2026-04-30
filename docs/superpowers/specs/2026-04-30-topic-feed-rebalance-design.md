# Topic & Feed Rebalance for "Chip" channel — 2026-04-30

## Goal

Reshape the topic and feed configuration for `chat_id='1535729092'` (channel "Chip") to:

1. Remove dead-weight feeds and underperforming/redundant topics surfaced by 30-day delivery analysis.
2. Close coverage gaps for explicitly stated domains: **AI applying / production LLM best practices**, **third-party / supply-chain security**, **Kubernetes & container security**.
3. Deepen AWS coverage and add multi-cloud AI service coverage (AWS / GCP / Azure).
4. Strengthen FinOps coverage with vendor-neutral and competitor sources.

This is a data update, not a code change. No application code is touched.

## Findings (30-day window)

- **Healthy topics**: `all update relate to google and anthropic and openai` (48 deliveries), `exploit development` (16), `AI engineering` (14).
- **Dead topic**: `engineering culture or personal growth…` — 0 deliveries.
- **Underperforming**: `IaC and CaC` (1), `cloudflare …` (1).
- **Sprawl**: 4 overlapping Linux topics with redundant keywords.
- **Dead-weight feeds (0 delivered)**: `itsfoss.com`, `news.itsfoss.com`, `isc.sans.edu` (podcast-only, no body), `sysdig`, `tecmint`, `slashdot/Linux`, `ostechnix`, plus `wired.com` (111 articles → 2 delivered = 1.8% yield).
- **509 `__no_match__` articles in 30 days** — significant unmatched signal currently includes supply-chain attacks, k8s security, applied LLM patterns.

## Changes

### Topics (13 → 15)

**Merge** (2 → 1):
- Drop: `linux performance monitoring container automation troubleshooting`, `linux ssh firewall kernel backup networking storage`.
- Insert: `linux sysadmin performance and networking` with union of relevant keywords; threshold 0.6.

**Update in place** (4):
- `AI engineering …` → broaden keywords to RAG, agents, MCP, evals, LLMOps, vector DB, embeddings, fine-tuning, inference, prompt engineering. This becomes the "AI applying / best practices" topic.
- `cloudflare …` → rename to `edge and CDN security and performance`; broaden keywords to include Fastly, edge compute, DDoS, DNS security.
- `engineering culture …` → refocus to `tech leadership and staff engineering`; keywords around staff/principal eng, career growth, technical strategy.
- `finops with aws cost optimize best practice` → broaden keywords to include GCP/Azure cost terms, unit economics, reserved instances, savings plans, spot.

**Insert new** (3):
- `supply chain and third party security` — npm/pypi attacks, dependency confusion, typosquatting, github actions, SBOM, sigstore, cosign, vendor breach.
- `kubernetes and container security` — pod security, admission controller, image scanning, runtime security, eBPF, falco, OPA, gatekeeper.
- `cloud AI services and applied ML` — Bedrock, SageMaker, Vertex AI, Azure OpenAI / Azure AI, Amazon Q, managed ML, inference endpoints.

All new topics: `confidence_threshold=0.6`, `active=1`, `exclude_keywords=[]`.

### Feeds (33 → 46)

**Remove (8, all 0 deliveries / 30d or extreme noise):**

```
https://itsfoss.com/rss
https://news.itsfoss.com/feed/
https://isc.sans.edu/rssfeed.xml
https://sysdig.com/feed
https://www.tecmint.com/feed/
https://rss.slashdot.org/Slashdot/slashdotLinux
https://ostechnix.com/feed/
https://www.wired.com/feed/rss
```

**Add (21, all verified `200 OK` valid RSS/Atom):**

Supply-chain & 3rd-party security (3):
```
https://github.blog/security/feed/
https://snyk.io/blog/feed/
https://www.aikido.dev/blog/rss.xml
```

LLM applying / best practices (4):
```
https://simonwillison.net/atom/everything/
https://www.latent.space/feed
https://openai.com/blog/rss.xml
https://lilianweng.github.io/index.xml
```

Anthropic coverage (no official RSS exists; multi-source bridges) (2):
```
https://news.google.com/rss/search?q=Anthropic+Claude&hl=en-US&gl=US&ceid=US:en
https://hnrss.org/newest?q=anthropic+OR+claude+code&count=30
```

Container / k8s security (1):
```
https://blog.aquasec.com/rss.xml
```

AWS deepening (4):
```
https://aws.amazon.com/blogs/machine-learning/feed/
https://aws.amazon.com/blogs/devops/feed/
https://aws.amazon.com/blogs/containers/feed/
https://aws.amazon.com/blogs/aws-cloud-financial-management/feed/
```

Google Cloud (2):
```
https://cloudblog.withgoogle.com/products/ai-machine-learning/rss/
https://cloudblog.withgoogle.com/rss/
```

Microsoft Azure (1):
```
https://azure.microsoft.com/en-us/blog/feed/
```

FinOps independent (4):
```
https://www.finops.org/feed/
https://www.vantage.sh/blog/rss.xml
https://www.duckbillgroup.com/feed/
https://www.cloudzero.com/blog/rss/
```

## Risks & Mitigations

- **Google News RSS noise**: every regional outlet rewrites Anthropic press releases. Mitigation: existing `content_hash` dedup collapses exact dupes; if the AI/Anthropic topic floods, raise threshold to 0.7 or add `exclude_keywords=["stock","valuation","lawsuit"]`.
- **Topic overlap** between `AI engineering` and `cloud AI services`: intentional. Different signal — patterns vs. vendor service news. Pipeline handles the same article matching multiple topics.
- **Orphaned `processing_results` rows** for deleted/renamed topic names: harmless; can be pruned later.

## Application

1. Back up the database: `cp data/culifeed.db data/culifeed.db.bak.2026-04-30`.
2. Apply all changes inside a single SQL transaction scoped to `chat_id='1535729092'`.
3. Verify: re-run topic + feed counts, confirm 14 topics and 46 feeds active.
4. Monitor first 7 days of processing for: floods on the broadened/new topics; new `__no_match__` rate.

## Out of Scope

- No code changes.
- No changes to other channels.
- No threshold tuning beyond initial 0.6 default for new topics; tune after observing real delivery data.
