import type { Messages } from "../en";

export const capture: Messages["capture"] = {
  label: "Registratore",
  upcoming: (course: string, minutes: number) => `${course} tra ${minutes} min`,
  skip: "Salta",
  starting: "Avvio…",
  rec: "REC",
  stop: "Ferma",
  saving: "Salvataggio audio…",
  saved: (duration: string) => `Salvata · ${duration}`,
  transcribe: "Trascrivi",
  micBlocked: "Microfono bloccato",
  failed: "Registrazione non riuscita",
  fix: "Risolvi",
  details: "Dettagli",
  expand: "Mostra i dettagli del registratore",
};
