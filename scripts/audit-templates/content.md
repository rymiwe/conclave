A finding is a claim a reader would act on that is currently false, or a step that cannot be completed as written. Cite the snapshot as `file:line`. Name the URL fetched. Quote the passage from the fetched source that shows the claim is false, verbatim. Settled product choices are not findings. Propose replacement copy for each finding.

Use these sections, in this order:

## Findings

## Proposed copy changes

## Sources fetched

## Checked and found clean

End the report with one fenced json block and nothing after it. Use `false-claim` or `broken-step` as the verdict. Use an empty list when there are no findings.

```json
{"findings": [{"file": "...", "line": 1, "claim": "...", "verdict": "false-claim", "url": "...", "quote": "..."}]}
```
