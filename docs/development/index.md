# Development record

These pages are a record of how moonbuggy was built. They are not documentation
about how to use it, and they are deliberately excluded from the published site
(see `exclude_patterns` in [../conf.py](../conf.py)) — they have no toctree
entry and no page at
[jhamon.github.io/moonbuggy](https://jhamon.github.io/moonbuggy/).

They stay in the repository because several of them are cited by path from
source comments, and because the record of what was tried — including what did
not work — is worth more than a tidy tree.

| page | what it is |
|---|---|
| [acceptance-criteria.md](acceptance-criteria.md) | Phase 0 + Phase 1 criteria, and their status |
| [next-milestones.md](next-milestones.md) | the four Phase 2 milestones, written as checkable claims |
| [phase-2-status.md](phase-2-status.md) | criterion-by-criterion outcome for the above |
| [spike-a-findings.md](spike-a-findings.md) | in-memory mutation, pytest, and xdist |
| [spike-b-findings.md](spike-b-findings.md) | coverage mechanism for the line→test map |
| [perf-hypotheses.md](perf-hypotheses.md) | predicted vs actual saving for every optimisation attempted |

## Related, and elsewhere

`docs/superpowers` holds the design spec and implementation plan from the
sessions that produced this work — the same kind of material, excluded from the
build for the same reason. Its documents refer to the pages above by their old
`docs/` paths. Those links are stale by design: they are a frozen record of what
was written at the time, and rewriting paths inside them would edit history for
no reader.

## Still published

Three pages that once sat in this group remain on the site, because they are
measurements about moonbuggy rather than notes about building it:
[benchmark-results](../benchmark-results.md),
[differential](../differential.md), and
[oss-findings](../oss-findings.md).
