export const assistant = {
  title: "AI assistant",
  close: "Close assistant",
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
  unavailable: "The in-app chat arrives with the next backend update.",
  footer: (assistantName: string, company: string) => `Messages go to ${assistantName} (${company}).`,
};
