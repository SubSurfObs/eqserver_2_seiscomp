# Cross-project handoffs

Asynchronous design conversations between this project and its peer projects,
conducted via committed markdown files (one Claude session writes, the peer's
Claude session reads, commits a reply, the cycle continues until convergence).

This is a **local convention** for the project group listed in
`../RELATED_PROJECTS.md`. It is NOT a Claude Code default — as of this writing
there is no established cross-session handoff pattern in the Claude Code or
MCP ecosystems. The convention here is bespoke and lightweight.

## Layout

```
handoffs/
└── <peer-project>/
    └── <YYYY-MM-DD>_<thread-slug>/
        ├── 01_<message-type>_from_<sender>.md
        ├── 02_<message-type>_from_<sender>.md
        └── ...
```

- **One subdirectory per peer project** — easy to scope "everything I've ever
  exchanged with X."
- **One subdirectory per thread** (conversation), keyed by start date and a
  short topic slug — multiple unrelated design threads can run concurrently
  without conflating.
- **Messages numbered sequentially** with the sender on the right of the
  filename so the conversation reads top-to-bottom in `ls` output.
- **The full thread is mirrored in each participating repo's `handoffs/`
  directory** (sender's outgoing AND incoming messages, both). Costs a few
  KB of duplication per repo; buys discoverability: any session reading
  any repo sees the complete exchange without cross-repo grep.

## Naming

- Thread dir: `<YYYY-MM-DD>_<topic-slug>`
  - Date is the thread's **start** (not last activity).
  - Slug is kebab-case, short, descriptive of the design topic.
  - Example: `2026-05-30_ledger-integration`.
- Message file: `<NN>_<message-type>_from_<sender>.md`
  - `<NN>` is the sequential index (`01`, `02`, ...) for chronological order.
  - `<message-type>` is a short tag: `proposal`, `reply`, `ready_for_review`,
    `decision`, `closing`. Use whichever conveys the message's role best.
  - `<sender>` is the project name (or session label if multiple agents
    operate in the same repo).

## When to start a new thread

- A new design topic surfaces that wouldn't fit cleanly under an existing one.
- An existing thread has closed (PR merged, decision made, work shipped) and
  the next round of conversation is about something different.
- A thread has grown too long (>10 messages without convergence) — split it
  into focused sub-topics.

## When to extend an existing thread

- Iterating on the same proposal / schema / API across rounds.
- Negotiating the same design decision.
- Responding to a round-N message with round-N+1.

## Closing a thread

When a thread reaches a terminal state (decision made, PR merged, schema
locked and implemented), add a final `NN_closing_from_<sender>.md` message
that summarises the outcome and links to the relevant commits / PRs. Then
leave the directory in place — future sessions may want the historical
record.

## Active threads (this repo: `eqserver_2_seiscomp`)

| Peer | Thread | Status |
|---|---|---|
| `disk_to_sds` | `2026-05-31_production-workflow` | active — three messages; disk_to_sds has answered our follow-up Q1/Q2 (SSH VM→dev1 firewall-blocked → file-queue design on shared mount instead; state file under `eqserver_sweep/` with per-host single-writer files). Eqserver to rebuild the orchestrator on that pattern. |

## Closed threads (this repo)

| Peer | Thread | Closure |
|---|---|---|
| `disk_to_sds` | `2026-05-30_ledger-integration` | CLOSED — eqserver-integration branch merged to `sds_staging_ledger` main as `3cbdfd1`; eqserver shipped commits `cdb6439` + `b7bbe56` on `rewrite-suds2sds`. Smoke test on staging VM verified end-to-end. |
| `sds_staging_ledger` | `2026-05-30_ledger-integration` | CLOSED — one-way proposal; ledger session reviewed by committing code, no document reply needed. |

## Future agents reading this

If you're a Claude session opening this directory for the first time:

1. Read `RELATED_PROJECTS.md` to know which peers exist.
2. Browse the thread directory most relevant to your task — files are numbered
   chronologically so you can read top-to-bottom.
3. If you need to send a new handoff message, follow the naming convention
   above and write to **both** your repo's `handoffs/<peer>/<thread>/` AND
   the peer repo's matching directory (full-thread-in-both is the contract).
4. Commit your message with a descriptive commit message; the peer session
   will pick it up on next `git pull`.
