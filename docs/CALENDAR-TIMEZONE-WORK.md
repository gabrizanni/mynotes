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
- [ ] C2: ICS timezone provenance, invalid timezone handling, recurrence/DST checks and stable event identity.
- [ ] C3: Day-clipped event segments, DST presentation and recording deadline reconciliation checks.
- [ ] C4: Relevant automated checks, public-tree audit, reviewed commits and draft PR into develop.
- [ ] C5: 0.3.4 build/install with private backup and rollback; real UI and launched-path verification.

## Baseline evidence

Before implementation, selected calendar/dashboard/workspace tests passed. Additional synthetic checks passed 12 UTC/TZID/floating conversions across UTC, Europe/Rome, Asia/Kolkata and America/New_York host timezones; recurring Rome 10:00 stayed 10:00 across the autumn DST transition; naive ambiguous/nonexistent Rome times were rejected.

Confirmed gaps: Today/Calendar use the first event timezone, library/detail use individual event zones, and week columns only render events starting that day. All-timed ICS ingestion stores the subscription timezone even for an explicit different event TZID.

## Resume procedure

Inspect `git status`, this checklist and the latest focused commits. Do not assume a build/install process stopped: check its live handle/log before restarting. Until C5 is verified, the installed app remains 0.3.3. Build and install helpers from previous updates are private temporary files and must be inspected before reuse. Keep installation receipts and personal paths outside this public document.

C1 evidence: release SwiftUI build passed; both workspace native tests passed, including India/New York day boundaries and original Rome time. Added system timezone/clock/day notification handling and a bounded 30-second visible-clock refresh. Real UI verification remains C5.

Additional C2 reproductions: a floating 19:00 Kolkata event inside the UTC subscription window was omitted before localization; an unknown explicit TZID was silently interpreted as Kolkata. Both require regression coverage.
