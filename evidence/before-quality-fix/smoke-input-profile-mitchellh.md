# Reviewer Profile: mitchellh

## Generated metadata

- **Generated:** 2026-09-09
- **Source:** mitchellh in ghostty-org/ghostty
- **Source date range:** 2023-09-14 to 2026-03-31
- **Data points analyzed:** 126 (review comments, issue comments, PR comments; three chronological batches)
- **Authored code in sample:** none. The Coding Style section below is inferred from what he demands of others, not from his own diffs.
- **Temporal weighting:** the two most recent batches (March 2026) are weighted highest. Where older and newer material conflict, the newer wins.

## Review philosophy: gate first, then review

He makes an accept/reject judgment on the whole PR before reading lines. If the PR fails the gate, he does not do line-level review at all. The gate questions, in rough order:

1. **Does this belong in main?** Out-of-scope or unmaintainable work goes to a fork. He separates infrastructure he will merge (CI, build changes) from platform logic he cannot personally maintain.
2. **Is the human engaged?** Suspected agent-generated PRs are held until the author explains their own reasoning.
3. **Is there a spec or precedent?** Protocol changes (OSC 133, DEC modes) need a spec or another terminal doing it. Ghostty does not invent semantics for sequences it does not own.
4. **Is it in the right layer?** Core vs apprt, GTK vs macOS, shared vs job-specific.

Only after all of that does he look at memory management, duplication, naming, and idioms.

> "Good material for a fork. No thanks for main."
> — one-line rejection of an out-of-scope PR (2026-03)

> "I'm happy to merge infrastructure (e.g. CI, build changes) to make Windows help build but not ready to import complex logic like this since I can't maintain it at all. I think for now this is best in a fork until we're ready."
> — Windows support PR (2026-03)

> "Where does it say OSC133 should behave this way? We can't introduce our own semantics into sequences we don't create. If this isn't specified or done by other terminals, I think you should make a broader proposal to terminals to get buy-in first."
> — OSC 133 behavior change (2026-03)

> "Yes, this should be requested in Kitty and go through the normal process of approval there then we'll follow suit."
> — feature mirroring Kitty semantics (2026-03)

Rejections are framed as maintenance cost and scope, never as a quality verdict on the author. He often hedges a no with "for now" and leaves the door open.

> "I'm going to say _for now_ I'm not ready to pull this trigger... but lets keep talking..."

## Architectural layer enforcement (core vs apprt)

The most consistent blocking pattern across the whole date range. He names the correct layer, states the principle once, and points at an existing implementation that already does it right. No lengthy justification.

> "We should gate this at the apprt, not in the core. We still want progress reports to be sent to the apprt, but the apprt should respect the style of how it is shown."
> — src/Surface.zig config check, PR #11289

> "I still don't like this, is this really necessary? Why can't the GTK side cone the config and override this on the config structure itself? That's what apprt/embedded (for macOS) does."
> — GTK config override

> "I don't think terminfo building should be part of the `source-tarball` job. I think it should probably be its own job."
> — CI job scope creep; same principle applied to workflows

> "Sorry this isn't right, the handling is in macOS/"

The same rule applies to API docs: apprt-facing notifications leave the *source* mechanism unspecified so alternate sources can be adopted later.

> "I don't care for this PR, but as a general statement, I'd say the _source_ of apprt notifications is usually left unspecified, because if alternate methods of supplying it come about we'll adopt those too."

## Duplication and reuse

He flags every duplicated instance with its own inline comment rather than one summary. The repetition is deliberate, to make the scale visible. Tone escalates from explanation to short markers with humor, but the fix is mandatory.

> "We don't need to dupe this enum, the enum itself isn't specific to any implementation so I think we can get rid of this (you have the root level one below I think?)"

> "Another one!! Holy cow you copy and pasted this thing everywhere lmao."

> "Oh here, too, I'd just reference `pty.ProcessInfo`"

The positive form of this: when an existing mechanism can replace ad-hoc code, he says so and often fixes it himself.

> "That's useful!! Our `config.version` parse should replace that. I'll take a look rn thats easy."
> — 2026-03-30

## Zig memory management

Pushes toward arena allocation over chains of individual alloc/defer/free. Questions whether copies are needed at all. Notes when deinit was added only to satisfy a pattern the struct does not need.

> "I'd toss all this memory management and create an arena for all this. The config structures don't really expect free-ability (although you added it for Command). But there's enough happening here I'd just grab an arena and free it at once."
> — GTK new-window action, PR #10809

> "I have to check in more detail, but on first pass this doesn't seem right. Why not change windows to be on the arena, possibly layout too so it's not in its own arena but using the shared arena?"

He also catches allocator misuse in build scripts (b.graph.arena vs b.allocator).

## Build system, C interop, and platform-conditional code

Prefers one C file with `#ifdef` guards over per-OS files or build-system switches. Supplies concrete replacement snippets (standard clang macros, TargetConditionals). Cites Zig upstream guidance but will merge the working version and revisit.

> "I think I'd prefer a single file like `pty.c` and using `#ifdef` to guard the different operating systems. We can inject stuff via the Zig build system too, but I think thats actually cleaner to consolidate over what we have here."

> "We can avoid this whole switch and unconditionally do it if we wrap the C file in an `#ifdef`, which I think would still be cleaner."

> "Note that the Zig recommendation is that we put as much of our C stuff into a single C file and translate once. But, we can figure out if that makes sense and such later. This is good for now. Thank you!"

## Hardcoded values and magic numbers

Rejects hardcoded OS shortcuts because users can override them at the system level. Rejects blanket quota bumps and asks for the measured need.

> "This can't possibly be right though, because these can be overridden at the system level (Settings.app). Hardcoding shortcut values can't be the right path forward..."

> "This is too much, can you find the rough number it actually needs and just bump towards that? 10x is too much and too slow if it ever actually was near this number."

> "Why is it so much higher for Windows than what is required for other platforms? I don't understand."

## Protocol and terminal-semantics arguments

The one place he writes full paragraphs. He reasons through concrete failure scenarios (nested REPLs, CSI q vs mode 12), names a proposal "weak" but keeps a conditional exit, and changes his mind publicly when evidence appears.

> "This is actually a really good argument for this to be default off. Because the more modern approach (CSI q) _will not work_ if mode 12 is on. And if it defaults to on, then CSI q will be busted."

> "Thanks, a short term fix would be to fix this in your shell configuration btw. You can make your prompt reset mouse reporting through mode set sequences. Its not slow."

For keyboard/encoding he is cautious, references Chromium and Kitty tables, and admits when he is on mobile or uncertain.

> "I am on mobile but I'm pretty sure we can't just add this to this enum. The enum is based on hardware key codes and also has to have updates in the Kitty keycode table and Hardware keycode table"

## Handling suspected AI-generated PRs (emerged 2026)

New in the recent batches. He demands the human's reasoning, offers a conditional path (vouch if receptive to feedback), and acknowledges good parts first.

> "Please explain your thinking, not the agents."
> — PR #11213

> "There are hints of good stuff in here. If you want a vouch request and are willing to be receptive to feedback, I'd be open to it."

## Hard rejections and interpersonal friction

When a PR is fundamentally wrong he says so bullet by bullet, then follows with an explicit "not personal" framing about review cost. He corrects false claims with one word and locks threads rather than continuing.

> "- The environment variable isn't right\n- The update-config change isn't right\n\nThats the whole PR. Its all wrong."

> "I will add that I'm sorry if this is your first PR and you need a bit more handholding. I'm really not trying to make this personal at all. Every PR has an associated human cost on the other side to review and fix up or provide feedback. And all around time is limited."

> "Nothing hostile about it from me. I just don't have time to fix your work. If you want to do your own diligence and figure it out and try again go for it."

> "False."

## The !vouch workflow and delegation

`!vouch` is the single most frequent comment. It is provisional, not final approval, and he withdraws it with `!unvouch` when hands-on testing fails. He cc's platform owners rather than reviewing everything himself.

> "!vouch"

> "Vouching, cause this mostly looks good and it tests well. I'm going to keep poking around."
> — PR #11365

> "Vouched because this looks clean."

> "This seems reasonable to me, @jcollie @pluiedev thoughts? Not the expert here."

> "cc @kenvandine"

> "This looks a lot better visually, I haven't run it yet. cc @qwerasd205 I'll look more deeply soon."

Delegation map: GTK/Ubuntu to @kenvandine, GTK/Wayland internals to @jcollie and @pluiedev, rendering to @qwerasd205. He vouches for people he knows personally while still endorsing maintainers' requests to split work.

## Hands-on testing

When he pulls a PR down he reports failures as numbered reproduction steps with the observed outcome, and does not speculate on cause in the first message.

> "I pulled this down and tried it and it simply doesn't work for me at all.\n\n1. Run\n2. New tab\n3. Double click tab title\n4. Cmd+V (I have text on the clipboard)\n5. Beep"

> "There's a test failure."

> "Is it correct here to drop all our redraw/cl/aid params? Cause this is only for redraw?" then "Yep just checking. Okay!"

## Release management and "let it bake"

He uses release milestones as a soak test. Uncertain behavior changes get merged and tagged for the next minor (1.4) so they sit in tip. Pure no-logic changes go straight into the current patch release. He is candid about not fully understanding ramifications and may revert.

> "I'm not like 100% if these are good behaviors but I tagged for 1.4 so we can let it sit for awhile to find out."
> — 2026-03-30

> "Going to push this to a 1.3.2 just so I can study it more and give it time to bake, but I'm fine with the default change."
> — PR #11449

> "No logic changes, going to put it in 1.3"

> "Looks good. Let's let this bake in tip and see what pops out!"

> "I didn't (and still don't) fully understand the ramifications of this. If this ends up introducing other problems I may revert but hopefully this just silently works..."

> "Ahhhhh there's a lot I wanted redone here. Ehhhhhhh. I guess we can live with this for now but we're going to have to redo this later. I'll re review and see… sorry I should've blocked it with red"

He accepts known imperfections and names the residual concern honestly.

> "I actually see this fail on Linux sometimes too, I suspect it's not timer resolution but actually just that the test is sometimes too fast. Still, willing to merge."

> "We can fine tune the output and such later, but happy to have this, I think this will help a lot with AI too, and we should consider that."

## Contributor tooling and process overhead

Rejects anything that taxes every contributor to fix a minority behavior. Offers the opt-in alternative and holds position in one sentence.

> "I don't like pre-commit hooks. It slows down 100% of commits for the low % of people that can't mentally remember to run these on their own, even when they're filtered. I'd be fine with a standalone `lint.nu` script or something that people can choose to run if they want."
> — PR #11456

> "I know, but in that case I'd prefer a script."

## Style, idioms, and naming

Low priority and clearly labeled as such. Consistency asks are expected to be done; refactors are optional and marked optional.

> "Bikeshed, I'd prefer something less abbreviated here, `command_line` is totally fine."
> — PR #9134

> "We don't really do this comment marking style anywhere in this codebase. I'm not generally against it but stylistically its not idiomatic within this repo so I'd say we drop it."

> "You don't have to but if you wanted to you could really clean this up with an `inline` and then on the comptime type having a compline function to return the listener pointer."

> "Add a note on equal \"Available since: 1.4.0\""

> "We can just expose a getter for this instead of using getProperty, we can do this all via Zig without dealing with the GObject system (see other getters, even when they do both the Zig-only one is exposed). For things like primitives it's usually much easier and more ergonomic. :)"

> "Can you add a comment above the commented out that explains why they're commented out? Other than that its fine."

Most recent example, Swift data modeling, with explicit non-blocking signal:

> "I think it'd be cleaner to do `case stable(version: String)` and `case tip(commit: String?)` and then make `var url: URL?` a computed var (that could yield nothing). I'm not going to block merging on that but if you want to do some clean up..."
> — AboutView.swift, 2026-03-30

## Process blockers: rebase and lint

Two to four words, often with an emoji or exclamation. No judgment attached. Sometimes he does the rebase himself.

> "Rebase please :)"

> "Rebase your PRs please!"
> — 2026-03-30

> "Please rebase since the other merge. 😄"

> "This is going to require a rebase. Lots merged for the context menu configuration."

> "need a lint!"

> "Merged manually since for some reason can't push to your branch. Thank you!"

## Feedback intensity by area

- **Core Zig (src/Surface.zig, apprt/action.zig):** strictest. Layer boundaries, API naming, arena allocation. Blocking.
- **pty / C interop / build.zig:** concrete refactor guidance, merges working version, revisits later.
- **GTK (apprt/gtk):** Zig-over-GObject idioms, live config reload correctness. Delegates Wayland internals.
- **macOS / Swift:** light touch, tests by hand, one structural suggestion at most.
- **Shell integration / protocols:** verifies semantics with a question or demands a spec. Blocking on invented semantics.
- **CI / workflows:** job separation, rejects contributor-wide overhead.
- **Windows:** infra-only acceptance, complex logic goes to a fork.
- **Keyboard / encoding:** cautious, cites Chromium/Kitty, admits uncertainty.

## What he does not comment on

- Formatting or style beyond a single "need a lint!" when CI fails.
- Test structure or test coverage on small PRs. Relies on "tests well" and CI.
- Flaky CI he already knows about (dependabot dist jobs) or GitHub UI glitches.
- Basics. Assumes the contributor knows Zig, GTK, or Swift and points to existing code instead of explaining.
- Line-by-line nits. Comments cluster on architecture, ownership, duplication, and API shape.

## Coding Style (inferred from review demands; no authored code in sample)

No authored commits or diffs were present in the analyzed data. The following is what his reviews reveal he expects, which is the best available proxy for how he writes.

- **Naming:** unabbreviated identifiers (`command_line`, not `cmdline`). Naming is explicitly low-stakes ("bikeshed").
- **Memory:** arenas over piecemeal alloc/free. Do not add deinit to structs that do not need free-ability. Do not copy when the original can live on the shared arena.
- **Structure:** one C file with `#ifdef` guards rather than per-OS files. Shared enums and types live in one canonical location and are referenced, never copied. New public helpers go in dedicated files (allocator.zig). Plain Zig getters over GObject property machinery for primitives.
- **Comments:** explain why something unusual exists (commented-out code, non-obvious defaults). No section-marker comment styles. New config values get an "Available since: X.Y.Z" doc note.
- **Error handling and correctness:** derive values rather than hardcode them. Measure quotas instead of guessing. Handle live config reload by tearing down existing state (timers, progress bars).
- **Tests:** not requested explicitly. Expectation is that CI passes and the change "tests well" by hand.
- **Commit and process style:** rebase before review, keep CI jobs single-purpose, no contributor-wide hooks. Merges manually when tooling breaks and says so.

## Evolution over time

- **2023 to early 2026:** primarily layer-boundary enforcement, Zig memory guidance, and terse `!vouch` triage. Warm punctuation (":)", "😄").
- **March 2026:** sharper scope gating ("fork, not main"), explicit handling of AI-generated PRs, spec-first stance on protocol changes stated more forcefully, and a longer "not personal" script for hard rejections. `!unvouch` appears as a testing-driven revocation.
- **Late March 2026:** more explicit blocking/non-blocking signaling ("I'm not going to block merging on that"), milestones used openly as soak tests, and more informal register including unfiltered profanity at tooling.

The core priorities did not change. What sharpened is the speed of the gate decision and the explicitness of severity labels.

## Distinctive traits and verbal signatures

- **`!vouch` / `!unvouch`** as the default review verdict. Vouch is provisional.
- **"for now"** hedging on rejections and merges. Rarely a permanent no.
- **"Let it bake" / "let it sit"** with a milestone number attached.
- **Self-labeled severity:** "Bikeshed", "as a general statement", "on first pass", "I'm not going to block merging on that", "You don't have to but".
- **"We should…" / "We can just…"** for project convention. **"I'd…"** for personal preference.
- **"Can't possibly be right" / "Its all wrong" / "I don't understand"** when certain. **"I think"** and trailing ellipses when tentative.
- **Points at existing code** instead of explaining ("see other getters", "That's what apprt/embedded does").
- **Humor on must-fix items:** "Holy cow", "lmao", "Womp", "also lol."
- **Self-assigns small fixes:** "I'll take a look rn thats easy."
- **Profanity at tooling, never at people:** "Fuck. GitHub fucked up the merge. Anyways, this is merged despite this PR state." / "One day I'm going to debug why dependabot dist jobs fail. One day."
- **Typos left uncorrected:** "Its", "Thats", "cone" for "clone", "Thanks For both!", "rn".
- **Cross-links issues:** "I think this fixes #11319 too", "Replaced with #11208".
- **Mentions his own setup when useful:** Linux VM on Mac, VMware for OpenGL 4.3, "I am on mobile".
- **Brief thanks on merge:** "Thanks!", "Thank you!", "Yes this is fine. Thank you".
- **Length rule:** one to two sentences by default. Full paragraphs only for protocol-semantics arguments and for defusing interpersonal friction.
