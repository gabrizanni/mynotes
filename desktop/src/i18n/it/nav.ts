import type { Messages } from "../en";

export const nav: Messages["nav"] = {
  mainLabel: "Sezioni",
  skipToContent: "Vai al contenuto",
  today: "Oggi",
  calendar: "Calendario",
  courses: "Corsi",
  library: "Raccolta",
  activity: "Attività",
  assistant: "Assistente AI",
  settings: "Impostazioni",
  recorderLabel: "Registratore",
  recorderNext: (course: string, time: string) => `Prossima: ${course} ${time}`,
  recorderLive: (elapsed: string) => `In registrazione · ${elapsed}`,
  recorderSaving: "Salvataggio audio…",
  recorderOff: "Registratore spento",
  recorderIdle: "Nessuna lezione in arrivo",
  recorderProblem: "Il registratore ti aspetta",
  shortcut: (key: string) => `Scorciatoia ${key}`,
  activityCount: (n: number) => `${n} da sistemare`,
};
