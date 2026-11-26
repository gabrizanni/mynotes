import type { Messages } from "../en";

export const backend: Messages["backend"] = {
  failure: {
    title: "MyNotes si è fermato",
    crashLoop: (count: number) =>
      `La parte di MyNotes che fa il lavoro si è fermata ${count} volte di seguito. Riprova; se continua a fermarsi, copia i dettagli per chi ti aiuta.`,
    mismatch: "Questa app e il MyNotes installato non corrispondono. Aggiorna MyNotes, poi riprova.",
    other: "MyNotes non è riuscito ad avviare la parte che fa il lavoro. Riprova; se non funziona, copia i dettagli per chi ti aiuta.",
    detailsLabel: "Ultimi messaggi di MyNotes",
    noDetails: "MyNotes non ha scritto nessun messaggio prima di fermarsi.",
    retry: "Riprova",
    copied: "Dettagli copiati",
    copyFailed: "La copia è stata bloccata: seleziona i dettagli e copiali a mano.",
  },
};
