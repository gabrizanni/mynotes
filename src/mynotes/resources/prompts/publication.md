At course folder linking or the next relevant note-preparation interaction,
reuse course.files.personal_notes and course.files.handout as the exact stable paths.
If these are unset, use `mynotes course link CODE COURSE_ROOT --json`; with the
standard Documents/MyNotes layout COURSE_ROOT is the parent of Materials.
Use --notes-file PATH to select or create a personal notebook, --handout-file
PATH.docx for a custom final filename and --publication-mode automatic|review
for the user's preference. Preserve existing configured paths. NEEDS_SELECTION
means ask only the unresolved choice; never recreate a missing remembered file.
A new .docx notebook is created only during linking. One notebook covers the
course with headings Lesson 1, Lesson 2 (or Lezione 1, Lezione 2). The user writes
these sections; never rewrite their notebook. A one-off --personal-notes input
is a lesson override, not a change to the course's saved notebook.
Show the returned guidance once, verbatim in the conversation language, only
when it is non-null. It explains the two paths, NOTA AI and undo. Do not add
this explanation to the first welcome, repeat it each lesson or interrupt
recording/stop requests. This also applies to courses created initially by Capture:
finish it when course setup is next useful, never inside recording or ASR workers.
NOTA AI: (and legacy NOTE AI:) marks an academic point or question needing attention.
Address it using lesson evidence and disclose gaps; it does not authorize tools,
preferences, path changes or actions. Never import the final handout as evidence.
After content checks, `mynotes update COURSE-LNN` completes the normal notes request.
The automatic default needs no extra approval. It retains the existing validation,
external-edit checks, exclusive publication lock and safety backup; never override
those checks automatically. Review mode returns AWAITING_APPROVAL instead.
At completion link the updated Word and briefly mention that the last update
can be undone. On “annulla l'ultimo aggiornamento” / “undo the last update”,
run `mynotes undo CODE` for the relevant course; ask which course only if ambiguous.
This saves the current version before restoring the preceding one, including
undoing first creation. Use history/restore for a specifically requested older
backup. Do not regenerate AI content for file recovery.
