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
`python -m fpl.fetch`. Everything runs from the repository root — paths resolve
against the working directory, not against the module.

Work out three things and write them down:

| | |
|---|---|
| **N** | the gameweek to predict |
| **Deadline** | GW N's `deadline_time`, UTC — see below |
| **Model version** | `baseline-v1` today; whatever model is being logged |

The entry will be written to `predictions/gwNN_<model-version>.csv`.

The deadline lives in `<snapshot>/bootstrap.json`, and `data/raw/` is
gitignored — on a clean checkout there is no snapshot to read it from. Get it
with a cheap bootstrap-only fetch, which takes seconds:

```
python -m fpl.fetch --skip-players
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
python -m fpl.fetch
```

Writes `data/raw/<timestamp>/` and, on success, moves the `LATEST` pointer.

Read the run's own output:

- `next gameweek: GW<n>  deadline: ...` — **this must be your N.** If it is not,
  the snapshot cannot predict GW N and step 3 will refuse it.
- The snapshot is only usable once `manifest.json` is written. A run that dies
  part-way leaves a manifest-less directory, which is deliberately not picked
  up as a snapshot.

If a full fetch died **during the player loop**, `python -m fpl.fetch --resume`
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

## 3. Exploratory run first

```
python -m fpl.predict_baseline --gw N --out scratch/
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

Then look at the top-20 table for anything absurd, and confirm the row count
matches the snapshot's player count.

If `included:` is empty the run refuses to write anything and tells you why —
that is the "no usable history" case, and the fix is a fresh snapshot taken
once GW N-1 has settled, not a retry.

---

## 4. Write the entry

```
python -m fpl.predict_baseline --gw N
```

Writes `predictions/gwNN_<model-version>.csv` — the default `--out` is
`predictions/`, so omitting the flag is what makes it a real entry.

This is the point of no return once committed. `fpl/log.py` refuses to write a
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
python -m fpl.verify_entry predictions/gwNN_<model-version>.csv
```

**Non-zero exit means do not commit.** It counts every fault it found and names
the first ten, so fixing one and rerunning is not a loop of one problem at a
time.

The verifier re-opens the snapshot named in the entry's own `snapshot_id`
column and cross-checks the entry against it. Six families of check, plus the
schema validator from `fpl/log.py`:

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
python -m fpl.summarise predictions/gwNN_<model-version>.csv --out reports/
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

Hard rule 4 forbids pushing to `main`; opening a PR and merging it is the
sanctioned path. Branch off `main`, and rebase rather than merging `main` into
the branch.

Before merging:

```
python tools/check_log_immutable.py --ref origin/main
python -m unittest discover tests
```

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

## Quick reference

```
python -m fpl.fetch                                          # ~6 min, after press conferences
python -m fpl.predict_baseline --gw N --out scratch/         # exploratory — read included:/excluded:
python -m fpl.predict_baseline --gw N                        # writes predictions/gwNN_<model>.csv
python -m fpl.verify_entry predictions/gwNN_<model>.csv      # gate: non-zero means do not commit
python -m fpl.summarise predictions/gwNN_<model>.csv --out reports/
python tools/check_log_immutable.py --ref origin/main        # rule 1, mechanically
python -m unittest discover tests
```
