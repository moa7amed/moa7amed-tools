# Replica: rebuilding an application from evidence

*Understand. Reconstruct. Compare. Improve.*

Replica is part of moa7amed-queue (since 0.6.0). Give it an existing application (a URL,
screenshots, screen recordings, source code, specifications) and ask for it to be
rebuilt. It turns the request into evidence, an architecture and a plan you approve, and
queue tasks that build the result in small verified steps. Then it measures the rebuild
against the reference screenshots, checks its behaviour in a real browser, and runs
bounded correction rounds.

It is not a screenshot-to-HTML generator, and it does not copy applications. It rebuilds
what the evidence shows, says what it does not know, and keeps its claims to what was
measured or tested.

## Contents

- [When to use it](#when-to-use-it)
- [How a request becomes a project](#how-a-request-becomes-a-project)
- [Modes and quality targets](#modes-and-quality-targets)
- [References](#references)
- [Reconnaissance: screens, features, flows](#reconnaissance-screens-features-flows)
- [Design tokens](#design-tokens)
- [Architecture and plan](#architecture-and-plan)
- [Building](#building)
- [Visual comparison](#visual-comparison)
- [Functional parity](#functional-parity)
- [Bounded improvement](#bounded-improvement)
- [Ownership, clean room and privacy](#ownership-clean-room-and-privacy)
- [Settings](#settings)
- [Commands](#commands)
- [End-to-end example](#end-to-end-example)
- [How it compares with other reconstruction tools](#how-it-compares-with-other-reconstruction-tools)
- [Limitations and roadmap](#limitations-and-roadmap)

## When to use it

- Migrating your own application to a new stack, with its source, screenshots and
  documentation.
- Rebuilding an application whose source is lost, from what it shows.
- Prototyping an equivalent of a product pattern, with original branding.
- An accessibility or responsive redesign that must keep the existing behaviour.

Not for: copying a third party's brand, content or code; anything behind a login you are
not authorised to use; extracting data from someone else's application.

## How a request becomes a project

There is no separate assistant to talk to. Ask in your Claude Code session:

```text
Reconstruct this app from its URL and these screenshots: https://...
```

Claude starts the `replica` skill. Its first step classifies the request
(`moa7amed-queue replica intent`): a question about a website ("what does this site
do?"), "clone this git repository", "reproduce this bug" or "rebuild the docker image"
stay ordinary conversation. Requests in English, Arabic and Egyptian Arabic are
recognised. The intents are REPLICA_DISCOVER, REPLICA_VISUAL, REPLICA_FUNCTIONAL,
REPLICA_FULL, REPLICA_COMPARE and REPLICA_IMPROVE.

A queued request (`queue: Replicate this site: https://...`, while Claude is busy) is
saved as a replica project instead of a task, because a reconstruction is planned before
anything is built; continue it with `/moa7amed-queue:replica r1`. Either way the request
is kept verbatim, and nothing is built in the interactive session: building happens in
the queue after you approve the plan, so the same request never runs twice.

The entry points are the `replica` skill, the capture hook and the command line; there
is no separate router.

## Modes and quality targets

| Mode | Rebuilds | Needs |
| :-- | :-- | :-- |
| visual | layout, typography, colours, spacing, responsive behaviour, navigation, forms, tables, dialogs, RTL/LTR | something that shows the screens |
| functional | the above, plus navigation, forms and validation, search and filters, CRUD, state transitions, observable flows; unknown backend behaviour as labelled mocks | screens and observed behaviour |
| full | the above, plus backend, data model, APIs, authentication and authorization, files, background work, deployment | source code, API specifications, database schemas or documentation of the business rules |

The default is the least extensive mode that satisfies the request: "rebuild this
website" is visual, "a working application" or "this application" is functional, and a
mention of the backend, database, API or source code asks for full. A full request
without the evidence it needs runs as functional and says what is missing; add the
evidence and it becomes full.

Quality targets are delivery goals, not guarantees: **prototype** (core screens,
representative navigation, synthetic data, visual verification), **mvp** (main
workflows, data model, core automated tests), **production** (adds security controls,
deployment configuration with backup and recovery notes, and a measured performance
assessment). Visual similarity is never evidence of production readiness.

## References

```bash
moa7amed-queue replica new --request "<verbatim request>" --ownership own|licensed|third_party|unknown
moa7amed-queue replica ref r1 https://example.com/app
moa7amed-queue replica ref r1 screens/list.png
moa7amed-queue replica ref r1 recordings/checkout.mp4
moa7amed-queue replica ref r1 ../legacy-app            # source folder: fingerprinted in place
moa7amed-queue replica ref r1 docs/openapi.yaml --kind api
moa7amed-queue replica ref r1 phone/home.png --platform android
moa7amed-queue replica tools                           # what this machine can capture and read
moa7amed-queue replica capture r1 [url ...] --viewport 1280x800 --viewport 390x844
```

- Files are copied into the queue's data folder (`projects/<key>/replica/<id>/refs`) with
  their SHA-256; prompts carry paths, not contents.
- `capture` uses Playwright when it is installed for the configured Python
  (`replica_python`). It uses Playwright's own Chromium, else an installed Chrome or
  Edge, else a Chromium build another Playwright installation left; nothing is
  downloaded. It stores a screenshot, the page source and a summary of computed styles,
  and lists same-site links so you can decide which pages to capture next. There is no
  crawler: only the URLs you give are opened.
- A page that asks for a login, an HTTP error, a missing tool or a missing file is
  recorded as a **limitation** of the reference, never worked around. Capturing the same
  page again reports whether it changed.
- Recordings: duration and size are read with `ffprobe` when it is installed; otherwise
  the limitation says so. Frames are not extracted; describe the screens or add
  screenshots.
- Mobile screens (`--platform android|ios`) are evidence of layout and visible behaviour
  only. Device automation is reported as available or not; Replica does not drive devices
  and does not reverse-engineer applications.

## Reconnaissance: screens, features, flows

The `replica-recon` and `replica-flows` skills look at the evidence (Claude reads
screenshots as images) and record it with `replica analyze`:

```json
{"screens": [{"id": "S1", "name": "Shipments list", "evidence": "OBSERVED", "refs": ["E3"],
   "route": "index.html", "layout": "...", "components": ["data table"], "actions": ["filter"],
   "navigates_to": ["S2"], "unknown": ["what export produces"]}],
 "features": [{"id": "F1", "name": "Filter by status", "screens": ["S1"], "evidence": "OBSERVED",
   "refs": ["E3"], "behavior": "only matching rows", "backend": false}],
 "flows": [{"id": "U1", "name": "Open a shipment", "evidence": "OBSERVED", "refs": ["E3", "E5"],
   "steps": [{"screen": "S1", "action": "click a reference"}, {"screen": "S2", "action": "details"}]}],
 "unknowns": ["user roles"]}
```

| Evidence | Meaning | Checked |
| :-- | :-- | :-- |
| OBSERVED | seen in a capture, screenshot or recording | must cite an available visual reference |
| DOCUMENTED | stated by a supplied document, specification, schema or source | must cite one |
| USER_CONFIRMED | stated by the owner | |
| INFERRED | concluded, not seen | can never be a requirement |
| UNKNOWN | not known | listed in the report and asked about |

**Sample data.** Generated tasks use synthetic data. When the reference is your own (or
licensed) and its visible data is invented sample data, say so and the tasks reproduce
the rows the inventory lists for each screen (`"data"`), which keeps screens and
behaviour comparable:
`{"sample_data": {"synthetic": true, "evidence": "USER_CONFIRMED"}}`. This never applies to
third-party references.

A flow that is INFERRED or UNKNOWN cannot claim a successful outcome. Only settled
features (OBSERVED, DOCUMENTED, USER_CONFIRMED) become plan requirements; the others reach
the tasks as "build it as a clearly labelled mock or placeholder".

## Design tokens

`replica tokens r1` counts colours, font families, sizes and weights, spacing, radii,
shadows, breakpoints, CSS variables and text direction in captured pages (computed
styles and source) and supplied stylesheets, and the dominant colours of screenshots.
Counts are evidence; the `replica-design` skill turns them into a design specification
and records which tokens were changed on purpose (original branding for third-party
references).

## Architecture and plan

Technology is not chosen from the reference's appearance. The replica project's linked
project plan (same id) goes through the planning engine: infrastructure with evidence
classes, architecture options checked against the target, decisions you approve
([planning.md](planning.md)). `replica plan r1` writes the reconstruction tasks into that
plan:

| Task | Contents |
| :-- | :-- |
| T1 | project structure, layout shell, navigation, design tokens |
| T2... | two or three screens per task, with their inventory entries, reference image paths and (functional, full) features |
| flows | the user flows end to end (functional, full) |
| backend | data model and endpoints from the supplied source, schema or specification (full) |
| tests | browser tests named with feature and flow ids (functional, full) |
| production | security controls, deployment configuration, performance assessment (production target) |

Each task carries only the inventory entries it needs. Running `replica plan` again after
the inventory changed replaces the generated tasks; it refuses while the inventory has
evidence errors.

## Building

`/moa7amed-queue:replica-build` imports the approved tasks (`project execute`,
idempotent) and starts the runner. Each task runs in its own Claude Code session on the
model the router chooses, with the workflow its risk calls for, must report how it
verified its work, and is committed on its own. Tasks of a replica project may read that
project's reference folder (`--add-dir`), so they can look at the reference screenshots.

## Visual comparison

```bash
moa7amed-queue replica config r1 --candidate-url http://localhost:8000/ --page S1=/index.html
moa7amed-queue replica compare r1 --all                   # capture the rebuild and compare every mapped screen
moa7amed-queue replica compare r1 S1 rebuild-s1.png       # or compare a screenshot you took
```

What is measured, printed with every score:

- **Score:** the share of matching pixels among the *content* pixels: pixels that are not
  the reference's background colour in either image. A pixel matches when no RGB channel
  differs by more than `replica_pixel_tolerance` (24 of 255), at the same position or one
  pixel away (`replica_pixel_shift`, 1 by default: anti-aliasing and one-pixel offsets of
  text are not counted as differences; 0 makes it strict). The whole-image share is
  recorded as well, but not used: a large shared background makes two different screens
  look alike (the sample's list and details screens score 0.98 on the whole image, 0.76
  on content).
- **Where:** an 8x6 grid of regions, ranked by differing pixels, and a difference image
  (red where pixels differ).
- **Sizes:** when the sizes differ, the common area is compared and the rest counts as
  different.
- **Masks:** regions with moving content can be excluded
  (`replica config r1 --mask S1=1100,10,160,30`).

Exit code 0 when every compared screen meets `replica_visual_threshold`, 1 when some do
not, 2 when something could not be compared. The score is not perceptual, is sensitive
to fonts, anti-aliasing and dynamic content, and **says nothing about behaviour**.
Images are read with Pillow when it is installed (0.1 to 0.4 s for two 1280x800
screenshots, measured), otherwise with a built-in PNG reader (about 7 s; PNG only). Both
give the same score to within 0.001.

## Functional parity

The parity matrix lists every feature with its evidence, what was expected, whether its
tasks are done, the tests recorded and a verdict: **verified** (a real test passed),
**works against a mock**, **mock**, **built, not verified**, **failing**, **unknown
behaviour**, **out of scope**. A comparison never verifies a feature, and a clickable
button proves nothing about the backend.

```bash
moa7amed-queue replica test r1 --junit report.xml       # test names carry F3 / U2; "mock" marks mocks
moa7amed-queue replica test r1 --feature F3 --result pass --kind real --evidence "what was run"
moa7amed-queue replica report r1 [--output report.md]
```

A skipped check is recorded as nothing, never as a pass.

## Bounded improvement

```bash
moa7amed-queue replica improve r1          # one round: measure, then queue corrections or stop
moa7amed-queue replica improve r1 --run    # round after round, running the queue in between
```

Each round closes the previous one with the latest measurements and queues correction
tasks for the screens that differ most (`replica_fixes_per_iteration`), each with the
score, the method, the regions and the three images. It stops, with the reason recorded,
when:

- every compared screen meets the threshold;
- `replica_max_iterations` rounds were run;
- the replica's tasks have cost `replica_budget_usd` (as reported by Claude Code);
- every correction of a round failed;
- two rounds together gained less than half a percentage point per screen;
- a screen got worse by more than two points: the report names the commit before the
  round, so the corrections can be reverted (nothing is reverted automatically).

## Ownership, clean room and privacy

`--ownership` decides what may travel from the reference into the rebuild:

- **own / licensed:** the owner's assets may be migrated as supplied.
- **third_party / unknown:** original code, branding, artwork and text; layout and
  interaction patterns only. Every generated task says so, and `replica sweep r1 --term
  "<brand>"` reports files in the rebuild that contain the reference's domain names, page
  titles or given brand terms, or are byte-identical to a reference file. The sweep finds
  what it looks for; it does not prove a clean room.

Never: signing in to someone else's application, bypassing logins, paywalls, CAPTCHAs or
rate limits, private APIs, copying personal or customer data (tasks use synthetic data),
storing credentials or session tokens (such URLs are refused and such notes redacted).
Reference files stay in the queue's data folder, outside the rebuild and the plugin.
Details: [security-and-privacy.md](security-and-privacy.md#replica-application-reconstruction).

## Settings

| Setting | Default | Meaning |
| :-- | :-- | :-- |
| `replica_python` | the Python running moa7amed-queue | interpreter with Playwright, for captures |
| `replica_visual_threshold` | `0.9` | content score at which a screen counts as close enough |
| `replica_pixel_tolerance` | `24` | per-channel difference still counted as equal |
| `replica_pixel_shift` | `1` | a pixel also matches its neighbour one pixel away (0: strict) |
| `replica_max_iterations` | `5` | correction rounds |
| `replica_fixes_per_iteration` | `3` | screens corrected per round |
| `replica_budget_usd` | none | stop the rounds when the replica's tasks have cost this much |
| `replica_capture_timeout_s` | `45` | time limit for capturing one page |
| `replica_capture_wait_ms` | `500` | pause after a page loaded |
| `replica_hook_intake` | `true` | a queued rebuild request becomes a replica project |

## Commands

```text
moa7amed-queue replica intent TEXT            is this a reconstruction request? (no side effects)
moa7amed-queue replica tools                  capture, image reading, ffprobe, adb, xcrun
moa7amed-queue replica new [ID] --request T [--mode M] [--quality Q] [--ownership O] [--target DIR]
moa7amed-queue replica list | show ID | status [ID] | report ID [--output F]
moa7amed-queue replica config ID [--mode] [--quality] [--ownership] [--target] [--candidate-url U] [--page S=P] [--mask S=X,Y,W,H]
moa7amed-queue replica ref ID [SOURCE] [--kind K] [--provenance P] [--permission P] [--platform web|android|ios]
moa7amed-queue replica capture ID [URL...] [--viewport WxH] [--full-page] [--screen S]
moa7amed-queue replica analyze ID --data JSON | --file F
moa7amed-queue replica tokens ID
moa7amed-queue replica plan ID
moa7amed-queue replica compare ID (SCREEN FILE|URL | --all) [--reference E]
moa7amed-queue replica test ID (--junit F | --feature F|--flow U --result pass|fail [--kind real|mock])
moa7amed-queue replica improve ID [--run]
moa7amed-queue replica sweep ID [--term T]
```

Skills: `replica` (Claude may start it; it records the request and analyses), the
user-started `replica-recon`, `replica-flows`, `replica-design`, `replica-backend`,
`replica-plan`, `replica-build`, `replica-compare`, `replica-test`, and the read-only
`replica-status`.

## End-to-end example

`tests/replica_e2e.py` rebuilds the sample application in
`tests/fixtures/replica_sample` (a two-screen shipment tracker owned by this repository,
with synthetic data) with the real Claude Code CLI and a real browser, under a hard
spending limit.

Last run (2026-11-20, Claude Code 2.1.329, Windows 11, Chrome 157 through Playwright):

| Step | Result |
| :-- | :-- |
| Request | a request to rebuild the sample application from its `file://` URL and screenshots, recognised as a functional reconstruction |
| Capture | both screens captured (screenshot, page source, computed styles) in 27 s |
| Inventory | 2 screens, 3 features, 1 flow, all OBSERVED and citing the captures; sample data confirmed as synthetic |
| Plan | 4 tasks in the linked plan, approved, imported; all routed to Sonnet with the STANDARD workflow |
| Build | 4 tasks, one attempt each, 6.7 minutes, each committed |
| Functional checks (real browser) | filter by status, search, details, open from the list: all pass after the build and after the corrections; recorded as verified |
| Visual comparison | S1 0.7318, S2 0.9652 (strict method of that run) |
| Correction rounds | 2 rounds, 1 task each; S1 0.7318 -> 0.8963 -> 0.9185, S2 0.9804; stopped at "acceptance met" |
| Cost | $1.38 reported by Claude Code for the run |

Re-measured with the default one-pixel tolerance on the images the run kept: the first
build's S1 scores 0.9125 and the final S1 0.9461 (S2 0.9811); two different screens of the
sample still score 0.76. Most of what the correction rounds fixed were one-pixel offsets.
The remaining differences of the final S1 are sub-pixel shifts of text columns; the
rebuild and the reference look the same to the eye.

Two earlier runs found defects that were fixed before this one: tasks were routed to
Opus because the router read the plan's context blocks and took the word "replica" for
database replication ($0.51, stopped), and the tasks invented different sample data
because the data rule did not distinguish an owned synthetic sample ($0.89).

## How it compares with other reconstruction tools

Three public projects were studied before Replica was designed. Their ideas were
considered; none of their code, prompts or assets is used.

| Area | albertirgi/replica (MIT) | Jakeschincariol/replica-skill (MIT) | thenavidm/remaster (Apache-2.0) | moa7amed Replica |
| :-- | :-- | :-- | :-- | :-- |
| Reconnaissance | scope step | detailed method (prose) | scripted plus method | skills plus evidence classes checked by code |
| Screen and feature inventory | no | screens, features.csv | screens, features with sources | screens, features, flows, unknowns; validated |
| Design tokens | from computed styles | manual | scripted | computed styles, stylesheets, screenshot palette |
| Screenshot comparison | pixel, layout, style and motion axes | edge-density grid | structure plus aligned edges or pixels, masks | content-pixel score with the method stated, regions, diff image, masks |
| Functional verification | none | Playwright template | Playwright, accessibility, console errors | parity matrix: only real passing tests verify |
| Iteration limits | 8 rounds, stall rule | none | none | rounds, budget, stall, regression, failures |
| Architecture and hosting | no | prose | no | the planning engine: evidence, target, decisions, approval |
| Execution | in the session | in the session | in the session | the persistent queue: routed models, workflows, commits, crash recovery |
| Clean room | rule and placeholders | prose plus sweep | enforced by scripts | ownership-dependent task rules plus a sweep |

Ideas adopted from the study: exit codes as a stop signal, stop on a stall and on a
score drop, masks for moving content, stable ids across files, parity where a skipped
check is never a pass, a sweep for the reference's identity, and target content treated
as data. Not adopted (yet): layout and style axes, motion analysis, row alignment of
shifted sections, calibration against the reference's own noise.

## Limitations and roadmap

Implemented and tested (Stage A and B): reference intake, browser capture when
available, reconnaissance records with evidence classes, design tokens, plans through the
planning engine, queue integration, content-pixel comparison, parity matrix with JUnit
import, bounded improvement rounds, clean-room sweep, hook intake, Arabic and English
intent.

Partial or pending (Stage C):

- **Mobile:** screenshots and recordings are accepted and planned for; device or
  emulator automation is not implemented.
- **Recordings:** metadata only (with ffprobe); no frame extraction.
- **Comparison:** pixel-based only; no layout-element, style or motion comparison, no
  alignment of shifted sections, no calibration against dynamic content (masks are the
  manual remedy).
- **Change detection:** a capture of the same page reports a changed page source; there
  is no scheduled re-check.
- **Functional tests** are written by the build tasks and run by you (tasks cannot run
  commands with the default permission mode unless you allow it); Replica records their
  results.
- The reconnaissance analysis is done by Claude in your session; its quality depends on
  the evidence and the model, and the engine checks only that its claims cite evidence.
