import type { Messages } from "../en";

export const courses: Messages["courses"] = {
  title: "Corsi",
  addCourse: "Aggiungi corso",
  tabs: { lessons: "Lezioni", notes: "Appunti", style: "Stile", profile: "Profilo", files: "File" },
  notesUpdated: (relative: string, lesson: string) => `Appunti aggiornati ${relative} · ${lesson}`,
  nextLesson: (when: string) => `Prossima lezione ${when}`,
  openInWord: "Apri in Word",
  showInFolder: "Mostra nella cartella",
  history: "Cronologia",
  undoLast: "Annulla l'ultimo aggiornamento",
};
