export const courses = {
  title: "Courses",
  addCourse: "Add course",
  tabs: { lessons: "Lessons", notes: "Notes", style: "Style", profile: "Profile", files: "Files" },
  notesUpdated: (relative: string, lesson: string) => `Notes updated ${relative} · ${lesson}`,
  nextLesson: (when: string) => `Next lesson ${when}`,
  openInWord: "Open in Word",
  showInFolder: "Show in folder",
  history: "History",
  undoLast: "Undo last update",
};
