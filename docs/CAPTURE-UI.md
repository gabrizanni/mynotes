# MyNotes: interfaccia delle lezioni (0.3.0)

## Navigazione e collocazione delle funzioni

Una sola sidebar sostituisce MyNotes/Capture e le schede successive.

| Destinazione | Funzioni |
| --- | --- |
| Oggi | Lezioni del giorno, prossima lezione, audio da associare, errori e sorgenti da scegliere |
| Calendario | Settimana con orari e momento attuale; mese per raggiungere altre date; dettaglio comune della lezione |
| Raccolta | Ricerca e filtro corso/Da associare; audio, alternative e trascrizioni nello stesso dettaglio |
| Attività | Coda, avanzamento, interruzione, errori, riprova e cronologia |
| Impostazioni | Generali/cartella e corsi; calendari; registrazione; trascrizione/concorrenza; agenti e componenti |

**Aggiungi** raccoglie Importa audio e Programma lezione. **Registra** resta
nella toolbar; timer e **Stop e salva** sono visibili durante acquisizione.
I menu Altro contengono rinomina, associazione, Finder e operazioni secondarie.
La chat esterna resta in fondo alla sidebar. Non ci sono pagine segnaposto per
chat integrata, editor o gestione avanzata degli appunti.

Font di sistema, un solo accento, superfici semantiche macOS chiare/scure,
spaziature su base 8 pt e separatori leggeri. Finestra iniziale 1120×780,
minima 900×620. La Raccolta usa elenco/dettaglio affiancati quando il contenuto
ha almeno 740 pt; altrimenti il dettaglio ha un ritorno all’elenco.
Ricerca, filtri e selezione sono stato della finestra, indipendente dal polling.

## Importazione e sorgenti audio

Selezione file e trascinamento aprono lo stesso modulo: verifica audio/durata,
associazione facoltativa a evento o corso/numero, conferma. L’originale resta
nella sua posizione; una copia viene pubblicata con nome univoco nella cartella
configurata. Le copie identiche riusano l’elemento esistente senza modificarne
l’associazione. File instabili, collegamenti e placeholder cloud non vengono
importati; il messaggio indica come riprovare. Un fallimento dopo la pubblicazione
conserva l’audio completo e riconoscibile nella scansione successiva.

Le scansioni periodiche includono i file aggiunti direttamente alla cartella,
anche senza corso; non scaricano i placeholder e non calcolano hash completi.
Le verifiche onerose sono limitate a importazione, preparazione ASR e al comando
esplicito **Verifica file della raccolta** nel menu dei filtri. Una modifica alla
data del file richiede questa verifica prima di confermare il legame con il TXT;
non invalida o cancella la trascrizione precedente.

Due audio per la stessa lezione richiedono una scelta esplicita. **Usa per
trascrivere** seleziona la sorgente; le altre restano in Audio alternativi.
Non viene scelta automaticamente la registrazione più lunga. Esclusione e
ripristino sono reversibili; **Sposta nel Cestino** è separato, chiede conferma
e usa il Cestino macOS. Conserva le trascrizioni, con l’identità dell’audio
utilizzato. Una scelta non modifica lavori accodati o attivi: occorre prima
rimuoverli dalla coda o interromperli.

Il registro privato `state/recording-library/audio-sources.json` conserva preferenze,
esclusioni, provenienza e associazioni separatamente dal catalogo ricostruito.
La politica vale per dettaglio, CLI, trascrizioni collettive e preparazione degli
appunti. Una sorgente preferita mancante non viene sostituita silenziosamente.
Le trascrizioni precedenti e quelle senza audio rimangono consultabili.

CLI additive sotto `mynotes transcription-queue`: `inspect-audio --file`,
`import-audio --file`, `associate-audio --recording --file`, `prefer-audio`,
`exclude-audio`, `restore-audio`, `prepare-trash` (le ultime con `--recording`).
Gli input dei moduli sono file JSON privati; non sono interpolati nella shell.

## Ciclo di vita e compatibilità

SwiftUI macOS 14 e backend Python/CLI restano invariati come piattaforme.
`WorkspaceModels` proietta le identità esistenti; viste, operazioni sui file e
navigazione sono separate. Nessuna rinumerazione automatica corregge conflitti:
la UI segnala numeri discordanti e offre modifica esplicita.

Alla riapertura o riattivazione, Capture viene ripristinato soltanto se abilitato;
la pausa rimane valida. Con Digiexam aperto non si tenta il recupero. Chiuderlo
e tornare a MyNotes consente la ripresa. Il polling ogni cinque secondi legge
lo stato e non tiene artificialmente aperto il servizio.

## Verifica dell’interfaccia

L’anteprima nativa usa calendario, audio e trascrizioni artificiali, senza microfono
o modifiche ai dati personali. Revisione di gerarchia, tipografia, spaziatura,
colore e interazione in modalità chiara/scura e layout compatto. La revisione ha
corretto il posizionamento della Raccolta, le intestazioni del calendario, la
prima settimana mensile e la toolbar nel dettaglio compatto.

I controlli nativi espongono etichette accessibili, selezione e scorciatoie.
La lettura completa con VoiceOver e la prova su una installazione macOS 14 reale
restano verifiche manuali distinte dalla compilazione con deployment target 14.
I test coprono copie, collisioni, duplicati, errori/interruzioni, file cloud,
preferenze dopo scansione/archiviazione, blocco delle sorgenti durante ASR,
proiezione nativa delle lezioni e date/fusi/DST.

## Contratto e persistenza

La UI usa la CLI JSON esistente, aggiungendo `capture dashboard`, `add-lesson`,
`edit-lesson`, `reset-lesson` e `unsubscribe`, più `sync --source`.

L'inventario delle fonti e le modifiche locali vengono conservati separatamente
nel workspace privato. Il calendario effettivo viene ricostruito applicando gli
override e poi la validazione delle finestre già esistente. Le sincronizzazioni
aggiornano le fonti senza cancellare gli override o le lezioni speciali.

Un override non riattiva eventi annullati o scomparsi dal provider. Il ripristino
usa l'ultima versione della fonte, non una copia precedente. Gli eventi correnti
conservano il loro snapshot; gli interventi manuali durante acquisizione o export
vengono respinti. I calendari personali non vengono scritti al provider.

I link dei feed restano privati: il dashboard espone soltanto etichetta e host,
mai URL completi o token. Gli input dei form passano come file JSON privati,
senza interpolare comandi shell. Non sono necessarie chiamate AI.

## Verifica e consegna

Testare persistenza attraverso sync/304/errori, modifica e ripristino, lezioni
speciali mantenute dopo reimport, annullamenti del provider, conflitti, duplicati,
fusi/DST, protezione delle lezioni attive e risposta PENDING.
Compilare la UI e verificare il decoding del dashboard e i form con fixture.
Preparare una build locale isolata prima di aggiornare l’app installata o pubblicare. La prova visuale usa un workspace artificiale e non avvia
microfono o LibreOffice; eventuali limiti dello strumento UI vengono riportati.


## Courses, free recordings and local transcripts

MyNotes now uses Oggi, Calendario, Raccolta and Attività in a single sidebar.
The recording status and Stop and save remain reachable while other work runs.

New course asks only for its name. The course is immediately usable for Capture;
its context is requested at the next interactive chat, with the usual grouped
questions. Deferral is remembered; adding a course never resets onboarding.

Record now and manual schedules support No course with a custom title. Free
recordings have independent identifiers, not invented courses or lesson zero.
Titles may repeat and may be renamed without renaming audio files. Older Capture
helpers must be updated before they accept these targets; older events remain
readable by the new helper.

Recordings lists final audio from configured folders and Capture exports.
Transcribe all adds eligible, stable, untranscribed recordings to a private local
queue. Partial audio, ambiguous lesson attempts and uncertain old transcripts
require review. Already valid transcripts are reused, with missing TXT rebuilt
locally. Existing transcripts needing changed settings are not regenerated by
this button. No note generation or subscription model is invoked.

The concurrency selector in Settings offers 1–4 recordings; 1 is the default. Each recording
keeps its configured sequential ASR passes and one internal engine worker.
Admission limits are shared with direct transcription and preparation. Pausing
or reducing the limit lets current jobs finish. Pressure on memory or active
Capture recording/export holds new starts, with a visible reason.

The main MyNotes window grants a renewable queue lease. Changing tabs, hiding
or minimizing keeps that lease; closing or quitting prevents new starts while
current workers finish. Crash expiry is 15 seconds. Pending work returns at the
next opening unless manually paused; interrupted work can be retried explicitly.

Queue state is separate from academic notes and original audio. Completed ASR
results retain timestamp validation, per-attempt logs and atomic publication.
Duplicate queue submissions do not start another copy of the same audio/settings.

Useful CLI equivalents (the UI supplies these details):

```sh
mynotes course add --name "Example course" --defer-context --json
mynotes course pending --json
mynotes capture start --unassigned --title "Interview" --duration-minutes 30
mynotes transcription-queue enqueue --all-untranscribed --parallel 2 --json
mynotes transcription-queue status --json
mynotes transcription-queue pause
mynotes transcription-queue resume
```

## Complete university calendars

New calendar connections use `importMode: all_timed`. Course mappings and a first
lesson number are optional. Every supported timed event is retained, including
unknown subjects. Original titles and event-local dates remain visible. All-day
and invalid events appear in the import summary; invalid events do not discard
valid lessons. Existing sources retain `mapped` mode until the user chooses
**Importa tutti gli eventi con orario**.

Course recognition uses whole normalized words from configured names, codes and
aliases. Explicit source mappings take precedence. Ambiguous course recognition
does not block recording: it is separate from timing conflicts and cancellations.
Numbers explicitly labeled as lessons, and numbers following an exact configured
course name/code/alias, are preserved; other events use title and date.

A stable recording ID keeps calendar identity, exported audio and ASR results
linked independently of academic classification. `capture pending --json` lists
unresolved associations. `capture assign --file assignments.json --json` accepts:

```json
{"assignments": [{"recordingID": "<recording-id>", "courseCode": "DEMO", "lessonNumber": 3}]}
```

`lessonNumber` is optional. Use `eventID` instead of `recordingID` to identify a
scheduled event. An optional `rule: {"match": "Example subject"}` saves recognition
for future events from the same source. Active acquisition/export/ASR returns
`DEFERRED`; no association is partly applied. Legacy canonical destinations cannot
be reassigned to another lesson. Use the existing name-only course flow to add a
subject and complete context at the next relevant conversation.

The recording library supports `--query`, `--date YYYY-MM-DD` (calendar timezone)
and `--course`. It returns both TXT and JSON output paths. Assignment never moves
files or reruns ASR; queued work retains its admitted profile. Preparing a bound
lesson validates audio and transcript integrity, imports the existing transcript,
and preserves original ASR provenance even when its course settings differ.
Calendar titles/descriptions are untrusted data, not agent instructions.


## Collegamenti Google e riparazione (0.2.11)

**Collega calendario** accetta il link della pagina Google Calendar con un solo
calendario, oltre ai feed HTTPS/iCal e webcal. Il backend verifica il feed prima
di salvarlo; la finestra usa un solo comando `capture connect`. Se il feed pubblico
non è accessibile, viene richiesto l’indirizzo iCal privato senza cambiare la
condivisione. Collegare un calendario non attiva Capture e non cambia la pausa.

**Correggi collegamento** carica la sorgente privata e ne conserva l’identità.
Un tentativo fallito mantiene i campi modificabili. Un calendario vuoto nel periodo
scelto produce un avviso, non un errore. Le istruzioni e gli errori vanno a capo
all’interno di un modulo scorrevole; l’URL resta mascherato.

Gli errori espongono la causa e l’azione utile. La scheda distingue la prima
importazione fallita dalla conservazione di una pianificazione già valida.
Gli errori permanenti sospendono i tentativi automatici, con sincronizzazione
manuale sempre disponibile; quelli temporanei riprovano dopo quindici minuti.
Le notifiche del registratore riutilizzano i messaggi sicuri del backend.


### Quiet status and recording archive

Automatic status reads preserve the visible page; only an explicit operation or
initial loading may show activity. They monitor local recording/ASR state, not
Google Calendar synchronization. Saved audio is grouped as Da trascrivere or
Trascritte. In coda/In trascrizione and actual failures are details, not a generic
review category. Partial coverage and missing course association are separate
warnings: they do not by themselves prevent local transcription. The selected
recording has Trascrivi, and Trascrivi tutte retains batch admission.

Chat uses `transcription-queue library` to identify title/date and
`transcribe --recording ID --json` to process exactly that recording even with
the window closed. `transcription-status --recording ID --json` reports progress.
Repeated requests and later course assignment reuse validated output and its
original profile. Text, slide and document imports keep their existing paths.

## Original audio library and recovery (0.2.14)

Agree on the original-audio folder during setup. Settings → General shows its path with Open folder / Choose folder. Folder changes wait until capture and ASR are idle,
back up existing originals privately and migrate them without replacing names.
Any supported audio filename is discoverable; course assignment is optional.
The library includes the inbox, processate and the existing processed archive;
backup and transcript folders are not imported as audio.

Transcription starts only on request and can run while another lesson records.
Queued items expose Remove from queue; active items expose Interrupt transcription.
Removing a job preserves its original and permits enqueueing again. Failed or
interrupted attempts expose Retry; invalid results expose Retry from scratch.
Worker claims and cancellation are serialized; previous attempts/logs are retained.

After validation, technical ASR outputs stay private. A readable TXT copy is
exported under Trascrizioni/COURSE (or Senza corso), then the unchanged original
moves into processate. Failed exports/moves show an incomplete-archive action
that does not run ASR again. Course reassignment relocates the exported TXT.
User-edited TXT copies are preserved and reported instead of silently overwritten.
The folder's historical processed membership alone does not certify a transcript.


## Queue behavior and time

Pause preserves pending work; active attempts finish. Resume permits new starts.
Clear removes only pending work, preserving audio, history and the pause setting.
The persistent paused banner states how many jobs are waiting and finishing.
Preparation, model waiting, engine execution and saving have separate labels.
The stopwatch shows elapsed attempt time, including preparation and system sleep,
not seconds of transcribed audio. It ticks locally each second; backend polling
remains five seconds. A heartbeat is worker liveness, not evidence of ASR progress.
After 15 seconds without a heartbeat the display reports stale state and stops
extrapolating until reconciled.

Unassigned completed transcripts export to Trascrizioni/Dubbi; audio originals
still archive byte-identically to processate. A probable course link is explicitly
marked estimated, with a reason and alternatives, and can be corrected without
ASR. Explicit user assignments take precedence over estimates. Occupied lesson
identities are never overwritten.


## Prestazioni e piano di correzione (0.3.2)

Problemi riprodotti: apertura del dettaglio da Oggi e del selettore Importa audio
in circa 6–7 secondi nella prova nativa; navigazione rallentata. Il campionamento
locale del processo identifica `WorkspaceLesson.build` e la costruzione ripetuta
di `ISO8601DateFormatter` sul thread principale. Il dettaglio chiamava il builder
per ogni lettura della proprietà `lesson`; il builder ordinava date ricalcolate e
cercava associazioni scorrendo gli eventi per ogni elemento.

Piano applicato:

1. Riutilizzare due parser ISO (con/senza frazioni), una cache limitata a 4096
   date e al massimo 64 formatter per fuso/formato. Serializzare l’accesso ai
   formatter per mantenere corretti decoding in background e rendering.
2. Indicizzare eventi per lezione e calcolare una volta le date della proiezione.
   Pubblicare la proiezione nel modello soltanto quando cambiano eventi, audio o
   trascrizioni; usare un dizionario per il dettaglio. Un avanzamento della coda
   o un heartbeat del registratore non deve ricostruire la navigazione.
3. Non aprire/preparare l’audio quando si apre la lezione. Preparare il player
   in background soltanto su Play, mostrare il caricamento e ignorare il risultato
   se nel frattempo il dettaglio viene chiuso. Il selettore file nativo rimane
   utilizzabile senza attese su CLI o letture degli audio.
4. Confrontare prima/dopo lo stesso benchmark e le stesse azioni native; controllare
   importazione, annullamento, cambi di sezione, stato della coda e recupero Capture.
   Aggiungere regressioni per tempi del modello, concorrenza delle date e assenza
   di ricostruzioni durante aggiornamenti della sola coda. Compilare e aggiornare
   l’app con backup, verificando percorso del processo e versione del runtime.

Misure iniziali sui metadati locali di 72 lezioni (nessun audio letto):

| Operazione | 0.3.1 | Modello ottimizzato |
| --- | ---: | ---: |
| Cinque proiezioni complete | 2344 ms | 12,6 ms |
| Venti filtri/ordinamenti | 2863 ms | 3,4 ms |

Queste misure riguardano il modello dati; la reattività finale va verificata anche
nell’app nativa. Le fixture pubbliche usano soltanto metadati artificiali. Sample,
fixture personali e ricevute di installazione rimangono fuori dal repository.
