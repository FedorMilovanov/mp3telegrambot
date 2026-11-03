# Ownership, authorship and third-party boundaries

**Maintainer and project engineering:** [Fedor Milovanov](https://github.com/FedorMilovanov). MP3Bot is a locally operated Telegram media-processing and AI-assisted editorial workflow; its implementation is maintained using human-directed programming, testing and AI tooling.

## Attribution and decision rights

- **Engineering and operations:** The repository owner maintains the application integration, local operational environment, source policies, regression checks and release decisions. Being the repository owner does **not** establish that every line was handwritten or that an external dependency, model or protocol was invented here.
- **Editorial outputs:** Sermon/video analysis, source cards, transcripts, translated audio and publication text are derived from underlying media and external AI services. The original speakers and rightsholders retain the rights they actually hold; an automated transcript, a Telegraph publication or a local archive entry is not a transfer of those rights. The operator is responsible for reviewing accuracy, quotations, licensing and permissions before distribution.
- **Third-party technology:** Telegram and Telegraph APIs; FFmpeg/FFprobe, yt-dlp, the Yandex LiveDub service, Gemini and other provider models are independently supplied technologies. Their respective API terms, model restrictions and upstream licenses remain applicable. Local TTS models and weights are not claimed as original models of this repository.
- **Project license:** [LICENSE](LICENSE) specifies MIT terms for the repository's software and documentation to the extent the maintainer has rights to license them. It does not extend MIT rights to third-party source videos, provider services, model weights, voices, user-uploaded media or external platform content.
- **Credentials and private state:** Telegram, Gemini, Yandex and other tokens, cookies, SQLite data and downloaded files are operational secrets/data, not portfolio artifacts; never commit or publish them. See [SECURITY.md](SECURITY.md).

## Engineering and verification authority

| Responsibility | Primary source |
|---|---|
| End-user modes and runtime boundaries | [README.md](README.md) / [README_EN.md](README_EN.md) |
| Bootstrap, local state, provider and layer architecture | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Non-negotiable editorial and quality policy | [AGENTS.md](AGENTS.md) |
| Private vulnerability reporting and credentials | [SECURITY.md](SECURITY.md) |
| Generated-doc and code verification | [tools/verify_repo.py](tools/verify_repo.py) |
| Provider/model and cost boundary | [docs/dependency-policy.md](docs/dependency-policy.md) |
| Portfolio-facing engineering evidence | [CASE_STUDY.md](CASE_STUDY.md) |

This document does **not** modify the app's authorization model, open any provider accounts, waive copyright restrictions, claim live availability or establish audience/revenue figures. Runtime actions remain subject to the existing operator and source-of-truth contracts.
