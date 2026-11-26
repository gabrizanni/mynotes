const lessonWord = (lessons: string) => (/[,–-]/.test(lessons) ? `lessons ${lessons}` : `lesson ${lessons}`);

export const assistant = {
  title: "AI assistant",
  close: "Close assistant",
  newConversation: "New conversation",
  context: (page: string) => `Context: ${page}`,
  suggestions: "Try asking",
  prompts: [
    "Prepare notes for lesson 3",
    "Make this course's notes more concise",
    "From now on show every calculation step",
    "Quiz me on lessons 1–4",
    "Set up recordings from my calendar",
    "Undo the last update",
  ],
  composerLabel: "Message",
  composerPlaceholder: "Ask about your courses…",
  send: "Send",
  stop: "Stop",
  conversationLabel: "Conversation",
  you: "You",
  working: "Working…",
  stopped: "Stopped. What was written so far is kept.",
  restoring: "Opening your last conversation…",
  codeLabel: "Code",
  tool: {
    show: "Show command",
    hide: "Hide command",
    status: { running: "Running", completed: "Done", failed: "Failed", denied: "Not allowed in the panel" },
    notes: (running: boolean, course: string, lessons: string | null) =>
      `${running ? "Preparing" : "Prepared"} notes for ${course}${lessons ? ` ${lessonWord(lessons)}` : ""}`,
    transcribe: (running: boolean, course: string, lesson: string) => `${running ? "Transcribing" : "Transcribed"} ${course} lesson ${lesson}`,
    instructions: (running: boolean): string => (running ? "Loading MyNotes instructions" : "Loaded MyNotes instructions"),
    check: (running: boolean): string => (running ? "Checking MyNotes" : "Checked MyNotes"),
    undo: (running: boolean, course: string) => `${running ? "Undoing" : "Undid"} the last update of ${course}`,
    calendar: (running: boolean): string => (running ? "Syncing calendars" : "Synced calendars"),
    read: (running: boolean, file: string) => `${running ? "Reading" : "Read"} ${file}`,
  },
  duration: {
    seconds: (count: number) => `${count} s`,
    minutes: (count: number) => `${count} min`,
  },
  errors: {
    quota: (name: string) => `${name}'s usage limit is reached. Try again when it resets.`,
    auth: (name: string) => `You're signed out of ${name}.`,
    network: "The connection dropped before the reply finished.",
    timeout: "The reply took too long and was stopped.",
    unavailable: (name: string) => `${name} isn't installed on this computer.`,
    interrupted: "MyNotes closed during this reply.",
    busy: "Another reply is still running. Wait for it or stop it.",
    other: "The assistant couldn't finish this reply.",
  },
  fixes: { retry: "Try again", signIn: "Sign in", settings: "Open Settings" },
  footer: (assistantName: string, company: string) => `Messages go to ${assistantName} (${company}).`,
  handoff: {
    /** Generate flow, PERSONAL_CONTENT_CONFLICT (UX-SPEC §3): the prefilled message. */
    personalContentConflict: (course: string, lesson: number) =>
      `My edits in the ${course} course notes overlap the new Lesson ${lesson}, so nothing was changed. Help me merge the new lesson without losing my edits.`,
  },
};
