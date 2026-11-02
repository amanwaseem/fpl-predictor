# 0026. An entry pushed before its deadline counts, if a maintainer accepts its late merge

- **Status:** Accepted
- **Date:** 2026-11-02
- **Issues:** #117, #120, #118, #119

## Context

GW9's entries were made at 2026-10-31T00:53Z and pushed to GitHub in PR #117
ten hours before the 11:00Z deadline. They were not merged because `ci` was
red on tests that read the newest log files (#118), and merging was still a
manual step (#119). The first reading of 0004 was that GW9 was missed.

0004 refuses late entries because a prediction made after its deadline cannot
be told apart from one made knowing the result. That is not the case here. The
entries were public on GitHub before the deadline: the PR's opening time is
GitHub's, not the committer's, and the files have not changed since. What was
late was the merge, not the prediction.

## Decision

- **An entry counts if its exact bytes were pushed to GitHub before its
  deadline,** shown by a PR opened before the deadline with the entry in it.
  It counts even if it is merged afterwards. It goes into the log byte for
  byte, and the original commit stays on its branch as the proof.
- **A maintainer decides, by name.** `tools/late_merges.json` records each
  accepted file with its SHA-256, its PR, the commit, when the PR was opened,
  and the deadline. The `ci` gate's on-time check (0024) lets through only
  those files, at those hashes. Anything else past its deadline stays
  refused, so auto-merge still cannot land a late entry by itself.
- **GW9 is the first:** `baseline-v1`, `form-fixture-v1` and `gbm-v1` from
  #117. GW9 is not missed, and gbm-v1's first entry is GW9.
- 0004 stands for everything else. An entry made after its deadline, or
  changed since it was pushed, is still refused, and a gameweek with no
  entry pushed in time stays missed.

## Consequences

- The record shows what was actually predicted in time, not when a merge
  button was pressed.
- The proof is outside this repository's history: anyone checking an accepted
  entry compares `late_merges.json` with the PR and commit on GitHub. The
  branch must not be deleted.
- Acceptance is a human judgement. The list keeps it visible and
  reviewable, rather than hidden in a merge.

## Revisit if

- An accepted entry's PR or commit can no longer be shown on GitHub. Then the
  proof would rest on the list alone.
- Late merges become routine. That would mean the merge step is failing, and
  that, not this rule, is what needs fixing.
