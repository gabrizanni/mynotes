import type { Messages } from "../en";

export const assistant: Messages["assistant"] = {
  title: "Assistente AI",
  close: "Chiudi l'assistente",
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
  unavailable: "La chat nell'app arriva con il prossimo aggiornamento del backend.",
  footer: (assistantName: string, company: string) => `I messaggi vanno a ${assistantName} (${company}).`,
};
