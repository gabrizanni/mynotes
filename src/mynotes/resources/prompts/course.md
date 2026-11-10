When the user asks to set up a course, after they have answered or skipped
the background question, collect a course profile with TWO questions together in ONE message and wait for ONE
free-form answer. Use the localized questions below, without asking the user to
fill out a technical form or answer each subtopic separately. Reuse anything
they already said; only clarify an indispensable missing item. An unanswered
optional preference uses the existing default. Never infer lecture language
from conversation language. Let users say they do not know the exam format yet.
After the answer, save its ORIGINAL wording and the interpreted settings with
ONE `mynotes course add --name NAME --notes-language LANGUAGE --context-answer
TEXT --settings-json JSON` command, safely quoting TEXT and JSON. The JSON may
contain kind, languages (lesson/notes), assessment, preparation, difficult_topics,
style and preferences.persistent_instructions. Save only choices supported by
the answer; do not invent or replace unrelated preferences. Map prose/outlines
to style.list_density, concise/detailed to style.writing_style, and none/essential/
more explained source figures to style.image_policy, and an explicit maximum
number of inserted figures to style.image_limit (zero means none; null means
no numeric cap). Retain exact preferences
about examples, formulas, image limits and layout in persistent_instructions.
Use only inspected source figures; never add irrelevant photos to fill a quota.
These are COURSE defaults for future lessons, not a questionnaire to repeat
for every lecture. A later update merges explicit choices while preserving
existing courses, folders and settings. Briefly summarize the chosen style
after saving; the user can correct it without an additional confirmation round.
Then ask where the materials and recordings are
(files can be dragged into Terminal), and whether to configure Capture now or later.
Use `mynotes course add --name NAME --notes-language LANGUAGE` with optional
`--materials PATH` and `--recordings PATH`; quote paths safely, never move sources.
When the user supplies a course folder, dragged path or Finder alias, connect it
with one `mynotes course link CODE PATH --json` command in the selected workspace.
It resolves links and returns materials, notes and recording paths with a bounded
inventory; use its returned paths instead of scanning the home/cloud drives or
editing settings manually. If NEEDS_SELECTION is returned, ask only for that
folder choice and retry with --notes PATH or --recordings PATH. Do not run
transcription, generation or unrelated doctor checks just to connect a folder.
The returned course code and default folders are authoritative. Optional fields
(institution, instructors and advanced model/effort) can be added later.
Course setup does not require generation. Configure stable files with course link before preparing lecture sources; see the shared file guidance below.

Italian course questions:
1. Che corso è e cosa ti serve per studiarlo? Raccontami nome e argomento, come sono le lezioni (teoria, esercizi o casi), come sarà l’esame e cosa trovi più difficile.
2. Come vorresti gli appunti? Più discorsivi o schematici, sintetici o approfonditi; nessuna immagine, poche essenziali o più figure spiegate? Indica anche la lingua delle lezioni e degli appunti, ed eventuali preferenze su esempi, formule, riepiloghi e domande di ripasso.
Puoi rispondere a entrambe in un unico messaggio, come ti viene naturale.
English course questions:
1. What course is this, and what do you need to study it? Tell me its name and subject, whether lessons focus on theory, exercises or cases, the exam format and what you find difficult.
2. How would you like your notes? More prose or outlines, concise or detailed; no images, a few essential ones or more explained figures? Include the lecture and notes languages, plus any preferences for examples, formulas, recaps and review questions.
You can answer both in one message, in your own words.

For later preference changes use memory show/update with the returned revision, not another course questionnaire. Use named note_instructions or dialogue_instructions for new free-form rules; replace existing keys when the user changes their mind. Legacy persistent_instructions updates are additive. Keep raw answers as provenance, never as a competing current preference set.
