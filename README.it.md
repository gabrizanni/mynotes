# MyNotes

> Assistente AI agentico local-first e motore transazionale di pubblicazione Word per macOS.

[![Tests](https://img.shields.io/badge/tests-80%20passed-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)]()
[![Platform](https://img.shields.io/badge/platform-macOS%20(Apple%20Silicon)-black.svg)]()
[![Package Manager](https://img.shields.io/badge/built%20with-uv-purple.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

Guida in italiano | English documentation: [README.md](README.md)

---

## Panoramica

Trasformare le lezioni universitarie in dispense di studio complete e rigorose è un processo complesso: richiede di combinare ore di registrazioni audio, slide dense (PDF/PPTX), appunti personali e formule matematiche articolate.

I software convenzionali spesso inventano citazioni (allucinazioni), degradano le formule matematiche in immagini sgranate, inviano l'audio privato su server cloud terzi o sovrascrivono inavvertitamente le modifiche manuali degli studenti.

**MyNotes** risolve queste criticità con una pipeline rigorosa, orientata alla privacy e nativa per macOS. Il tool elabora input multimodali, allinea le evidenze empiriche, orchestra il ragionamento multi-agente con contratti JSON verificabili e pubblica dispense in formato Microsoft Word contenenti equazioni vettoriali native e modificabili.

---

## Architettura

```mermaid
flowchart TD
    subgraph Ingestion ["1. Acquisizione Multimodale"]
        Audio["Audio Lezione (.m4a, .wav)"] --> ASR["WhisperKit CoreML / FluidAudio<br>(Apple Neural Engine)"]
        Slides["Slide e Materiali (.pdf, .pptx)"] --> Extractor["Estrattore Documenti (pypdfium2 / pptx)"]
        Notes["Appunti Personali (.docx, .md)"] --> Extractor
    end

    subgraph Evidence ["2. Validazione Evidenze"]
        ASR --> SourcePack["Evidence Source Pack<br>(JSON Schema con Timestamp)"]
        Extractor --> SourcePack
    end

    subgraph Agents ["3. Ragionamento Multi-Agente"]
        SourcePack --> Tier["Pipeline Agenti<br>(Stesura → Verifica → Revisione → Editing)"]
        Tier --> Candidate["Candidato JSON Validato<br>(Schema e Vincoli Evidenze)"]
    end

    subgraph Publishing ["4. Pubblicazione Transazionale"]
        Candidate --> OMML["Motore Matematico OOXML / OMML<br>(LaTeX → Formule Word Native)"]
        OMML --> RenderAudit["Verifica Headless LibreOffice e PDF"]
        RenderAudit --> Publisher["Pubblicazione Two-Phase Commit<br>(Lock fcntl e Hash SHA-256)"]
        Publisher --> Word["Dispensa Word Cumulativa (.docx)"]
    end
```

---

## Pilastri Ingegneristici

### 1. Edge AI e Riconoscimento Vocale On-Device (Apple Silicon)
- Basato su **WhisperKit** in esecuzione diretta sull'**Apple Neural Engine (CoreML)** e **FluidAudio**.
- Nessun dato inviato al cloud: l'audio e le trascrizioni rimangono esclusivamente sul tuo computer.
- Supporto per vocabolario personalizzato del corso, normalizzazione con `afconvert` di macOS e timestamp a livello di singola parola.

### 2. Pipeline di Ragionamento Multi-Agente
- Integrazione diretta con i runtime locali (Codex, Antigravity) tramite contratti JSON Schema convalidati.
- **Quattro livelli di qualità selezionabili:**
  - `base`: sintesi a singolo passaggio con contesto mirato.
  - `standard`: sintesi approfondita del contesto della lezione.
  - `approfondito`: bozza estesa con revisione mirata tra agenti.
  - `revisione`: flusso completo comprendente stesura, audit formale delle citazioni e revisione editoriale.
- Ogni affermazione generata mappa a specifici identificatori di evidenza (`T001` per audio, `S005` per slide, `H002` per dispense).

### 3. Motore Matematico OOXML Nativo (da LaTeX a OMML)
- Implementa un parser dedicato che converte le espressioni LaTeX direttamente in linguaggio **Office Math Markup Language (`m:oMath`)**.
- Produce formule vettoriali native, nitide e modificabili direttamente con l'editor di equazioni di Microsoft Word, evitando l'uso di immagini statiche.

### 4. Pubblicazione Transazionale Two-Phase Commit
- Gestione della concorrenza tramite file locking POSIX consultivo (`fcntl`).
- Calcolo degli hash di integrità crittografica SHA-256 prima e dopo la modifica per rilevare eventuali modifiche esterne, preservando le note manuali scritte dallo studente al di fuori dei blocchi di lezione.
- Giornale delle transazioni e rollback automatico in caso di interruzioni impreviste.

---

## Guida Rapida per Sviluppatori

Clonare e verificare il progetto richiede meno di un minuto utilizzando [uv](https://docs.astral.sh/uv/):

```sh
# 1. Clona il repository
git clone https://github.com/gabrizanni/mynotes.git
cd mynotes

# 2. Sincronizza le dipendenze in <3 secondi
uv sync

# 3. Esegui la suite di test completa
uv run pytest

# 4. Esegui il test end-to-end della pipeline Word
uv run python scripts/validate_word_pipeline.py /tmp/mynotes-demo
```

---

## Flusso dei Comandi CLI

MyNotes separa rigorosamente il codice dell'applicazione dallo spazio di lavoro privato dell'utente. Tutti i file, le registrazioni e i documenti Word risiedono in una cartella esterna dedicata.

```sh
# 1. Inizializza lo spazio di lavoro privato
mynotes configure --workspace "~/Documents/MyNotes-Workspace"

# 2. Elabora i materiali e crea il source pack per la lezione
mynotes prepare CS101-L01 \
  --workspace "~/Documents/MyNotes-Workspace" \
  --material "~/Courses/CS101/slides.pdf" \
  --personal-notes "~/Courses/CS101/notes.docx"

# Riusa una trascrizione normalizzata già disponibile, senza ripetere l'ASR;
# puoi aggiungere --audio per mantenere il riferimento alla registrazione.
mynotes prepare CS101-L01 --workspace "~/Documents/MyNotes-Workspace" \
  --transcript "~/Courses/CS101/transcript.json"

# Se i nomi dei file sono ambigui, imposta course.lesson_sources["1"] nella
# configurazione privata: {"materials": ["/percorso/slides.pptx"],
# "personal_notes": "/percorso/notes.docx"}. Le fonti esatte prevalgono
# sulla ricerca automatica; sono supportati anche .ppt e .xlsx.

# 3. Genera la bozza con il provider configurato (Codex o Antigravity)
mynotes generate CS101-L01 --workspace "~/Documents/MyNotes-Workspace" --provider agy --quality standard

# 4. Compila e valida la bozza in formato Word
mynotes build CS101-L01 --workspace "~/Documents/MyNotes-Workspace"

# 5. Pubblica atomicamente nella dispensa cumulativa del corso
mynotes publish CS101-L01 --workspace "~/Documents/MyNotes-Workspace"

# Ispeziona lo storico delle pubblicazioni e lo stato
mynotes history CS101 --workspace "~/Documents/MyNotes-Workspace"
mynotes status --workspace "~/Documents/MyNotes-Workspace"
```

### Stile delle dispense

MyNotes include uno stile di studio codificato: elenco numerato degli argomenti
all'inizio della lezione, paragrafi esplicativi, veri elenchi Word, termini tecnici
selezionati in arancione, immagini motivate dalle fonti, domande con risposte,
riepilogo in italiano e numeri di pagina continui. L'impaginazione usa un
modello Word neutro incluso nell'applicazione: non serve fornire una dispensa
personale come riferimento. Il disegno usa Aptos a 11 pt per il testo, Aptos
Display per i titoli di 16/14/12 pt, termini chiave arancioni in grassetto e
didascalie grigie in corsivo.

Il primo setup chiede le preferenze per scrittura, elenchi, immagini, riepilogo e
domande. Ogni corso eredita tali scelte, ma può modificarle separatamente:

I margini sono 1,5 cm su tutti i lati. Titolo della lezione, paragrafo e sottoparagrafo usano gli stili Word nativi Titolo 1, Titolo 2 e Titolo 3. I termini arancioni hanno lo stile carattere visibile `Termine tecnico`, modificabile insieme in Word. I livelli JSON 3/4 del provider restano invariati e vengono tradotti in Titolo 2/3.

```sh
mynotes style show --workspace "~/Documents/MyNotes-Workspace"
mynotes style edit --workspace "~/Documents/MyNotes-Workspace" --course CS101
mynotes style preview --workspace "~/Documents/MyNotes-Workspace" --course CS101
```

Le modifiche valgono per i candidati successivi e non riscrivono le dispense già pubblicate.

---

## Installazione Utente Finale

Per installare MyNotes come applicazione autonoma senza configurare l'ambiente di sviluppo, scarica l'archivio compilato da [GitHub Releases](https://github.com/gabrizanni/mynotes/releases):

```sh
zsh scripts/install-mynotes.sh
mynotes doctor
```

Lo script verifica l'ambiente, Python 3.12+ e installa i file dell'applicazione in `~/Library/Application Support/MyNotes/app/` con il launcher in `~/.local/bin/`.

---

## Sicurezza e Sovranità dei Dati

- **Architettura Local-First:** Tutti i materiali di studio, le registrazioni audio e i documenti Word rimangono sempre all'interno dello spazio di lavoro locale.
- **Autenticazione Provider:** I provider AI esterni sfruttano i propri flussi di autenticazione CLI. MyNotes non richiede né memorizza token o chiavi API nei propri file di configurazione.
- **Licenze di Terze Parti:** Consulta [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) per i dettagli sulle librerie open source utilizzate.

---

## Licenza e Autore

- **Licenza:** [Licenza MIT](LICENSE)
- **Autore:** Sviluppato da [Gabriele Zanni](https://github.com/gabrizanni).
