Every factual claim gets one verdict: `Accurate`, `Inaccurate`, `Misleading`, or `Unverifiable`. An Unverifiable verdict takes one sub-tag: `opinion`, `prediction`, `contested-law`, or `no-source`. Every Accurate, Inaccurate, or Misleading verdict needs a fetched URL. No URL means Unverifiable (no-source). Memory of events after training is not evidence. A claim searched for and not found is no-source, never Inaccurate. Every Inaccurate or Misleading row must quote, verbatim, the passage from the primary source that contradicts the claim or supplies the missing context.

Use these sections, in this order:

## Claim table

One row per claim: line, claim, type, verdict, URL.

## Findings

Inaccurate and Misleading only. Each finding has a URL and a quoted passage.

## Sources fetched

## Not examined

End the report with one fenced json block and nothing after it. Include only Inaccurate and Misleading rows. Use an empty list when there are none.

```json
{"findings": [{"file": "...", "line": 1, "claim": "...", "verdict": "Inaccurate", "url": "...", "quote": "..."}]}
```
