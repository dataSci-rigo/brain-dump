# Habit Engine Plan — implementing *The Power of Habit* (Duhigg)

*Sub-plan / decision document, 2026-08-22. Compares three homes for a full
Power of Habit implementation and ends with a recommendation. Nothing here is
built yet; the poh quip miner (this folder) ships independently and feeds
whichever option is chosen.*

## 1. What "implementing Power of Habit" means

The mechanics worth building, from the book:

| Mechanic | What the app must do |
|---|---|
| **Habit loop** (cue → routine → reward, driven by **craving**) | Model a habit as a loop with all four parts; log observations of the loop firing |
| **Golden rule of habit change** | Change a habit by keeping the cue + reward and swapping only the routine — guided "diagnose then substitute" flows |
| **Framework (book appendix)** | 4-step diagnostic: (1) identify the routine, (2) experiment with rewards, (3) isolate the cue (location / time / emotional state / other people / preceding action — log all five at each urge), (4) have a plan |
| **Implementation intentions** | "When \<cue\>, I will \<routine\> because it gives me \<reward\>" — stored, rehearsed, pinged |
| **Keystone habits** | Flag habits whose ripple effects matter most; track their knock-on wins |
| **Small wins & streaks** | Adherence tracking, streaks, celebrating small wins |
| **Belief** | The ingredient that makes change stick under stress — reflection prompts, mantras, evidence-of-change reviews |

**How the poh quip miner feeds this:** kept **whys** are craving/reward
material (what the user actually wants — the reward a new routine must
deliver), and mined **starting fears** are cue material (the emotional states
that trigger avoidance routines). Whatever option is chosen should read
`poh_quips`/`poh_concepts` from brain-dump's `notes.db` (read-only sqlite,
same pattern brain-dump uses for STM/Compass) — the mined data is the
personalization layer the book can't provide.

## 2. Option A — praxis Django module

New Django app `apps/habits/` inside praxis, following its established
add-a-capability pattern (new app → models + admin → one conversation module
in `apps/bot/handlers/` registered in `all_handlers` → jobs in
`apps/bot/scheduler.py` → analytics in `apps/insights/services.py` → web
templates).

**Models sketch:**
- `Habit(name, kind=BUILD|CHANGE, status, is_keystone, goal FK→goals.Goal null)` —
  keystone habits hang directly off the existing TOP/MID/LOW goal hierarchy.
- `HabitLoop(habit FK, cue_location, cue_time, cue_emotion, cue_people,
  cue_preceding, craving, routine, reward, is_current)` — versioned so
  golden-rule substitutions keep history.
- `LoopObservation(habit FK, occurred_at, cue_* snapshot fields, followed_routine,
  reward_felt 1-5, notes)` — the framework's step-3 cue isolation log.
- `RewardExperiment(habit FK, alternative_routine, reward_hypothesis,
  craving_gone_after_15min, notes)` — framework step 2.
- `ImplementationIntention(habit FK, text, rehearsal_count, last_rehearsed_at)`.

**Leverage (what comes free):** owner-gated PTB bot + JobQueue/APScheduler for
scheduled pings; ESM machinery as a template for random cue-sampling pings;
`library.BookCard` already anticipates more books (`BOOK_CHOICES` includes
WILLPOWER and SMART_CHANGE — adding POWER_OF_HABIT is a 2-line change, and
kept poh quips could even be synced in as cards); `insights` + the Chart.js
website give streaks/adherence/keystone-ripple analytics without new infra;
Django admin gives free CRUD for loop editing.

**Costs:** praxis has no weekly-curriculum engine (no equivalent of
wp_instinct's program.yaml weeks) — but see the recommendation: Power of
Habit doesn't really need one. Django app + migrations + handler conversation
is a heavier lift than a script bot; two services (praxis-bot, praxis-web)
must both deploy the change.

## 3. Option B — wp_instinct extension

Generalize wp_instinct's engine to run a second program. What maps well: the
`program/program.yaml` week engine (theme / microscope observation /
experiment / rotating daily prompts / typed structured questions) is exactly
the shape of a guided book program; the `urges` table
(trigger ≈ cue, gave_in ≈ routine ran, intensity) is two columns short of a
habit-loop log (add craving/reward); morning-nudge 8 AM / evening check-in
9 PM / weekly kickoff / weekly Claude synthesis already exist.

**Costs:** the engine hardcodes McGonagall's frame throughout its ~2,400
lines — `challenge_type i_will/i_wont/i_want`, `i_want_anchor`, week-skill
conversation states bespoke to weeks 1–3, synthesis prompts written for
willpower. Making the engine program-agnostic (program registry, per-program
DB scoping, per-program synthesis prompts, neutral challenge model) is a real
refactor of a working single-purpose bot, with regression risk to the live
Willpower program. And weeks 4–10 of the *current* program are still stubs —
the engine's owner has more curriculum debt before taking on a second book.

## 4. Option C — standalone bot

Clone wp_instinct's skeleton (PTB + aiosqlite + YAML program) into a new
project with its own token and service. Full freedom, no regression risk to
anything.

**Costs:** a fourth bot process on the VM (todo, stm, braindump, praxis ×2,
hab7bot ×3, food… — ops surface keeps growing); duplicates scheduling,
storage, synthesis, and analytics praxis already has; another token, env
section, registry entry, panel row; and it walls the habit data off from the
goal hierarchy and insights where it is most useful.

## 5. Comparison matrix

| Criterion | A: praxis module | B: wp_instinct ext. | C: standalone |
|---|---|---|---|
| Data-model fit (loops, keystone→goals) | **Best** — FK to Goal hierarchy | OK (urges ≈ loops) | OK but isolated |
| Scheduling machinery | Good (JobQueue, ESM patterns) | **Best** (daily/weekly cadence exists) | Build from clone |
| Reuse vs refactor effort | New app, no refactor | **Refactor of live bot** | Clone + rewrite |
| Analytics / review UI | **Web + Chart.js free** | Claude synthesis only | None |
| Ops burden | No new services | No new services | **+1 service, +1 token** |
| Regression risk to existing bots | Low (additive app) | **High** (shared engine) | None |
| Multi-book future | **Designed for it** (BookCard) | Single-program by design | N/A |
| poh data integration | RO sqlite read of notes.db | RO sqlite read | RO sqlite read |

## 6. Recommendation: **Option A — praxis module**

Reasoning: *The Power of Habit* is **loop-data-shaped, not
week-curriculum-shaped**. Unlike McGonigal's book (a literal 10-week course,
which is why wp_instinct's engine fits it so well), Duhigg's method is a
diagnostic cycle you run per-habit, on the habit's own timeline: observe the
loop, experiment with rewards, isolate the cue, install a plan, then track
adherence indefinitely. That is models + conversations + analytics — praxis's
strengths — not a sequential program player. Praxis is also the designated
multi-book home (BookCard precedent), keystone habits belong on its goal
hierarchy, and its website turns small wins/streaks into visible evidence —
which is itself the book's "belief" ingredient.

**Tie-breakers:**
- Choose **B** only if you decide you want Power of Habit delivered as a
  strictly sequential N-week curriculum clone of the wp_instinct experience —
  then the YAML engine is worth generalizing (do it after weeks 4–10 of the
  willpower program are authored, so the refactor serves two programs).
- Choose **C** only if praxis is being retired or you explicitly want the
  habit tracker isolated from everything else.

**Suggested build order when it happens (A):** models + migrations + admin →
`/habit` bot conversation (create habit → guided loop diagnosis via the
4-step framework) → LoopObservation quick-log (`/loop` with inline buttons) →
scheduled pings (implementation-intention rehearsal AM, adherence check PM) →
insights (streaks, keystone ripple, cue-pattern breakdown) → web pages →
poh integration (pull kept whys/fears from brain-dump's notes.db read-only;
surface the right "why" quip inside a habit's reward framing).
