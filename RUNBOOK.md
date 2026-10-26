# Runbook: committing a prediction log entry for gameweek N

The procedure for producing one entry in the public prediction log. It is
written to be followed by someone who has not seen the project before, and it
is the same procedure every gameweek — substitute the real number for N
throughout, and zero-pad it in filenames (`gw04`, never `gw4`).

Read `CLAUDE.md` for the hard rules this procedure exists to satisfy. The two
that shape every step below:

- **Rule 1** — a committed prediction is never rewritten. Everything that can
  be checked must be checked *before* the merge, because nothing can be fixed
  after it.
- **Rule 2** — no post-deadline information. This is why the snapshot's timing
  is a step in its own right rather than an afterthought.

---

## 0. Before you start

You need the repository clean, on a branch off `main`, and a working
`fpl-fetch`. Everything runs from the repository root — paths resolve against
the working directory, and every `fpl-*` command refuses to run anywhere else
rather than reading or writing the wrong place.

Activate the virtualenv first. The `fpl-*` commands and `python` live in
`.venv/bin` and do not exist outside it — and a missing command piped into
`tail` exits 0, so a fetch can fail without looking like it did:

```
source .venv/bin/activate
```

(Or prefix every command with `.venv/bin/`.) Each `fpl-<name>` is the same
function as `python -m fpl.<package>.<module>`, so either spelling works.

After pulling a change to `pyproject.toml` or `uv.lock` — the commands are
declared in one, the versions in the other — run `uv sync --locked --no-dev`
once, or a new command will not exist. `--no-dev` is the entry workflow's
environment, `requests` alone: an entry made in the development environment
could import something the workflow's cannot.

Work out three things and write them down:

| | |
|---|---|
| **N** | the gameweek to predict |
| **Deadline** | GW N's `deadline_time`, UTC — see below |
| **Model version** | `baseline-v1` and, from GW6, `form-fixture-v1` — one entry each |

The entry will be written to `predictions/gwNN_<model-version>.csv`.

Each model has its own command, and every step below runs once per model:

| Model | Command |
|---|---|
| `baseline-v1` | `fpl-predict-baseline` |
| `form-fixture-v1` | `fpl-predict-fixture` |

Run both from **the same snapshot**. Head-to-head scoring compares them
player by player, and two snapshots would make that a comparison of snapshots.
By hand, pass `--snapshot <id>` to each command so that a fetch in between
cannot move `LATEST` under the second one.

The deadline lives in `<snapshot>/bootstrap.json`, and `data/raw/` is
gitignored — on a clean checkout there is no snapshot to read it from. Get it
with a cheap bootstrap-only fetch, which takes seconds:

```
fpl-fetch --skip-players
python3 -c "import json,sys
events = json.load(open(sys.argv[1]))['events']
for e in [e for e in events if not e['finished']][:3]:
    print(e['id'], e['deadline_time'], 'next' if e['is_next'] else '')
" data/raw/<timestamp>/bootstrap.json
```

Write it down **before** the real run, so that §3's deadline check compares the
run against something independent rather than against itself.

This is safe to run at any point, including with a deadline close. A
bootstrap-only fetch never moves `LATEST`, so it cannot cost you the last good
snapshot that §9's fallback depends on.

### One entry per (gameweek, model)

**Do not produce a second entry for a gameweek you have already logged.**

Rule 1 means the committed entry cannot be replaced, so a second file does not
correct the first — it sits beside it. `model_version` is the grouping key the
scoring harness pools cumulative metrics over, so a one-off second version
becomes a phantom model with a one-gameweek track record that can never be
fairly compared against anything.

To measure what some pipeline variable is worth — a fresher snapshot, a
parameter change — run it into `scratch/` and diff it against the committed
entry. Post the diff on the PR. That answers the question without putting a
phantom model in the log.

### When the deadline watchdog fires

`.github/workflows/deadline.yml` runs hourly. It fails, and GitHub emails you,
when the next deadline is 10 hours away or less and `main` has no
`predictions/gwNN_*.csv` made for it. The automated entry (below) opens its PR
from 12 hours out, so by then something has gone wrong, and the email says
which:

- **"Read and merge #N"**: the PR is open and waiting for you. Read it as
  below and merge it.
- **"no open PR adds one"**: the entry workflow did not make it. Start at §1
  now. If time is short, go straight to §9's fallback. An entry from a stale
  snapshot is still evidence; no entry is not.

It stops failing once an entry for that gameweek is merged. If it says "could
not check", the API was unreachable. That is not a missed deadline, but look
up the next one by hand as above. If it says "malformed", the API answered in
a shape the project does not expect: its schema may have changed, and
`fpl-fetch` is probably broken too. That needs a person whatever the
deadline.

An entry only counts if its `deadline_utc` is this gameweek's. Entry names
carry no season, so next season's GW4 would otherwise find this season's
`gw04_*.csv` and pass; one from another season is named and not counted.

Once a day, at 07:00 UTC, it also checks `ENTRY_BOT_TOKEN` (below) and fails
when it is missing, rejected, or expires within 7 days.

Check it any time, from the repository root:

```
python tools/check_deadline.py
```

To test the alert end to end, run the workflow by hand from the Actions tab
with a fake deadline: 6 hours and a gameweek with no entry should fail; 48
hours should pass; 6 hours with a logged gameweek should pass.

### The automated entry

`.github/workflows/entry.yml` runs hourly. Once the next deadline is 12 hours
away or less, with nothing for that gameweek on `main` or in an open PR, it
takes a snapshot, runs `fpl-entry`, uploads the snapshot and digest as the
release `snapshot-<id>`, and opens a PR from `entry-bot/<season>-gwNN`. It
never merges. Every decision is `tools/entry_bot.py decide`; each run's summary
page says what it decided and why.

When its PR appears, read it as you would your own run, then merge it before
the deadline:

- `included:` and `excluded:` are what you expect: no unsettled round
  included (§3);
- the deadline is right, and `generated` is before it;
- `ci` is green.

What it does in the cases that are not the usual one:

| It finds | It does |
|---|---|
| an entry on `main` for this deadline | skips |
| an open PR adding one, by hand or not | skips; merge that one |
| its own branch with no PR (an earlier run pushed, then failed to open it) | **resumes**: re-checks the branch, re-verifies the entry against the release asset, opens the PR |
| its own branch whose PR was closed without merging | skips: you closed it, it is not reopened. The watchdog still warns |
| `predict/gwNN` with no PR, inside the window | fails: you are making the entry by hand. Open its PR |
| an entry on `main` for this gameweek number from another season | fails: the log has no season dimension yet. Your call |
| an open PR for a gameweek whose deadline has passed | comments on it, once: close it, do not merge |
| the deadline under 5 minutes away mid-run | stops before pushing or opening anything |

Nothing fails outside the 12-hour window, so a branch you push days early
does not turn every hourly run red. A failure before the push deletes that
run's release again; a failure after it leaves the branch for the next run to
resume. It skips while any open PR adds an entry for the gameweek, so a
hand-made entry and an automated one never both happen.

**Setting it up.** The PR is opened with `ENTRY_BOT_TOKEN`, so that `ci` runs
on it (a PR opened with the default token triggers no workflows). Create a
fine-grained personal access token: this repository only; Contents and Pull
requests, read and write. Add it under Settings > Secrets and variables >
Actions as `ENTRY_BOT_TOKEN`. It is used only to push and to open the PR, and
checked just before; an acting run without a working one stops there. Put the
expiry in your calendar: the watchdog warns a week ahead. A dry run does not
need it.

**Testing it.** Run the workflow by hand from the Actions tab. `dry_run` is on
by default: it ignores the window, makes the entry under `scratch/`, and puts
the digest on the run's summary page. Nothing is committed, released or
opened. A fake deadline (dry runs only) tests just the decision, with the
window kept and nothing fetched: 6 hours and a gameweek with no entry acts,
48 hours skips, and a gameweek with an entry on `main` skips.

### Re-verifying an automated entry

`data/raw/` is never committed (rule 3). Each automated entry's snapshot is
the release asset `snapshot-<snapshot_id>`, named in the PR and the commit
message with its SHA-256. To re-run `fpl-verify` on such an entry:

```
gh release download snapshot-<id> --dir /tmp/snap
shasum -a 256 /tmp/snap/snapshot-<id>.tar.gz     # compare with the PR
tar -xzf /tmp/snap/snapshot-<id>.tar.gz -C data/raw/
fpl-verify predictions/gwNN_<model>.csv
```

Extracting never touches `LATEST`, and `fpl-verify` picks the snapshot by the
entry's own `snapshot_id`.

---

## 1. Timing the snapshot

The snapshot has to land inside a window with a hard edge on each side.

**Not before GW N-1's deadline.** A snapshot predicts its own next gameweek and
nothing else. `resolve_target_gw` refuses any target that is not the snapshot's
`is_next`, in both directions:

- *Earlier than `is_next`* is a rule 2 violation. `status`, `now_cost` and
  `chance_of_playing_next_round` are all snapshot-time, so they are
  post-deadline for the older target.
- *Later than `is_next`* is not a leak, but is silently wrong in the same way.
  `chance_of_playing_next_round` means "next round **as of this snapshot**", so
  predicting GW N from a pre-GW N-1 snapshot scales GW N minutes by GW N-1's
  injury news, and nothing in the entry would record that it happened.

Both are refused outright rather than warned about, so a snapshot taken too
early cannot be used for GW N at all. Take a new one.

**Not during a gameweek.** A snapshot taken while fixtures are in flight carries
a history row for every player, including those whose fixture has not kicked
off. That row reads 0 minutes and 0 points and is indistinguishable from a
player who was left out, so a rolling-form window reads an unplayed match as a
non-appearance. Bonus points are provisional too, until `data_checked` is true.

`predict_baseline` excludes such rounds automatically, but an excluded round is
data thrown away. Prefer a snapshot taken between gameweeks.

**As late as availability allows.** Team news moves right up to the deadline.
Fetch after the final pre-deadline press conferences so injury and availability
flags are current.

**Clear of the deadline.** A full fetch is roughly **six minutes** for ~654
players at the 0.5s inter-request delay. The FPL API is undocumented and
unsupported; it can be slow or down. Do not start the run that has to succeed
with ten minutes left. If it is going to be tight, see §9 — the fallback is
decided in advance precisely so it is not being invented at this moment.

---

## 2. Fetch

```
fpl-fetch
```

Writes `data/raw/<timestamp>/` and, on success, moves the `LATEST` pointer.

Read the run's own output:

- `next gameweek: GW<n>  deadline: ...` — **this must be your N.** If it is not,
  the snapshot cannot predict GW N and step 3 will refuse it.
- The snapshot is only usable once `manifest.json` is written. A run that dies
  part-way leaves a manifest-less directory, which is deliberately not picked
  up as a snapshot.

If a full fetch died **during the player loop**, `fpl-fetch --resume`
continues it rather than restarting the six minutes; files already on disk are
skipped.

Two things about `--resume` that matter at a deadline:

- **It is narrower than it sounds.** `find_incomplete` only offers a directory
  that already holds at least one player file *and* is newer than `LATEST`. A
  fetch that died during bootstrap, fixtures or the live-results step — all of
  which run before the player loop — has no `players/` yet, so `--resume`
  reports `Nothing to resume: every snapshot has a manifest.` even though an
  incomplete directory is sitting there. That message means "nothing
  *resumable*". Start a fresh fetch.
- **It freezes availability at the original fetch's start.** Reusing the
  bootstrap already on disk is deliberate — refetching it would mix two points
  in time into one snapshot — but it means a resumed snapshot's injury flags
  are as old as the attempt that failed, not as old as the resume. If that gap
  spans a press conference, treat the result as a stale snapshot and disclose
  it per §9.

`--skip-players` fetches bootstrap and fixtures only, in seconds. It is useful
for checking deadlines and fixture counts, but a bootstrap-only snapshot cannot
be predicted from and leaves `LATEST` unchanged.

---

## Fast path: steps 3–6 in one command

```
fpl-entry --dry-run      # everything under scratch/, to read first
fpl-entry                # the real entries, into predictions/ and reports/
```

`fpl-entry` runs steps 3–6 for every logged model, from one snapshot
(`LATEST`, or `--snapshot <id>`). The list of logged models is
`LOGGED_MODELS` in `fpl/log/entry.py`. It calls the same commands as the steps
below, so it writes the same files. It differs from them in three ways:

- Every model's scratch run comes first, so nothing reaches `predictions/`
  until every model has run.
- Each real entry must match its scratch run apart from `generated_at_utc`.
- It refuses to start if any of the entries is already in `predictions/`, or
  if the deadline has passed. `--dry-run` only warns about either.

It stops at the first failure, names the model and the step, and lists the
entries and reports that run wrote before it stopped. None of them is
committed: fix the cause, delete them, and run it again. It prints a digest for the PR body (§8) and saves it to
`scratch/entry-gwNN-*/digest.md`. It never commits or pushes. §7 is still yours.

Read its output exactly as §3 says to read each command's: `included:`,
`excluded:`, `prior:`, `ratings:` and the verifier's verdict. Steps 3–6 below
remain the explanation of each check, and the fallback if `fpl-entry` itself is
what fails.

---

## 3. Exploratory run first

```
fpl-predict-baseline --gw N --out scratch/
fpl-predict-fixture --gw N --out scratch/
```

`scratch/` is gitignored. **Never point an exploratory run at `predictions/`,
and never point a real entry at `scratch/`.**

Read these lines before trusting anything below them:

```
snapshot:  <timestamp>
target:    GW<N>
deadline:  <deadline>
included: 1, 2, 3
excluded: none
```

- **`included:` / `excluded:`** — which rounds fed the form window. `excluded:`
  is populated when a round's results are not final, which is the mid-gameweek
  snapshot problem from §1 showing itself. A short `included:` list means the
  prediction rests on less history than you think.
- **`blanking:` / `doubles:`** — printed only when non-empty. Check them against
  the real GW N fixture list. The verifier checks `n_fixtures` mechanically in
  step 5, but a surprise here usually means the snapshot is wrong, not that the
  fixture list is.
- **`deadline:`** — confirm it matches the deadline you wrote down in §0.
- **`prior:`** (form-fixture-v1) — of the players it predicts (a fixture this
  gameweek, settled recent minutes), how many are shrunk toward last season
  rather than a positional guess. Around 80 in 2026/27, fewer if clubs blank. A number near
  zero means the snapshot's `history_past` is missing or its season names
  have changed.
- **`ratings:`** (form-fixture-v1) — club-matches the team ratings rest on: two
  per settled fixture. Well short of 20 × the included rounds means fixtures
  are missing from the snapshot.

Then look at the top-20 table for anything absurd, and confirm the row count
matches the snapshot's player count.

If `included:` is empty the run refuses to write anything and tells you why —
that is the "no usable history" case, and the fix is a fresh snapshot taken
once GW N-1 has settled, not a retry.

---

## 4. Write the entry

```
fpl-predict-baseline --gw N
fpl-predict-fixture --gw N
```

Writes `predictions/gwNN_<model-version>.csv` — the default `--out` is
`predictions/`, so omitting the flag is what makes it a real entry.

This is the point of no return once committed. `fpl/log/contract.py` refuses to write a
log entry at or after its own deadline, and refuses to overwrite an existing
file.

Neither guard survives an editor or a `sed -i`, and **step 7's
`check_log_immutable.py` will not catch a hand-edit to this entry either** — it
compares against entries already committed at the reference commit, and a file
that is new there passes as "new entry" whatever it contains. That is correct
behaviour: appending is the whole point.

So the entry you just wrote is protected by exactly one thing, the verifier in
step 5. **If you edit it by hand for any reason, re-run step 5.** After the
merge, `check_log_immutable.py` protects it permanently, because from then on
it exists at the reference commit.

---

## 5. The verifier gate

```
fpl-verify predictions/gwNN_<model-version>.csv
```

**Non-zero exit means do not commit.** It counts every fault it found and names
the first ten, so fixing one and rerunning is not a loop of one problem at a
time.

The verifier re-opens the snapshot named in the entry's own `snapshot_id`
column and cross-checks the entry against it. Six families of check, plus the
schema validator from `fpl/log/contract.py`:

| | |
|---|---|
| **Coverage** | every player in the snapshot appears exactly once, and nobody else does |
| **Description** | `web_name`, `team`, `position` and `price` say what the snapshot says |
| **Teams** | no unknown short names, and at least 20 teams represented |
| **Fixtures** | `n_fixtures` agrees with `fixtures.json` for GW N — the blank and double check, done by comparison rather than by eye |
| **Availability** | a flagged player's prediction is reduced in line with the snapshot |
| **Provenance** | `deadline_utc` matches the bootstrap event, `generated_at_utc` is strictly before it, and gameweek, model and snapshot are consistent across every row and with the filename |

Schema validity, value sanity and the contract sort order come from
`validate_rows`.

This replaces reading a 654-row CSV by eye shortly before a deadline, which is
the wrong task at the wrong time.

Because it resolves the snapshot by `snapshot_id`, **run it while that snapshot
is still on disk.** `data/raw/` is gitignored and regenerable, but a snapshot
you have deleted cannot be re-fetched — the API serves only the present.

---

## 6. The digest

```
fpl-summarise predictions/gwNN_<model-version>.csv --out reports/
```

Writes `reports/gwNN_<model-version>.md`. Derived output: it reads the entry
alone and never opens a snapshot, so it still regenerates from a clean checkout
long after `data/raw/` is gone. Rule 1 does not apply to it — regenerate it
freely when the format changes.

---

## 7. Commit, PR, merge before the deadline

```
git checkout -b predict/gwNN
git add predictions/gwNN_<model-version>.csv reports/gwNN_<model-version>.md
git commit
git push -u origin predict/gwNN
```

Entries go in through a PR, not a push to `main`. Branch off `main`, and rebase
rather than merging `main` into the branch.

CI (`.github/workflows/ci.yml`) runs both checks below on the PR, so neither
depends on being remembered. Run them locally anyway before pushing — a red check
found at 11:50 on deadline day costs more than one found now.

The PR needs one green check, `ci`. It passes only when everything else did:
the tests below on Linux and macOS, the rule 1 check below on Linux, and a
lint. An entry PR adds no
Python, so the lint cannot fail on it — if `ci` is red, the cause is in the
other jobs, and the job list on the PR names which one.

Before merging:

```
python tools/check_log_immutable.py --ref origin/main
```

The suite runs in CI on the PR, under `ci`; it needs the development
environment, which an entry is not made in.

`check_log_immutable.py` is the mechanical enforcement of rule 1: it compares
every entry committed under `predictions/` at the reference commit against the
working tree, and exits non-zero if one was modified or deleted. A *new* entry
passes — appending is the whole point.

**The merge must land before the deadline.** An entry committed afterwards is
indistinguishable from one made knowing the result, and is worth nothing as
evidence.

---

## 8. The PR body is the record

Once merged the entry is immutable and the PR is the contemporaneous record of
what was checked beforehand. Someone auditing the track record months later
should be able to see the model's reasoning inputs without rerunning anything —
and by then `data/raw/` will be long gone, so anything not pasted in is lost.

Paste in:

- The snapshot id, and **why that snapshot** if it is not a fresh
  post-press-conference one.
- The `snapshot:` / `target:` / `deadline:` / `included:` / `excluded:` block,
  verbatim.
- The `blanking:` / `doubles:` lines, or a note that both were empty.
- The top-20 table.
- The verifier output.

Anything unusual about the run belongs here too, in prose. A surprising
exclusion, a stale snapshot, a team whose fixture count looked wrong — the
value of the log is that its weaknesses are on the record before the results
are known, not explained afterwards.

---

## 9. Fallback, decided now rather than at the deadline

**If the fresh fetch fails or cannot complete in time, use the last good
snapshot and commit anyway.**

A prediction built on slightly stale availability flags is evidence. No entry
at all is not, and the gap in the log is permanent and unfillable — a later
entry for that gameweek would be post-deadline and worth nothing.

**You do not have to do anything to select it.** `predict_baseline` always
reads `data/raw/LATEST`, and a fetch that fails never moves that pointer — it
is written only after `manifest.json` succeeds. So the last good snapshot is
already what the predictor sees, and the fallback is simply to run step 3
onwards as though the failed fetch had not happened.

Check the `snapshot:` line in step 3's output to confirm which one you got.

Two constraints on the stale snapshot:

- Its `is_next` must still be N, or `resolve_target_gw` will refuse it and
  there is no fallback to be had. In practice this means the last snapshot
  taken after GW N-1's deadline. If the only snapshot you have predates that,
  there is no valid entry to make from it.
- Say so in the PR body, and say how old the availability flags are — counting
  from the original fetch, not from a `--resume`. The entry documents itself
  either way, since `snapshot_id` is in every row, but the PR is where the
  judgement gets recorded.

This decision is made here, in advance, precisely so that it is not being made
under time pressure fifteen minutes before a deadline.

---

## 10. Afterwards: score it

Not deadline work. Once GW N has settled — `finished` and `data_checked` both
true, usually the Monday or Tuesday after — take a snapshot and score:

```
fpl-fetch
fpl-score
```

The harness rescores every entry in `predictions/` against that snapshot and
rewrites `scores/` in full. That is safe: `scores/` is derived output, and a
rerun on the same inputs produces byte-identical files. Commit the changes to
`scores/` on a branch like any other.

Read the last lines of its output. `pending:` means a gameweek has not settled
yet; `missed:` means a settled gameweek has no entry and never will.

---

## Quick reference

```
source .venv/bin/activate                                  # the commands do not exist outside it
fpl-fetch                                                  # ~6 min, after press conferences
fpl-entry                                                  # steps 3-6, every logged model — or by hand:
fpl-predict-baseline --gw N --out scratch/                 # exploratory — read included:/excluded:
fpl-predict-baseline --gw N                                # writes predictions/gwNN_<model>.csv
fpl-predict-fixture --gw N --out scratch/                  # form-fixture-v1, from GW6: same two steps
fpl-predict-fixture --gw N
fpl-verify predictions/gwNN_<model>.csv                    # gate: non-zero means do not commit
fpl-summarise predictions/gwNN_<model>.csv --out reports/
python tools/check_log_immutable.py --ref origin/main      # rule 1, mechanically
fpl-score                                                  # after GW N settles, not before
python tools/entry_bot.py decide --prs F --branches F       # what the entry workflow would decide
```
