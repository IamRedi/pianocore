# ADR: STATUS Revision Semantics

> **ID:** ADR-STATUS-REVISION-SEMANTICS  
> **Title:** Repository-Specific STATUS Revision Semantics (Pilot Interpretation)  
> **Status:** ACCEPTED  
> **Owner:** Redion Cekrezi  
> **Authorized by:** PIANO-G002  
> **Date:** 2026-09-04  
> **Supersedes:** (none — first policy for this repository)  
> **RWS Context:** Repository-specific pilot interpretation addressing §5.3 self-reference problem

---

## 1. Context

RWS §5.3 requires `STATUS.md` to record the "exact HEAD" of the repository. During PIANO-G001, a self-reference problem was identified: the STATUS.md field recording the commit SHA cannot be updated to include its own commit's SHA before that commit is made. Any STATUS.md that attempts to record the SHA of the commit that contains it would require the file to be written twice — once before committing (without the SHA) and once after (with the SHA, requiring a second commit).

This ADR defines the repository-specific policy that resolves this problem without modifying the global RWS standard.

---

## 2. Decision

### A. Canonical Status Artifact

`docs/work/STATUS.md` is the canonical human-readable repository status artifact for `piano-core`. It is the single source of truth for current task state, blockers, and transition eligibility.

### B. Field Semantics: `captured_head`

The field previously named `local_head` is semantically clarified as **`captured_head`** for this repository's interpretation:

```
captured_head: <SHA>
```

**Meaning:** The exact Git HEAD SHA that was observed and recorded **at the moment this status snapshot was written**.

This field does **NOT** claim to equal the SHA of any future commit that may later contain this STATUS.md file.

> Note: The YAML key in STATUS.md currently uses `local_head` for compatibility with prior records. This ADR's interpretation applies to that field. A future governance task may rename the key; until then, `local_head` carries the semantics defined here.

### C. Authoritative Source for Live HEAD

The live current HEAD is always obtained deterministically from Git:

```bash
git rev-parse HEAD
```

STATUS.md's `captured_head` / `local_head` field is a **snapshot**, not a live value.

### D. Non-Self-Referential Commitment

Because `captured_head` records the HEAD *at snapshot time*, a committed STATUS.md is not self-referential. The file truthfully describes the HEAD that was current when the snapshot was taken; the act of committing the file creates a new HEAD that supersedes the snapshot.

### E. Authoritative Fields in STATUS.md

A status snapshot may truthfully describe, at snapshot time:

- `captured_head` (or `local_head`) — HEAD SHA at time of writing
- `staged` — files staged at time of writing
- `tracked_unstaged` — tracked modified files at time of writing
- `untracked` — untracked paths at time of writing
- `active_task` — currently authorized/in-progress task
- `accepted_revision` — the last formally accepted revision
- `deployed_revision` — the last deployed revision (if applicable)

### F. No Mandatory Rewrite After Commit

STATUS.md does **NOT** need to be immediately rewritten after every commit solely to insert the new commit's own SHA. Rewriting STATUS.md is only required when **repository state or task state materially changes** (e.g., task transitions, blocker changes, acceptance events).

### G. Stale Snapshot is Not an Error

If the live HEAD (from `git rev-parse HEAD`) differs from the recorded `captured_head` because a later commit occurred, this is not automatically an error. Git is the authoritative source for the current HEAD; STATUS.md is a human-readable snapshot.

### H. Derived Runtime Information

Automation and UI tools MAY display:

```
current_head = $(git rev-parse HEAD)
```

as **derived runtime information**. This does not conflict with the recorded `captured_head` in STATUS.md.

---

## 3. Alternatives Considered

| Alternative | Reason Rejected |
|---|---|
| Two-commit approach (write STATUS before commit, then amend) | Requires amending history; prohibited by RWS §11.2 |
| Omit HEAD from STATUS entirely | Violates RWS §5.3 requirement |
| Record HEAD as `PENDING` then update in second commit | Requires a second commit per status update; creates commit noise |
| Modify global RWS §5.3 | Out of scope for this pilot; RWS changes require §20 process |

---

## 4. Consequences

**Positive:**
- Eliminates the circular dependency between STATUS.md and Git commit SHA.
- STATUS.md updates are required only on meaningful state changes.
- Agents can correctly interpret STATUS.md without assuming it reflects the live HEAD.
- Compliant with RWS §5.3 intent (record the HEAD that was current when the snapshot was captured).

**Constraints:**
- Agents must always use `git rev-parse HEAD` for the live HEAD, not `local_head`/`captured_head` from STATUS.md.
- This is a repository-specific pilot interpretation. It does NOT amend global RWS §5.3.
- This ADR must be reviewed and either confirmed or superseded before Piano Core product implementation begins.

---

## 5. RWS Compliance Note

This ADR constitutes a **repository-specific pilot interpretation** under RWS §20 (Exceptions and Evolution) and RWS §21 (Adoption Protocol). It does not amend the global RWS document. The deviation is narrow, time-bounded to the pilot phase, risk-assessed (governance-only impact, no product risk), and Owner-approved.

---

## 6. Record Integrity

This ADR MUST NOT be silently modified. Supersession requires a new ADR with Owner authorization.
