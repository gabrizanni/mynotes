import type { Messages } from "../en";

const lessonWord = (lessons: string) => (/[,–-]/.test(lessons) ? `lezioni ${lessons}` : `lezione ${lessons}`);

export const assistant: Messages["assistant"] = {
  title: "Assistente AI",
  close: "Chiudi l'assistente",
  newConversation: "Nuova conversazione",
  context: (page: string) => `Contesto: ${page}`,
  suggestions: "Prova a chiedere",
  prompts: [
    "Prepara gli appunti della lezione 3",
    "Rendi più concisi gli appunti di questo corso",
    "D'ora in poi mostra ogni passaggio dei calcoli",
    "Interrogami sulle lezioni 1–4",
    "Imposta le registrazioni dal mio calendario",
    "Annulla l'ultimo aggiornamento",
  ],
  composerLabel: "Messaggio",
  composerPlaceholder: "Chiedi qualcosa sui tuoi corsi…",
  send: "Invia",
  stop: "Ferma",
  conversationLabel: "Conversazione",
  you: "Tu",
  working: "Al lavoro…",
  stopped: "Fermato. Quello che era già scritto resta.",
  restoring: "Apro l'ultima conversazione…",
  codeLabel: "Codice",
  tool: {
    show: "Mostra il comando",
    hide: "Nascondi il comando",
    status: { running: "In corso", completed: "Fatto", failed: "Non riuscito", denied: "Non consentito nel pannello" },
    notes: (running: boolean, course: string, lessons: string | null) =>
      `${running ? "Preparo gli appunti" : "Appunti preparati"} per ${course}${lessons ? `, ${lessonWord(lessons)}` : ""}`,
    transcribe: (running: boolean, course: string, lesson: string) => `${running ? "Trascrivo" : "Trascritta"} ${course}, lezione ${lesson}`,
    instructions: (running: boolean) => (running ? "Carico le istruzioni di MyNotes" : "Istruzioni di MyNotes caricate"),
    check: (running: boolean) => (running ? "Controllo MyNotes" : "MyNotes controllato"),
    undo: (running: boolean, course: string) => `${running ? "Annullo" : "Annullato"} l'ultimo aggiornamento di ${course}`,
    calendar: (running: boolean) => (running ? "Sincronizzo i calendari" : "Calendari sincronizzati"),
    read: (running: boolean, file: string) => `${running ? "Leggo" : "Letto"} ${file}`,
  },
  duration: {
    seconds: (count: number) => `${count} s`,
    minutes: (count: number) => `${count} min`,
  },
  errors: {
    quota: (name: string) => `Hai raggiunto il limite di utilizzo di ${name}. Riprova quando si azzera.`,
    auth: (name: string) => `Non sei più connesso a ${name}.`,
    network: "La connessione si è interrotta prima della fine della risposta.",
    timeout: "La risposta ci metteva troppo ed è stata fermata.",
    unavailable: (name: string) => `${name} non è installato su questo computer.`,
    interrupted: "MyNotes si è chiuso durante questa risposta.",
    busy: "Un'altra risposta è ancora in corso. Aspetta o fermala.",
    other: "L'assistente non è riuscito a finire questa risposta.",
  },
  fixes: { retry: "Riprova", signIn: "Accedi", settings: "Apri le Impostazioni" },
  footer: (assistantName: string, company: string) => `I messaggi vanno a ${assistantName} (${company}).`,
  handoff: {
    personalContentConflict: (course: string, lesson: number) =>
      `Le mie modifiche agli appunti del corso ${course} si sovrappongono alla nuova Lezione ${lesson}, quindi non è cambiato niente. Aiutami a unire la nuova lezione senza perdere le mie modifiche.`,
  },
};
