# Concierge package known limitations — current beta closure

- This is a rough private `0.1.16-dev` beta prerelease, not a finished production release. The repository is not public yet, so anonymous direct installation is not claimed.
- ADR 0012 ratifies the bounded fully automatic beta: independent finite backlog capture, recent ended-session capture, and automatic promotion jobs. Capture remains exact-title/alias plus explicit-cue and proposal-first; promotion uses the documented `0.85` threshold; automatic querying is bounded Concierge read-routing, not an active-session crawler.
- The beta gate separately verifies the current snapshot; historical P6.5, P7, P8, and provider receipts are not current proof and are not folded into this verdict.
- The exact versioned private repository ref exists, but `PUBLIC_PACKAGE_URL_UNRESOLVED` remains a publication/installability limitation until the repository is public. A local HTTP server is not an honest substitute for a public raw URL because Hermes' direct URL installer rejects loopback/private targets through its SSRF guard.
- P6.2 read-only preflight and P6.3 package-file install/upgrade/uninstall/recovery remain implemented and tested against explicit temporary paths; MCP, cron, consent, and package runtime remain separate lifecycle boundaries.
- The package CLI's MCP command points at a package-owned runtime checkout; the direct skill bundle alone is not the Python application runtime.
- Linux should work in theory, but Linux is not currently tested against this candidate. Windows is the tested path. There is no native Linux or Ubuntu WSL2 receipt here, and no claim of provider-backed fresh-agent chat or ordinary private-use acceptance.
- No active-session observer is included. Ended-session observation capture is bounded, proposal-first, and never a canonical write; automatic promotion is the separately enabled mutation path.
- The current beta evidence is a new exact snapshot/archive receipt. Historical packets remain context only and are not substitutes for that receipt.
- The compatibility and fixture documents are package-facing snapshots; the repository's application contracts and tests remain authoritative.
