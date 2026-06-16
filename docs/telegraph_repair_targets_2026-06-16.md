# Telegraph repair targets — 2026-06-16

Technical pages from the live Shepherds' Conference run that DOM audit found worth repairing.

## Raw Markdown artifacts

- https://telegra.ph/Razbor-materiala-Vernost-v-sluzhenii--Dzhon-MakArtur-06-16
- https://telegra.ph/Razbor-materiala-Shepherds-Conference-2019--GS2--Vernost-v-Lichnoj-Svyatosti--Shepherds-Conference-2019-06-16
- https://telegra.ph/Razmyshlenie-i-primenenie-Vernost-v-lyubvi--Ostin-Dankan-06-16
- https://telegra.ph/Razmyshlenie-i-primenenie-Pastorskaya-konferenciya-2019--Dzhon-MakArtur-06-16

## Third-person wrappers

- https://telegra.ph/Razmyshlenie-i-primenenie-SHkola-vernosti-ili-kak-nauchitsya-vernosti--Aleksej-Kolomijcev-06-16
- https://telegra.ph/Razbor-materiala-Vernost-v-poklonenii--Ligon-Dankan-06-16

## Source-card original-title issues

- https://telegra.ph/Razbor-materiala-Shepherds-Conference-2019--GS13--QA--Shepherds-Conference-2019-06-16
- https://telegra.ph/Razbor-materiala-Faithfulness-on-the-Pulpit--Steven-Lawson-06-16

## Usage

Dry-run:

```bash
python tools/repair_telegraph_pages.py --url-file docs/telegraph_repair_targets_2026-06-16.md --no-history
```

Apply on the runtime machine with `TELEGRAPH_TOKEN`:

```bash
python tools/repair_telegraph_pages.py --url-file docs/telegraph_repair_targets_2026-06-16.md --apply
```
