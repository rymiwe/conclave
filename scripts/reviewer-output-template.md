# Reviewer output template

Canonical schema for a review.md artifact. Enforced by
`review-lint <review.md> --repo <path> --sha <sha>`
(fleet helper; set `CONCLAVE_REVIEW_LINT` to its path; skip if unset).

Chair loads this file into the reviewer brief. Reasons stay in the brief;
the mechanical rules live here and in review-lint.

## Findings

Write findings most severe first. Each finding is a `###` heading with:

- **Severity**: `bug` (wrong behaviour), `suggestion` (real improvement, not a
  defect), or `nit` (cosmetic). Exactly one.
- **File:line** — `path/to/file.ext:N` at the review sha. The line must exist
  in `git show <sha>:<file>`; read the file and confirm the line holds what
  you claim.
- **Description** — the concrete failure: input or state, then wrong result.
- **Suggestion** — the fix, executed if it is executable.

Example:

```markdown
### Null deref on empty input
- **Severity**: `bug`
- **File:line**: `app/models/user.rb:42`
- **Description**: `User.find` with blank id raises instead of returning nil.
- **Suggestion**: guard with `return if id.blank?` before the find.
```

## Verdict

One line:

```text
VERDICT: APPROVE | COMMENT | REQUEST_CHANGES
```

Mapping from worst finding severity (review-lint enforces):

| Worst severity | Required VERDICT |
|---|---|
| `bug` | `REQUEST_CHANGES` |
| `suggestion` (no bug) | `COMMENT` |
| `nit` only, or no findings | `APPROVE` |

## Checked and found clean

End with a **"Checked and found clean"** list naming the areas you examined
that produced no finding, and for anything you tested by hand, the exact inputs
you tried. This is how the reader tells your silence about an area from your
judgment about it, and it exposes your own coverage gaps.

## Could this be simpler?

Required section. A one-line answer passes, for example:

```text
no, because the extra branch guards a real concurrent write.
```


## House rules (optional)

Conclave ships this generic template only. Per-caller house rules are **not**
stored in this repository. At run time, `scripts/reviewer-prefs.sh` reads
`CONCLAVE_REVIEWER_PREFS` (absolute path to a markdown file outside the repo).
When that variable is unset or the file is missing, the prompt states that no
house rules are configured and these generic defaults apply.

## Do not

Do **not** modify the code under review. State the review file's full path
and summarize the verdict in the final response.
