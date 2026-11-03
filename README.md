# SEO / GEO Engineering

SEO and generative-search optimization for AI agents, with protected business facts, technical audits, content rebuilding, crawler policies, and measurable experiments. Instructions are in Russian.

## Install

Install for Codex across projects:

```sh
npx skills add kvvzxd/seo-geo-engineering --skill seo-geo-engineering -g -a codex --copy -y
```

Choose agents interactively:

```sh
npx skills add kvvzxd/seo-geo-engineering
```

Preview available skills without installing:

```sh
npx skills add kvvzxd/seo-geo-engineering --list
```

Omit `-g` to install only in the current project. `--copy` keeps an independent copy instead of a symlink. On Windows, use `npx.cmd` if PowerShell blocks `npx.ps1`. Use a current Node.js version supported by [Skills CLI](https://github.com/vercel-labs/skills). Python 3.10+ is required for the included Python tools; no external Python libraries are required.

## Use

Ask your agent to use `$seo-geo-engineering` and provide the site or repository, markets, actual business facts, and permitted scope. Example:

> Используй $seo-geo-engineering: проведи аудит сайта, исправь разрешённые проблемы SEO/GEO, сохрани реальные услуги, цены и условия и проверь изменения.

The skill supports audit, implementation, content rebuilding, migrations, AI-search observability, and research. It may rewrite content and architecture while preserving the meaning of prices, units, qualifications, exclusions, geography, and commitments. Unknown or conflicting facts must not become invented claims.

## Included tools

`inspect`, `validate`, `plan`, `apply`, `verify`, `drift`, `rollback`, `facts`, `compare`, `analytics`, `geo`.

```sh
python skills/seo-geo-engineering/scripts/seo_geo.py --help
python skills/seo-geo-engineering/evals/test_tools.py -q
```

See [SKILL.md](skills/seo-geo-engineering/SKILL.md) and [tool contracts](skills/seo-geo-engineering/references/tool-contracts.md). Local file application is not a deployment. Network crawling, browser rendering, CMS/WAF changes and commercial search APIs require suitable project tools and authorization. Automated fact checks require semantic review. No ranking, indexing, citation, or revenue guarantees are made.

## Validation

62 automated tool tests. 38 documented agent evaluation scenarios; execution of all 38 is not claimed. A separate isolated operational run exercised 35 CLI calls and confirmed backups, idempotence, concurrency safeguards, and byte-exact rollback. No live SEO uplift experiment was performed.

## Knowledge maintenance

Research baseline: 2026-11-03. Dynamic search features, crawler behavior and report capabilities must be rechecked against primary sources before implementation. Research hypotheses are distinguished from documented mechanisms and observed effects.

## License

No open-source license has been granted. All rights reserved pending the owner's licensing decision.
