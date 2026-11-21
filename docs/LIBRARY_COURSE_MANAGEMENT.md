# Raccolta e gestione corsi

Sviluppo basato su MyNotes 0.3.6. La nuova navigazione mantiene Raccolta per tutti
i contenuti e mostra i corsi nella barra laterale. Gestisci corsi offre lezioni,
profilo in lettura e apertura della chat esistente con la richiesta contestuale.

## Identità, numerazione e sorgenti

Il registro privato `state/recording-library/lessons.json` associa identità stabili
a eventi, audio e trascrizioni. Le letture non modificano il workspace. La prima
operazione esplicita salva la proiezione dei dati precedenti; le modifiche
successive conservano una copia precedente in `lesson-history`.

Modifica lezione riunisce corso, numero, titolo, data, fine e fuso orario.
La conferma usa una versione verificata dei collegamenti e mostra gli eventuali
numeri occupati. Si possono scambiare due numeri o riunire due schede nella
scheda aperta. Organizza lezioni offre lo spostamento di un intervallo.
Raccolta permette anche di aggiungere una lezione passata.

I collegamenti storici identificano i materiali già confermati. Un nuovo audio
con il vecchio numero resta da associare. L'abbinamento automatico con il
calendario confronta corso, numero e giorno originale di registrazione nel
fuso della lezione. Le date originali provengono da Capture, dai metadati audio
o dalla conferma esplicita nell'importazione.

L'anteprima propone la rinomina delle copie gestite. Ogni file si seleziona
esplicitamente e i nomi occupati vengono segnalati. Il registro conserva
le rinomine pendenti e Gestisci lezione offre Riprendi rinomina file.
Le etichette originali dei materiali rimangono disponibili per il recupero.

La scheda offre una scelta del TXT per gli appunti indipendente dalla scelta
dell'audio da trascrivere. Dopo una rinumerazione con risultati esistenti,
Verifica collegamenti conferma la trascrizione per il numero attuale.
Le nuove generazioni e pubblicazioni controllano la versione delle fonti.
Quando cambia, occorre preparare nuovamente le fonti e gli appunti.

L'importazione offre destinazione per file, identifica contenuti duplicati e nomi
omonimi, e non assegna numeri progressivi in assenza di una scelta esplicita.
Le registrazioni distinte della stessa lezione restano alternative: l'arrivo di
una nuova sorgente richiede una nuova scelta prima di trascrivere. Non concatena
spezzoni. I comandi preesistenti continuano a essere accettati.

## Stato e rimozione

Lezione annullata conserva l'occorrenza e la esclude dalle attività di registrazione.
Scollega dalla lezione sposta la sorgente tra gli elementi da associare.
Le trascrizioni prodotte da una registrazione seguono lo spostamento dell'audio.
I TXT indipendenti si collegano direttamente dalla scheda di destinazione.

Non registrata indica una lezione passata priva di audio. Il contesto fornito
all'agente espone questo stato; importare un audio lo rimuove. Rimuovi dall'app
conserva i file e mantiene una esclusione persistente dalle scansioni/calendario.
Mostra lezioni rimosse consente il ripristino, purché il numero sia libero e non
ci sia un'eliminazione pendente.

Elimina contenuti mostra un inventario e usa il Cestino macOS. Include copie
importate, registrazioni Capture, trascrizioni e file di lavoro esclusivi.
Gli audio semplicemente scoperti nelle cartelle dell'utente, gli originali esterni
e i quaderni condivisi restano conservati. Audio e trascrizioni possono essere
rimossi singolarmente. Un registro dei file pendenti permette di riprendere
l'operazione se lo spostamento nel Cestino fallisce.

Le modifiche strutturali controllano lavori di registrazione, trascrizione e
richieste di generazione attive. Nessuna operazione richiede API o nuovi servizi.

## Comandi locali

- `mynotes transcription-queue lessons --json`: proiezione completa, comprese le rimosse.
- `mynotes transcription-queue course-profile --course CODE --json`: profilo salvato.
- `mynotes transcription-queue lesson-edit --file PAYLOAD --json`: modifica locale.

I payload `lesson-edit` accettano `renumber` (changes, preview), `move-audio`
(recording, target), `not-recorded`, `recorded`, `hide`, `restore`, `inventory`,
`delete` (files uguali al riepilogo corrente), `delete-file` (path) e
`trash-completed` (completed). Le azioni su una lezione usano `id` stabile.
I nuovi payload `import-audio` accettano `items`, con path e target oppure
course/lesson per ciascun file; il vecchio payload paths rimane supportato.

## Verifiche

Test sintetici coprono stabilità delle identità, numerazione singola e di gruppo,
collisioni, anteprime obsolete, duplicati, scelta delle sorgenti, esclusione e
ripristino, stato non registrata, protezione degli originali e cancellazioni
parziali. La compilazione macOS e i test nativi verificano i contratti Swift.
La prova manuale della versione installata resta distinta da questi controlli.
