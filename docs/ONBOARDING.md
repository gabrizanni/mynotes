# First use / Primo utilizzo

## Setup, introduction, then one course

The native app prepares the computer. Choose the conversation language and an agent; the model menu can keep that agent's default or select a specific model. The same saved choice applies to the next chat and future note generation. Returning to the default removes the MyNotes model override. Changing it does not change a conversation that is already running. The setup resumes completed work and lets you return later to repair components or choose another agent.

Codex model choices come from its local cached catalog, with hidden entries excluded. Without that catalog, keep the default or enter a model identifier. Claude Code uses its documented aliases; access still depends on the user's account. Agy keeps a saved choice or accepts a custom identifier. The menu does not make paid requests or certify model availability. [Claude model aliases](https://code.claude.com/docs/en/model-config).

At the first empty conversation, MyNotes introduces itself in three short lines and asks one question about your background. It waits for the answer before asking for courses or lessons. You may skip this question. Your answer is saved in your private workspace and is not asked again. Existing courses and profiles are retained.

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

Then connect recordings and materials, or use the proposed `~/Documents/MyNotes` folders. Dragging a file into Terminal inserts its path; submit the message after checking it. Choose whether to configure Capture now or later.

Private settings, SQLite state, candidates and temporary files live under MyNotes' Application Support workspace. User-facing recordings, materials and handouts use Documents or locations you choose. Connecting an existing folder does not move its contents. Defaults use `standard` quality and the packaged study-handout style. Style edits affect future candidates, not published handouts.

## Add Capture when ready

Say: **“Here is my calendar; set up the recordings.”** Provide a calendar document, plain text or an HTTPS ICS subscription. The agent uses the existing calendar planning tools, asks for missing course/period/timezone information, and shows the proposed lessons and conflicts before applying them. Confirm both the timetable and recorder activation.

Allow the microphone, make a short test, play it, and confirm that you hear the expected sound. A valid file proves file production; it does not prove sound quality without listening. Capture starts disabled and can be skipped. It records with the chat closed, but requires an awake Mac and an open lid. [Capture behavior and controls](CAPTURE.md).

Say in Italian: **“Ecco il calendario, configura le registrazioni.”** L’agente mostra il piano prima di applicarlo; conferma pianificazione, attivazione e riascolto della prova.

## Produce the first Word

Say: **“Prepare notes for lecture one from this audio and these slides.”** The agent runs preparation, generation and Word build, checks the candidate, and shows a preview. Approve it before publication into the course handout. Missing slides or personal notes are visible inputs, not invented sources. A provider error leaves a retry action; a failed candidate does not become a completed publication.

Say in Italian: **“Prepara gli appunti della prima lezione usando questo audio e queste slide.”** Approva l’anteprima prima dell’inserimento nella dispensa.

## Come back

Open MyNotes and select **Open chat**, or run `mynotes`. Use `mynotes codex`, `mynotes agy` or `mynotes claude` to select an agent explicitly. Saved model preferences apply to that agent. In a conversation, ask to change the model; the agent can save it with `mynotes preferences --provider PROVIDER --model MODEL`. The new choice applies to the next chat and future notes. `mynotes setup` reopens setup and `mynotes doctor --json` gives actionable component checks. These checks do not prove listening, model quality or completed classroom recording.

[Installation](INSTALL.md) · [Installazione](INSTALL.it.md) · [Privacy](PRIVACY.md)
