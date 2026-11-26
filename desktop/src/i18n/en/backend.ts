/** The window shown when MyNotes' backend cannot run, and when this window itself fails. */
export const backend = {
  failure: {
    title: "MyNotes stopped working",
    crashLoop: (count: number) =>
      `The part of MyNotes that does the work stopped ${count} times in a row. Try again; if it keeps stopping, copy the details for whoever helps you.`,
    mismatch: "This app and the installed MyNotes do not match. Update MyNotes, then try again.",
    other: "MyNotes could not start the part that does the work. Try again; if it fails again, copy the details for whoever helps you.",
    detailsLabel: "Last messages from MyNotes",
    noDetails: "MyNotes wrote no message before it stopped.",
    retry: "Try again",
    copied: "Details copied",
    copyFailed: "Copying was blocked: select the details and copy them by hand.",
  },
  crash: {
    title: "This window ran into a problem",
    body: "Recordings and notes are not affected. Reload the window; if it happens again, copy the details.",
    reload: "Reload window",
  },
};
