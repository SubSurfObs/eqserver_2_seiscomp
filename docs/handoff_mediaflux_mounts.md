# Handoff: Mediaflux SMB (CIFS) mounts — quick reference (2026-10-29)

The two Mediaflux shares this project works with, as mounted on our hosts.
Both resolve to the SAME Mediaflux backend: `128.250.56.118`
(primary-mf1-qh2.storage.unimelb.edu.au) — every `proj-6700_*` share does,
so bandwidth caps and contention apply at that shared backend, not per
share.

## 1. Staging share (read-write workspace)

- Share: `//mediaflux/proj-6700_seiscomp_staging-1128.4.1649`
- Mounted at `/mnt/seiscomp_staging` on the staging VM
  (`rs-l-0ezd3a.desktop.cloud.unimelb.edu.au` / 172.26.144.41, user
  `dsand`), **read-write**, in `/etc/fstab` with `nofail,_netdev`.
- Credentials: `/etc/fstab-creds-mediaflux` (root-owned, central UoM
  password).
- Holds the shared staging SDS tree (`seiscomp_archive/<YEAR>/<NET>/...`),
  the sweep queue dirs, and the per-network provenance snapshots
  (`eqserver_provenance/<NET>/<YYYY-MM-DD>/`).

## 2. Long-term archive share (the real SeisComP archive)

- Share: `//mediaflux/proj-6700_earth_sciences_seismology-1128.4.1237`
- Mounted at `/mnt/seiscomp_archive`:
  - staging VM: **read-only** by design (the failsafe against wrong-host
    writes);
  - dev1 (`seismology-dev1.its.unimelb.edu.au`, user `seiscomp`):
    **read-write** — the ONLY host that writes it, and only via the
    ledger's `apply.py`. dev1's mount now authenticates via a Mediaflux
    token rather than the username/password file.
- Never delete from this share; writes are promote-only.

## Platform properties worth knowing (verified with RCP 2026-06)

- Quota auto-grants up to **30 TB** per `proj-6700_*` share, no ticket.
- **Soft delete**: anything removed is recoverable for ~1 year via the
  offsite copy (operator-mediated through RCP).
- **Versioned overwrites**: writing over a file keeps the prior version —
  but version history costs quota, so don't lean on it routinely.
- Offsite replication lands within seconds of a local write.
- **`df` lies on these mounts** (reports ~1.9 GB total / 0 used). Judge
  headroom from the share quota, never from `df`.

## Misc

- A third share (`proj-6700_sds_other_networks`) has been mounted ad hoc
  at `/mnt/test_lt` as a proxy long-term target for pipeline tests;
  unmounted after use.
- The origin EqServer archive is NOT Mediaflux: it's NFS
  (`research-nfs.unimelb.edu.au:/6000/6250-mei` at
  `/mnt/eqserver_archive`, read-only, IP-whitelisted `sec=sys`).
