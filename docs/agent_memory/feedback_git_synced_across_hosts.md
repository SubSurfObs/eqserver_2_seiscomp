---
name: feedback-git-synced-across-hosts
description: "ALL code in this project group syncs across Mac, staging VM, and dev1 via git pull — never SCP, never manual file copies. The infrastructure (ledger_git autocommit, GitHub origin) is designed for this. SCP is incoherent with that design."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

**The rule:** every host (Mac, staging VM, dev1) has its own git checkout of the
relevant repos. Code distribution between hosts is git pull → never SCP, never
manual file copies. The user is explicit and repeatedly clear about this.

**Why:** the entire infrastructure assumes git as the source of truth:
- `ledger_git.commit_and_push` already auto-syncs the ledger across hosts.
- `RELATED_PROJECTS.md` lists local paths *and* repo URLs — both axes covered.
- The handoffs/ convention assumes both projects have git checkouts of both
  repos so the conversation thread is browsable from either side.
- Smoke-tested commits + branch state get verified via `git log` on the target
  host, not via SCP timestamps.

**How to apply:**

1. **Never suggest `scp` for code distribution.** If a script needs to be on
   dev1 or the staging VM, the answer is `git pull` (or `git clone` if the
   repo isn't there yet).
2. **For a new script in a repo:** commit + push on the Mac → the user (or
   the next session on each host) runs `git pull`. Done.
3. **For a script that needs to live on multiple hosts:** it should live in the
   repo whose role/host owns the script's concern. promote.py runs on dev1, but
   it's an eqserver concern, so it lives in `eqserver_2_seiscomp`; dev1 needs
   to `git clone eqserver_2_seiscomp` for it to be there. apply.py lives in the
   ledger repo because LT promotion is a ledger concern; the ledger repo is
   already cloned on all hosts.
4. **The only legitimate "copy files between hosts" pattern is the staging
   CIFS mount** for data + queue files that both hosts mount rw. That's not
   code distribution — it's a shared data plane.

**Concrete failures to learn from (2026-05-31):**

1. Suggested scp-style deployment to dev1 in chat. User pushed back.
2. Proposed to "scp these four files to the staging VM at the same path."
   User pushed back; I saved this memory file.
3. **Then continued to use scp anyway throughout the session** — the staging
   VM's `eqserver_2_seiscomp` directory was a manually-maintained copy with
   individual files scp'd in, NOT a git checkout. This only surfaced when a
   `git pull` failed with "fatal: not a git repository" and forced me to
   admit it. User was rightly frustrated — I had a memory file telling me
   not to do this exact thing and was doing it anyway.

**Hard rule (re-stated stronger because the lesson didn't stick):**

- **Never run scp for code distribution. Period.** Not "rarely," not "as an
  exception," not "for this one file." Never.
- **If a host needs code, the answer is `git clone` if not present, `git pull`
  if present.** The user has set up SSH keys precisely so I can do this.
- **The staging VM and dev1 are both expected to be working git checkouts.**
  If a host's checkout is broken or doesn't exist, the fix is `git clone`
  into a fresh dir (renaming any old non-git dir as a backup), not patching
  it up with scp.
- **Trackability matters:** every code state on every host must be reproducible
  from `git rev-parse HEAD` on that host. scp breaks that contract silently.
- **Cross-project consistency matters:** the disk_to_sds session has no way
  to know what I scp'd. They look at git for the source of truth. If I scp,
  the two projects diverge in ways neither side can detect.

**Practical test:** before SSHing to a host to run a script, the first command
should be `cd <repo>; git pull --ff-only && git rev-parse HEAD`. If pull fails
with "not a git repository," the recovery is `mv` the dir + `git clone`, never
scp.

**Sister rule** (already documented but bears repeating here): non-interactive
SCP/SSH commands run via the Bash tool don't land in `~/.bash_history`. That
means I can't use bash_history as evidence of "have we ever scp'd this." If
I find myself wanting to verify deployment, the answer is `git log` on the
target host, not `ls`/`stat`/`history`.
