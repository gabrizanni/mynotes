# Installa MyNotes + Capture

Il setup DMG richiede **Apple Silicon e macOS 14+**. Include Capture. Il candidato di sviluppo non è ancora una release GitHub pubblicata; gli ZIP precedenti usano l’installer manuale descritto sotto.

## Dal DMG alla chat

1. Scarica il DMG desiderato da [GitHub Releases](https://github.com/gabrizanni/mynotes/releases), quando sarà pubblicato.
2. Aprilo, trascina **MyNotes** in **Applicazioni** e apri **MyNotes** da lì.
3. Segui la preparazione dei componenti, scegli l’agente e premi **Apri la chat**.

Il setup prepara Python, pacchetti bloccati, trascrizione locale e strumenti Word nelle cartelle gestite da MyNotes. Non servono prima Python, Homebrew, Xcode o un agente ereditato da un altro progetto. La preparazione richiede internet e mostra l’avanzamento. Se chiudi l’app, i passaggi completati restano salvati; dopo un problema di rete o spazio puoi riprovare.

Il launcher viene installato in `~/.local/bin` e il setup aggiunge un blocco PATH riconoscibile alla configurazione zsh. Per usare `mynotes` da un’altra cartella, apri un nuovo Terminale.

## Apri comunque

Il candidato ha una firma ad-hoc, senza account Apple Developer e senza notarizzazione. Se macOS lo blocca, prova ad aprirlo una volta, poi vai in **Impostazioni di Sistema → Privacy e sicurezza → Apri comunque** e conferma **Apri** per MyNotes. La procedura crea un’eccezione per quell’app. [Guida Apple](https://support.apple.com/it-it/102445).

![Schema di Apri comunque](images/open-anyway.it.svg)

L’illustrazione è uno schema, non uno screenshot. Le etichette dipendono da lingua e versione macOS. Un Mac gestito può limitare l’eccezione: segui le indicazioni del suo amministratore. Se manca il pulsante o compare “app danneggiata”, conserva il messaggio esatto e reinstalla un candidato verificato: non disattivare le protezioni di sistema.

## Scegli l’agente AI

Serve **l’agente da Terminale**, non soltanto la sua app desktop.

**Claude Code:** l’account Free non include il servizio, nemmeno con Haiku. Serve un piano abilitato o accesso API con crediti. Installare la CLI e controllarne versione e comandi non richiede una generazione a pagamento. Il controllo di login non verifica il saldo o il diritto a usare un modello. [Piani ufficiali](https://claude.com/pricing), [crediti API](https://support.claude.com/en/articles/8977456-how-do-i-pay-for-my-claude-api-usage).

| Agente | Guida ufficiale | Dopo l’installazione |
| --- | --- | --- |
| Codex | [Codex CLI](https://developers.openai.com/codex/cli/) | Accedi tramite Codex, poi premi **Ricontrolla** |
| Agy | [Antigravity CLI](https://antigravity.google/docs/cli/) | Completa il suo accesso, poi premi **Ricontrolla** |
| Claude Code | [Setup Claude Code](https://code.claude.com/docs/en/setup) | Accedi tramite Claude Code, poi premi **Ricontrolla** |

Se manca un agente, il setup invita a installarlo; non installa provider, crea account o raccoglie password. Un launcher rilevato non equivale ad accesso verificato: se la verifica non è conclusiva, apri l’agente e accedi, poi riprova. Versioni incompatibili mostrano l’azione di aggiornamento. Modello e ragionamento usano i valori predefiniti del provider salvo tua scelta esplicita; MyNotes non cambia provider di nascosto.

[Prima conversazione e Capture](ONBOARDING.md) · [Privacy](PRIVACY.md)

## Ripara e controlla

Riapri MyNotes oppure usa `mynotes setup`. `mynotes doctor --json` mostra separatamente installazione, agente, trascrizione, Word e Capture con le prossime azioni. Capture può restare disattivato mentre usi registrazioni esistenti. Se hai negato il microfono, abilita **MyNotes Capture** in **Impostazioni di Sistema → Privacy e sicurezza → Microfono**, poi ripeti prova e riascolto.

Dopo un aggiornamento interrotto, riprova il setup: il runtime precedente resta selezionato fino al superamento delle verifiche della sostituzione. [Recupero e rollback](UPDATES.md).

## Collega la cartella del corso

Dopo aver configurato il primo corso, dai all’agente la cartella o l’alias Finder
e chiedi di collegarlo a quel corso. L’agente può usare un solo comando e il
risultato restituito: non serve cercare in tutto il disco o ripetere help e
controlli per questa operazione. File e alias rimangono al loro posto.

```sh
mynotes course link COURSE "PATH" --json
```

Sostituisci COURSE con il codice del corso e PATH con il percorso; trascinare la
cartella nel Terminale aiuta a inserire correttamente gli spazi. MyNotes controlla
solo i metadati della cartella e delle sottocartelle immediate, fino a 1.000
voci. L’anteprima mostra fino a 50 file per categoria e il conteggio delle voci
effettivamente esaminate; un elenco abbreviato è distinto da un inventario
incompleto. Collega appunti e registrazioni quando la scelta è chiara. Se il risultato
è ambiguo o incompleto, lo mostra senza salvare: usa `--notes "NOTES_PATH"` e
`--recordings "AUDIO_PATH"` oppure scegli una cartella più specifica.
`--dry-run` mostra un’anteprima. Questo inventario non legge né sposta i contenuti;
risolvere un alias non apre finestre o monta volumi. Per un alias rotto, ripristina
la destinazione o scegli la cartella originale.

Per controllare i tentativi registrati di una lezione, chiedilo all’agente oppure
usa `mynotes usage COURSE-L01 --json`. I contatori mancanti sono sconosciuti, non
zero; errori e riuso locale sono distinti. Il rapporto è in sola lettura, non
chiama l’AI e non è una fattura o un controllo del saldo. Queste funzioni hanno
superato prove sul pacchetto 0.2.4 installato in cartelle isolate. Il candidato
0.2.5 aggiunge la lettura degli appunti Word autonomi; il suo pacchetto verrà
verificato separatamente. [Verifiche](VALIDATION.md) · [Ottimizzazione](OPTIMIZATION.md).

## Appunti Word normali e primo avvio da zero

Nel sorgente 0.2.5, un file Word di appunti selezionato per una lezione non deve
avere titoli speciali: testo e tabelle vengono inclusi. Una dispensa cumulativa
con titoli di lezione riconosciuti continua a fornire la sezione richiesta. La
verifica del pacchetto resta separata. Le istruzioni scritte negli appunti sono
fonti non fidate; salva esplicitamente una preferenza in MyNotes per usarla come
istruzione.

Per ripartire con MyNotes sullo stesso account, usa un backup reversibile e una
reinstallazione limitata a MyNotes. Conserva originali dei corsi, altre app,
agenti AI e credenziali. Registra l’esito di setup, collegamento del corso e
strumenti locali dopo la prova. Gli agenti già installati possono restare: è
una prova con MyNotes nuovo, non con un account macOS vuoto. La prova autorizzata
è in preparazione e non è ancora dichiarata completata. [Verifiche](VALIDATION.md).

## Installer ZIP manuale (avanzato / release precedenti)

Da uno ZIP standalone estratto puoi eseguire `zsh scripts/install-mynotes.sh`. Questo percorso richiede macOS, Python 3.12+ e `uv` già disponibili e non offre il setup dei componenti del DMG. Opzioni: `--prefix PERCORSO`, `--app-home PERCORSO`, `--python PERCORSO`, `--offline`, `--dry-run`, `--list-versions` e `--rollback VERSIONE`. Offline richiede pacchetti bloccati già in cache. Da un clone sorgente usa invece `uv sync --locked`: il clone non è un archivio standalone.
