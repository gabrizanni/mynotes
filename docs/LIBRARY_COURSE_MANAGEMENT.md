# Raccolta e gestione corsi

Sviluppo basato su MyNotes 0.3.6. La nuova navigazione mantiene Raccolta per tutti
i contenuti e mostra i corsi nella barra laterale. Gestisci corsi offre lezioni,
profilo in lettura e apertura della chat esistente con la richiesta contestuale.

## Identità, numerazione e sorgenti

Il registro privato `state/recording-library/lessons.json` associa identità stabili
a eventi, audio e trascrizioni. Le letture non modificano il workspace. La prima
operazione esplicita salva la proiezione dei dati precedenti; le modifiche
successive conservano una copia precedente in `lesson-history`.

Il numero si modifica con tastiera o stepper, con verifica prima della conferma.
Lo spostamento di gruppo conserva i buchi e controlla collisioni e anteprime
obsolete. Gli originali non sono rinominati; le etichette originali dei materiali
restano utilizzabili. I risultati già generati non vengono reinterpretati sotto
un altro numero: la generazione resta bloccata se la rinumerazione interessa
cartelle di lavoro esistenti. Ripristinare il numero originale consente di
riprendere; una migrazione dei documenti pubblicati non fa parte di questa modifica.

L'importazione offre destinazione per file, identifica contenuti duplicati e nomi
omonimi, e non assegna numeri progressivi in assenza di una scelta esplicita.
Le registrazioni distinte della stessa lezione restano alternative: l'arrivo di
una nuova sorgente richiede una nuova scelta prima di trascrivere. Non concatena
spezzoni. I comandi preesistenti continuano a essere accettati.

## Stato e rimozione

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
