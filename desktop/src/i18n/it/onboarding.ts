import type { Messages } from "../en";

export const onboarding: Messages["onboarding"] = {
  segments: { setup: "Configurazione", course: "Il tuo corso", recording: "Registrazione" },
  steps: {
    language: "Le tue lezioni, trasformate in appunti.",
    assistant: "Scegli il tuo assistente AI",
    "sign-in": "Accedi al tuo assistente AI",
    privacy: "Cosa resta sul tuo computer",
    studies: "Cosa studi?",
    course: "Aggiungi il tuo primo corso",
    style: "Come vuoi i tuoi appunti?",
    files: "Dove sono i file del corso?",
    schedule: "Aggiungi l'orario delle lezioni",
    microphone: "Fai ascoltare le lezioni a MyNotes",
    automatic: "Registrare in automatico le lezioni in programma?",
    "first-notes": "Crea i tuoi primi appunti",
    ready: "Tutto pronto.",
  },
  intro: "Registra, trascrivi su questo computer e ottieni un Word per corso che cresce lezione dopo lezione.",
  getStarted: "Inizia",
  settingUp: (percent: number) => `Configurazione · ${percent}%`,
};
