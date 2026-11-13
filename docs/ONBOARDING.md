# First use / Primo utilizzo

## Setup, introduction, then one course

The native app prepares the computer. Choose the conversation language and an agent; the model menu can keep that agent's default or select a specific model. The same saved choice applies to the next chat and future note generation. Returning to the default removes the MyNotes model override. Changing it does not change a conversation that is already running. The setup resumes completed work and lets you return later to repair components or choose another agent.

Codex model choices come from its local cached catalog, with hidden entries excluded. Without that catalog, keep the default or enter a model identifier. Claude Code uses its documented aliases; access still depends on the user's account. Agy keeps a saved choice or accepts a custom identifier. The menu does not make paid requests or certify model availability. [Claude model aliases](https://code.claude.com/docs/en/model-config).

At the first empty conversation, MyNotes introduces itself in three short lines and asks one question about your background. It waits for the answer before asking for courses or lessons. You may skip this question. Your answer is saved in your private workspace and is not asked again. Existing courses and profiles are retained. This is the default pacing: saved dialogue preferences can customize it.

The Italian first welcome is:

> Benvenuto in MyNotes: ti aiuto a trasformare registrazioni, slide e appunti in materiale di studio ordinato e documenti Word.<br>
> Puoi anche organizzare i corsi e configurare le registrazioni delle lezioni con Capture.<br>
> Per iniziare, raccontami brevemente cosa studi, qual è il tuo background e come vorresti usare MyNotes.

There is no video and no long presentation. The English welcome follows the same structure. The agent saves the original answer with `mynotes profile --background TEXT`, or acknowledges a skip with `mynotes profile --skip`. Ordinary preference saves are local atomic configuration edits.

After the background answer, the agent asks two course questions together and waits for one free-form answer:

1. What course is this, and what do you need to study it? Include its name and subject, theory/exercises/cases, exam format and difficult topics.
2. How would you like your notes? Include prose/outlines, concise/detailed explanations, no images/a few essential images/more explained figures, lecture and notes languages, and any preferences for examples, formulas, recaps and review questions.

In Italian:

> **1. Che corso è e cosa ti serve per studiarlo?** Raccontami nome e argomento, come sono le lezioni (teoria, esercizi o casi), come sarà l’esame e cosa trovi più difficile.
>
> **2. Come vorresti gli appunti?** Più discorsivi o schematici, sintetici o approfonditi; nessuna immagine, poche essenziali o più figure spiegate? Indica anche la lingua delle lezioni e degli appunti, ed eventuali preferenze su esempi, formule, riepiloghi e domande di ripasso.
>
> Puoi rispondere a entrambe in un unico messaggio, come ti viene naturale.

Answer naturally; optional details can be omitted. The agent reuses facts already supplied and clarifies only indispensable missing information. It saves the original answer plus course-specific settings in one local command, then briefly summarizes the style so you can correct it. Examples and detailed image preferences remain in persistent course instructions. Image choices concern available, inspected source figures; an image limit is not a target to fill with unrelated photos. These settings apply to future lessons of that course and can be changed later without repeating the questionnaire.

Then connect recordings and materials, or use the proposed `~/Documents/MyNotes` folders. MyNotes creates or connects one stable personal notebook per course and remembers the exact notebook and final handout paths. Dragging a file into Terminal inserts its path; submit the message after checking it. Choose whether to configure Capture now or later.

Course creation/linking saves the supplied source folders. Each new chat receives
a small source-path index, and activity context rereads it from current settings.
The current request's explicit path takes precedence; saved paths are search hints,
not a claim that files exist or match a lesson. Transcript exports and explicit
sync/assignment refresh the course transcript folder, including observed moves.
Changing a recording-library folder updates the conventional transcript paths in
the index automatically. No source directory is scanned just to build this index.
An external move is learned when a normal sync observes it or the user reconnects
the folder; there is no background search of the computer for missing files.

Private settings, SQLite state, candidates and temporary files live under MyNotes' Application Support workspace. User-facing recordings, materials and handouts use Documents or locations you choose. Connecting an existing folder does not move its contents. Defaults use `standard` quality and the packaged study-handout style. Style edits affect future candidates, not published handouts.

After linking, the agent explains the two file paths once. Defaults are `Appunti personali.docx` and `CODE_COURSE_NOTES.docx`; both can be customized. Separate your notebook with headings `Lesson 1`, `Lesson 2` (or `Lezione 1`, `Lezione 2`). Write `NOTA AI: clarify this step` to highlight an academic question for the model; the legacy `NOTE AI:` spelling also works. These annotations do not change app settings or authorize actions. A missing remembered file requires selecting its new location, never silent replacement.

In Italian: **«NOTA AI: chiarisci questo passaggio»** richiama l’attenzione sul contenuto della lezione. Il file personale resta tuo; il modello aggiorna la dispensa separata. Questa spiegazione appare quando si collegano i file, anche per i corsi creati da Capture, senza interrompere le registrazioni.

## Add Capture when ready

Say: **“Here is my calendar; set up the recordings.”** Provide a calendar document, plain text or an HTTPS ICS subscription. The agent uses the existing calendar planning tools, asks for missing course/period/timezone information, and shows the proposed lessons and conflicts before applying them. Confirm both the timetable and recorder activation.

Allow the microphone, make a short test, play it, and confirm that you hear the expected sound. A valid file proves file production; it does not prove sound quality without listening. Capture starts disabled and can be skipped. It records with the chat closed, but requires an awake Mac and an open lid. [Capture behavior and controls](CAPTURE.md).

Say in Italian: **“Ecco il calendario, configura le registrazioni.”** L’agente mostra il piano prima di applicarlo; conferma pianificazione, attivazione e riascolto della prova.

## Produce the first Word

Say: **“Prepare notes for lecture one from this audio and these slides.”** The agent prepares sources, generates and checks the content, then uses `update` to build, render-validate and update the same final handout automatically with a safety backup. Ask for preview-first mode if you prefer approval before publication. Missing slides or personal notes are visible inputs, not invented sources. A provider error leaves a retry action; a failed candidate does not become a completed publication.

Say in Italian: **“Prepara gli appunti della prima lezione usando questo audio e queste slide.”** Il Word definitivo viene aggiornato dopo i controlli, con backup di sicurezza. Puoi chiedere «annulla l’ultimo aggiornamento».

## Come back

### Memory updated through the conversation

Say **“From now on, show every calculation step for this course”** or
**“Ask me one question at a time.”** The agent saves a preference only when its
persistence and scope are clear, then briefly reports the change. If either is
ambiguous it asks. **“Only for this lesson”** stays temporary and does not change
future defaults. General preferences are inherited; an explicit course override
wins for that course. Changing your mind replaces the corresponding named rule,
rather than accumulating contradictory instructions.

All memory remains in private `config/settings.json`, with atomic updates and
configuration history. Markdown is a readable view, not another editable store.
Original background/course answers remain available as provenance, but current
preferences take precedence. Agent and skill integration files are technical,
managed files; the conversation never needs to rewrite them to remember you.
App settings do not yet expose a memory editor or reader.

The local command interface is available to the agent and advanced users:

```sh
mynotes memory show --scope global --area dialogue --json
# Use the revision returned by show, not a remembered older value:
mynotes memory update --scope global --area dialogue --expected-revision REV --set questions "Ask one question at a time."
mynotes memory show --scope course --course COURSE --area notes --json
mynotes memory update --scope course --course COURSE --area notes --expected-revision REV --style-json '{"writing_style":"detailed","image_limit":3}'
```

Use `--area notes|dialogue` and an explicit `--scope global|course`. `--set KEY
TEXT` adds or replaces a named instruction; `--remove KEY` forgets an own rule.
A course can reuse a general rule's key to override it; empty text suppresses
that inherited rule for the course. Removing the course override restores
inheritance. `--inherit FIELD` removes a structured course style override.
`memory reset` clears the selected area's own rules and, for notes, resets its
style choices. It requires the same revision check as update. A stale revision
returns `MEMORY_CONFLICT` without writing: reread and reconcile the edit. Legacy
free-form preferences are shown with `legacy-…` keys and remain replaceable and
removable. A partial legacy course update adds instructions without discarding
unmentioned ones; use memory operations to replace or remove them explicitly.

For each new operational task the coordinator loads one local context:

```sh
mynotes context --activity notes --course COURSE
mynotes context --activity preferences --course COURSE --json
```

To list sources in a folder you supplied, say “find the recordings,
transcripts and slides in this folder.” The agent can use `mynotes inventory
PATH` immediately, without creating a course. This is a metadata-only inventory
of the supplied folder and its immediate children; it reports file counts and
truncation, and lists TXT files among notes because a filename alone cannot prove
whether one is a transcript. It does not validate audio, associate lessons or
read file contents. A deeper folder must be inspected separately. A later request
to prepare notes still performs the appropriate source and freshness checks.

Activities are `profile`, `course`, `notes`, `study`, `capture`, `transcription`,
`publication`, `recovery`, and `preferences`. Each returns the relevant guidance
and fresh inherited/own/effective memory. It invokes no model and does not scan
audio/transcript libraries. Greetings require no context command. Actual tasks
retain their source, integrity and publication checks. Existing courses load no
questionnaire at startup, and empty launches no longer enumerate Capture items.
When using the exported skill directly in a provider without the MyNotes
launcher/agent, add `--include-core` to the first context command to load the
common coordination policy once. Later context calls need only their activity.

Open MyNotes and select **Open chat**, or run `mynotes`. Use `mynotes codex`, `mynotes agy` or `mynotes claude` to select an agent explicitly. Saved model preferences apply to that agent. In a conversation, ask to change the model; the agent can save it with `mynotes preferences --provider PROVIDER --model MODEL`. The new choice applies to the next chat and future notes. `mynotes setup` reopens setup and `mynotes doctor --json` gives actionable component checks. These checks do not prove listening, model quality or completed classroom recording.

[Installation](INSTALL.md) · [Installazione](INSTALL.it.md) · [Privacy](PRIVACY.md)
