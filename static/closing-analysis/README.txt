Brazil 2026 presidential runoff: domestic closing audit
====================================================

Scope: all 497,897 domestic polling sections. Overseas voting is excluded.
The unit is a polling section, not a school/building or a unique physical machine.
All times below are voting-machine times normalized to Brasilia (UTC-3).
"Before" is strictly less than the exact second; "at/after" includes that second.

The official BUs for all domestic sections reconcile exactly:
127,854,631 ballots = 64,056,078 Lula + 59,654,544 Flavio Bolsonaro
                   + 1,425,821 blank + 2,718,188 null.
Lula's share of domestic valid votes: 51.778963652773484%.

Closing comparisons use the official BU dataHoraEncerramento field.
497,825 sections have classifiable closing clocks. We set aside 70 sections
whose logs have clock discontinuities and two sections with no published logs.
Their ballots remain in the full domestic accounting above. Four logs lack the
closing confirmation message, but their official BUs supply closing timestamps.

Before 17:04:00:
411,126 sections recorded a closing time.
256,355 sections had already issued their results bulletin (dataHoraEmissao).
Issuing the bulletin is a stronger indicator of completed local tallying than
the earlier closing-confirmation step. A section can be doing closing paperwork
after it has stopped admitting voters.

At/after 17:04:00:
86,699 sections recorded closing times, across 4,584 municipalities, all 27 UFs.
Whole-day valid votes in these sections: Lula 12,457,686 (55.51413453653531%);
Flavio 9,982,880 (44.48586546346469%).
Lula won 52,288 sections, Flavio 34,029; 382 tied.
The sections closing earlier gave Lula 50.95371482545268% of valid votes.

Before 17:10:00: 484,444 sections recorded closing times.

At/after 19:00:00:
6,352 sections, 147 municipalities, 19 UFs.
Whole-day valid votes: Lula 989,819 (55.17650232954311%);
Flavio 804,095 (44.82349767045689%).
Lula won 3,672 sections, Flavio 2,662; 18 tied.
The full set of municipalities and their section counts is in summary.json.

Separately, trusted ballot-event timestamps identify 34,109 post-17:00 ballots
in 29,668 sections across 3,340 municipalities and all 27 UFs. Of these ballots,
34,098 were before 19:00 and 11 at/after 19:00. Another 32,082 domestic ballots
have no usable cast-time classification. The late-ballot sections' whole-day
valid vote was 54.006624189297746% Lula and 45.993375810702254% Flavio.
These are whole-section candidate results, NOT candidates' post-17:00 totals.
Public logs do not disclose a candidate choice for each timed ballot.

What this does and does not establish
------------------------------------
Later-closing sections were more favorable to Lula on average and included
thousands of Flavio-winning sections. Timing records cannot establish whether
officials anticipated a court order, why they waited, when they learned of it,
whether a voter joined a queue before 17:00, or which candidate a late voter chose.
Voters already queued at 17:00 were ordinarily entitled to finish voting.
These are descriptive comparisons; they are not estimates of the extension's
causal effect or an assessment of voters who did not cast a ballot.

Files
-----
summary.json: definitions, coverage, national reconciliation, cohort totals,
and the 147 municipalities with BU closing times at/after 19:00.
domestic-section-closings.csv.gz: one row for every domestic section, including
both closing timestamps, bulletin issuance time, whole-day candidate/category
totals, observed post-17 count if usable, original TSE BU URL and its SHA-256.
post-17-sections.csv.gz: the 29,668 sections with observed post-17 ballots.
closed-at-or-after-19-sections.csv.gz: the 6,352 sections identified above.
manifest.json: byte sizes and SHA-256 hashes of the data files.

CSV times are ISO dates in Brasilia, without a zone suffix. The
usable_log_closing column refers to whether a final log confirmation was found
and its clock was usable; it is NOT the bulletin clock inclusion flag.
For BU cutoff comparisons, exclude log_timing_status values
machine_clock_discontinuity and no_published_log. Include the other rows.
For ballot-event counts, an empty post_17_ballots field means unclassified,
not zero. Do not fill those gaps with invented timestamps.

Recomputing the closing and candidate aggregations from the CSV
-------------------------------------------------------------
Download domestic-section-closings.csv.gz. Run with Python 3, one thread:

import csv, gzip, collections
groups = collections.defaultdict(collections.Counter)
with gzip.open('domestic-section-closings.csv.gz', 'rt') as f:
    for row in csv.DictReader(f):
        if row['log_timing_status'] in (
            'machine_clock_discontinuity', 'no_published_log'
        ):
            continue
        close = row['bu_recorded_closing_brt']
        labels = ['before_1704' if close < '2026-10-25T17:04:00'
                  else 'at_or_after_1704']
        if close >= '2026-10-25T19:00:00':
            labels.append('at_or_after_19')
        if row['bu_emitted_brt'] < '2026-10-25T17:04:00':
            labels.append('bulletin_already_issued_before_1704')
        lula = int(row['lula_whole_day'])
        flavio = int(row['flavio_whole_day'])
        for label in labels:
            groups[label].update(sections=1, lula=lula, flavio=flavio,
                                 lula_won=int(lula > flavio),
                                 flavio_won=int(flavio > lula),
                                 ties=int(lula == flavio))
for label, result in groups.items():
    valid = result['lula'] + result['flavio']
    print(label, dict(result), 'Lula %:', 100 * result['lula'] / valid)

Checking the original source independently
-----------------------------------------
The CSV is a derived dataset. For an independent source check, download each
bu_url and decode it with the TSE's official ASN.1 specification / bu_dump.py.
Verify election 6258 and the section's municipality, zone and section identity.
Compare the presidential candidate/category totals and the dataHoraEncerramento
and dataHoraEmissao fields. The CSV SHA-256 checks byte identity with our saved
copy; it is not an independent verification of TSE's digital signatures.
The clock exclusions come from the log analysis, whose source and one-connection
reproducer are linked below. Clocks are not certified against an external clock.

Official result (subtract overseas ZZ to obtain domestic figures):
https://resultados.tse.jus.br/oficial/ele2026/6258/dados/br/br-c0001-e006258-u.json
https://resultados.tse.jus.br/oficial/ele2026/6258/dados/zz/zz-c0001-e006258-u.json

Official machine-data portal:
https://resultados.tse.jus.br/oficial/app/index.html#/eleicao/6258/uf/br/cargo/1/vis/nominal/dados-de-urna/log-da-urna

Official BU / RDV / signature format and decoder examples:
https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/formato-arquivos-de-bu-rdv-e-assinatura-digital/@@download/file

Official section-auxiliary URL template (values zero-padded):
https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3221/dados/{uf}/{municipality}/{zone}/{section}/p003221-{uf}-m{municipality}-z{zone}-s{section}-aux.json
The selected publication's hashes/arq entries identify the original BU and log.

TSE's pre-election explanation of voting after 17:00 for existing queues:
https://www.tse.jus.br/comunicacao/noticias/2026/Outubro/o-que-acontece-nas-secoes-eleitorais-apos-as-17h-entenda-as-regras-de-seguranca-e-fiscalizacao

TSE extension clarification:
https://www.tse.jus.br/comunicacao/noticias/2026/Outubro/esclarecimento-sobre-prorrogacao-da-votacao

Dashboard and ballot-event reproduction script:
https://brazil-2026-election.vercel.app/
https://brazil-2026-election.vercel.app/reproduce.py
