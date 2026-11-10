# Calendar timezone implementation checkpoints

Branch: `fix/calendar-timezones`. Prerequisite: installed UI 0.3.3, retained by a merge from `fix/library-layout`.

## Scope and invariants

- Use the current Mac timezone consistently in Today, Calendar and Library; show original event time separately when different.
- Preserve explicit ICS event timezone; use subscription timezone only for floating times. Unknown or ambiguous input must not silently move a lecture.
- React to system timezone/clock/day changes without resetting navigation or forms. Recorder deadlines remain absolute instants and respect pause, disablement and Digiexam.
- Render midnight-spanning events and explain repeated hours at DST boundaries.
- Preserve recordings, transcripts, associations and user configuration. Do not change system timezone or start real recordings for tests.
- Deliver an installed 0.3.4 candidate with backup/rollback, verify actual launched app path, preserve microphone authorization.

## Checkpoints

- [x] C0: Clean tree inspected; dedicated topic branch created from develop with installed UI prerequisite merged.
- [x] C1: Consistent local display, live timezone/day updates and original-time detail; native regression checks.
- [x] C2: ICS timezone provenance, invalid timezone handling, recurrence/DST checks and stable event identity.
- [x] C3: Day-clipped event segments, DST presentation and recording deadline reconciliation checks.
- [x] C4: Relevant automated checks, public-tree audit, reviewed commits and draft PR into develop.
- [ ] C5: 0.3.4 build/install with private backup and rollback; real UI and launched-path verification.

## Baseline evidence

Before implementation, selected calendar/dashboard/workspace tests passed. Additional synthetic checks passed 12 UTC/TZID/floating conversions across UTC, Europe/Rome, Asia/Kolkata and America/New_York host timezones; recurring Rome 10:00 stayed 10:00 across the autumn DST transition; naive ambiguous/nonexistent Rome times were rejected.

Confirmed gaps: Today/Calendar use the first event timezone, library/detail use individual event zones, and week columns only render events starting that day. All-timed ICS ingestion stores the subscription timezone even for an explicit different event TZID.

## Resume procedure

Inspect `git status`, this checklist and the latest focused commits. Do not assume a build/install process stopped: check its live handle/log before restarting. Until C5 is verified, the installed app remains 0.3.3. Build and install helpers from previous updates are private temporary files and must be inspected before reuse. Keep installation receipts and personal paths outside this public document.

C1 evidence: release SwiftUI build passed; both workspace native tests passed, including India/New York day boundaries and original Rome time. Added system timezone/clock/day notification handling and a bounded 30-second visible-clock refresh. Real UI verification remains C5.

Additional C2 reproductions: a floating 19:00 Kolkata event inside the UTC subscription window was omitted before localization; an unknown explicit TZID was silently interpreted as Kolkata. Both require regression coverage.

C2 evidence: 83 focused ingestion/calendar tests passed. Floating DTSTART/DTEND/RECURRENCE-ID/RDATE/EXDATE are localized before recurrence expansion and period filtering, in both import modes. Explicit event TZID is preserved; unknown TZID and ambiguous/skipped local times are rejected/reported; existing invalid events remain reviewable with their identity retained.

C3 in progress: UI release build and workspace native checks passed for day-clipped spans and 23/25-hour days. `swift test --package-path capture` cannot run on this Command Line Tools installation because XCTest is unavailable; use `scripts/test_capture_native.py --scheduler-only`, now extended with the same clock-change assertions. No application update has been installed yet.

C3 evidence: final UI release build passed. Workspace native tests passed for split midnight spans, exclusive day-end boundaries, 23/25-hour grids and distinct positions for the repeated local hour. Standalone scheduler regressions passed after clock notifications were dispatched to the main actor; no microphone acquisition or system-clock changes were used. Equivalent XCTest assertions are retained for hosts with XCTest, but could not be run here. Full integration suite is running; C4/C5 remain incomplete.

C4 in progress: full pytest integration run exited successfully (no failures). Preparing 0.3.4 and an actionable localized INVALID_TIMEZONE explanation. The changed native dashboard case is being checked separately; no broad suite rerun is needed for the version bump/UI copy. New Capture code changes its ad-hoc signature, so C5 must explicitly verify microphone permission and must not assume authorization survived.

C4 evidence: all local integration/native/dashboard checks passed, public-tree audit and staged diff checks passed, universal release and signed UI app built. Isolated native UI preview verified Rome DST fallback (02 CEST and 02 CET), a spanning lesson on both month dates, and the same lesson at 06:00 India with original 02:30 CEST detail. Focused commits are pushed; draft PR: https://github.com/gabrizanni/mynotes/pull/6. C5 installation and its permission check remain outstanding.
