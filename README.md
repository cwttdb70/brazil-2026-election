# Brazil 2026 Election Analysis

A single-page SvelteKit dashboard for Brazil's October 25, 2026 presidential runoff. English and Brazilian Portuguese, selected from the browser language with a persistent manual switch.

## What the numbers mean

The official national result is **128,236,074 ballots**: Lula 64,256,232; Flávio Bolsonaro 59,826,041; blank 1,430,496; null 2,723,305. It includes overseas voting. Domestic turnout is 127,854,631; overseas turnout is 381,443.

The candidate result is the official nationwide result. Geographic filters apply to the timing analysis below it. Each geographic timing total is anchored to the matching official turnout. Observed machine-log records supply the timed buckets; ballots without verified timing remain explicitly unclassified. Thus the time buckets and the unclassified bucket add to the official total without estimating a voting time.

Time buckets use Brasília time (UTC−3): before 17:00; 17:00 inclusive through 19:00 exclusive; at or after 19:00; unclassified. The source logs use local machine clocks. Acre and eleven western Amazonas municipalities require +2 hours; the remaining Amazonas municipalities, Rondônia, Roraima, Mato Grosso and Mato Grosso do Sul require +1; Fernando de Noronha requires −1. Overseas times are not guessed.

The timestamped events do **not** identify candidate choices. Candidate votes before/after 17:00 cannot be reconstructed from the randomized digital vote records. A vote after 17:00 also does not establish that it depended on the extension: voters already in line could vote after the original close.

## Reproduce independently with one thread and one connection

Download [`static/reproduce.py`](static/reproduce.py). Python 3.10+ and the standard library are sufficient. No account, API key, proxy or extra package is needed.

```bash
python3 reproduce.py --self-test
python3 reproduce.py --database smoke.sqlite --output smoke.json --limit 10
python3 reproduce.py --database brazil-2026.sqlite --output results.json
```

The smoke run is deliberately incomplete. Run the full command again to resume it. The full dataset requires at least 100 GB free for the relevant raw archives and can take a long time over one connection. The default downloads logs, result bulletins and supporting official JSON; `--all-files` adds RDV, signatures and bulletin images and needs more space. All raw files are stored as compressed blobs in a single SQLite database rather than hundreds of thousands of loose files. Source URLs, SHA-256 fingerprints and checkpoints are preserved.

The script recursively reads archived log segments, binds their section identity, removes exact duplicate rows, filters the second-round voting date, counts presidential confirmation/automatic-null events, checks session counts, compares official bulletins, and records uncertain timing rather than imputing it. ZIP/fingerprint checks are not a verification of the separate TSE digital-signature envelopes.

## Exact official inputs

- [Official nationwide result JSON, election 6258](https://resultados.tse.jus.br/oficial/ele2026/6258/dados/br/br-c0001-e006258-u.json)
- [Example second-round section index, polling event 3221](https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3221/config/df/df-p003221-cs.json)
- [Example section metadata](https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3221/dados/df/97012/0001/0001/p003221-df-m97012-z0001-s0001-aux.json)
- [Official log specification](https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/log-do-ecossistema)
- [Official state boundary source, IBGE](https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_municipais/municipio_2024/Brasil/BR_UF_2024.zip)

Enumerate each UF index's municipalities/zones/sections, skipping secondary sections with `nsp`. Request the section's `aux.json`; use the advertised hash and filename to fetch its files. The script contains the exact URL templates. Both primary counts reconcile: 499,248 totalized sections; 499,207 installed and 41 uninstalled overseas sections.

The original 72 entries without regular VOTA logs break down into 41 uninstalled overseas sections; 29 overseas SA tabulation logs (831 known ballots); and two domestic sections without a VOTA log (454 known ballots). SA tabulation timestamps are not cast times. See [`data/missing-logs.json`](data/missing-logs.json), [`data/untimed-sections.json`](data/untimed-sections.json) and the reconciliation asset for audit details.

## Run the website

```bash
npm ci
npm run dev
npm run check
npm run build
npm run preview
```

`tools/prepare_site.mjs` validates arithmetic and copies the pinned public aggregate assets from `data/` into `static/data/` before development/build. `tools/aggregate_logs.py` is the local incremental analysis implementation; raw local databases and credentials are not part of this repository or the public site. The independently runnable script is `static/reproduce.py`.

The static production output is `build/`. Vercel runs `npm ci` and `npm run build`. All public data assets contain aggregate counts or public polling-section identifiers, never raw voter identifiers or proxy credentials. A deployed snapshot does not imply the local background analysis has finished: see the timestamp, coverage and caveats on the page.
