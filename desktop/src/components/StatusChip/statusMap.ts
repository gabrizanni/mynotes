/**
 * Backend status → chip (UX-SPEC §4 chip table). One label and one tone per status; unknown
 * statuses fall back to a neutral "Status unknown" instead of leaking a code.
 */
import {
  AlertTriangle,
  AudioLines,
  CalendarClock,
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CirclePause,
  CircleX,
  Clock,
  Download,
  FileCheck,
  FilePen,
  FileText,
  Hand,
  KeyRound,
  ListChecks,
  LoaderCircle,
  MicOff,
  Split,
  Tag,
  type LucideIcon,
} from "lucide-react";

import type { Messages } from "../../i18n";

export type ChipTone = "info" | "success" | "warning" | "danger" | "neutral" | "live";
export type ChipKind = "notes" | "transcription" | "recording" | "assistant";

export interface ChipSpec {
  tone: ChipTone;
  icon: LucideIcon;
  label: (t: Messages["status"]) => string;
  /** Animated (spinner) while something is running; still under reduced motion. */
  busy?: boolean;
}

const notes: Record<string, ChipSpec> = {
  CHECKING: { tone: "info", icon: ListChecks, label: (t) => t.notes.checking, busy: true },
  GENERATING: { tone: "info", icon: FilePen, label: (t) => t.notes.writing, busy: true },
  BUILDING_WORD: { tone: "info", icon: FileText, label: (t) => t.notes.formatting, busy: true },
  UPDATING_WORD: { tone: "info", icon: FileText, label: (t) => t.notes.updating, busy: true },
  COMPLETED: { tone: "success", icon: FileCheck, label: (t) => t.notes.ready },
  AWAITING_APPROVAL: { tone: "warning", icon: FilePen, label: (t) => t.notes.toReview },
  COMPLETED_WITH_WARNINGS: { tone: "warning", icon: AlertTriangle, label: (t) => t.notes.readyCheck },
  PAUSED_QUOTA: { tone: "warning", icon: CirclePause, label: (t) => t.notes.paused },
  PAUSED_AUTH: { tone: "warning", icon: CirclePause, label: (t) => t.notes.paused },
  PAUSED_NETWORK: { tone: "warning", icon: CirclePause, label: (t) => t.notes.paused },
  PAUSED_SLEEP: { tone: "warning", icon: CirclePause, label: (t) => t.notes.paused },
  NEEDS_TRANSCRIPT: { tone: "warning", icon: FileText, label: (t) => t.notes.needsTranscript },
  PERSONAL_CONTENT_CONFLICT: { tone: "danger", icon: Hand, label: (t) => t.notes.needsYou },
  NEEDS_REVIEW: { tone: "danger", icon: Hand, label: (t) => t.notes.needsYou },
  STOPPED: { tone: "danger", icon: Hand, label: (t) => t.notes.needsYou },
  NONE: { tone: "neutral", icon: CircleDashed, label: (t) => t.notes.none },
};

const transcription: Record<string, ChipSpec> = {
  NOT_STARTED: { tone: "neutral", icon: CircleDashed, label: (t) => t.transcription.notStarted },
  QUEUED: { tone: "neutral", icon: Clock, label: (t) => t.transcription.queued },
  RUNNING: { tone: "info", icon: AudioLines, label: (t) => t.transcription.running, busy: true },
  CANCELLING: { tone: "neutral", icon: LoaderCircle, label: (t) => t.transcription.stopping, busy: true },
  COMPLETED: { tone: "success", icon: CircleCheck, label: (t) => t.transcription.done },
  FAILED: { tone: "danger", icon: AlertTriangle, label: (t) => t.transcription.toCheck },
  INTERRUPTED: { tone: "warning", icon: AlertTriangle, label: (t) => t.transcription.toCheck },
  NEEDS_REVIEW: { tone: "warning", icon: AlertTriangle, label: (t) => t.transcription.toCheck },
  OUTDATED: { tone: "warning", icon: AlertTriangle, label: (t) => t.transcription.toCheck },
  WAITING: { tone: "neutral", icon: Download, label: (t) => t.transcription.waiting },
};

const recording: Record<string, ChipSpec> = {
  SCHEDULED: { tone: "neutral", icon: CalendarClock, label: (t) => t.recording.scheduled },
  PENDING: { tone: "info", icon: LoaderCircle, label: (t) => t.recording.starting, busy: true },
  RECORDING: { tone: "live", icon: AudioLines, label: (t) => t.recording.live },
  EXPORTING: { tone: "info", icon: LoaderCircle, label: (t) => t.recording.saving, busy: true },
  COMPLETED: { tone: "success", icon: CircleCheck, label: (t) => t.recording.recorded },
  EXISTING: { tone: "success", icon: CircleCheck, label: (t) => t.recording.recorded },
  PARTIAL: { tone: "warning", icon: AlertTriangle, label: (t) => t.recording.partial },
  MISSED: { tone: "warning", icon: CircleX, label: (t) => t.recording.missed },
  FAILED: { tone: "danger", icon: CircleX, label: (t) => t.recording.failed },
  INTERRUPTED: { tone: "warning", icon: AlertTriangle, label: (t) => t.recording.partial },
  CONFLICT: { tone: "warning", icon: Split, label: (t) => t.recording.clash },
  NEEDS_COURSE: { tone: "warning", icon: Tag, label: (t) => t.recording.needsCourse },
  CANCELLED: { tone: "neutral", icon: CircleDashed, label: (t) => t.recording.excluded },
  MIC_BLOCKED: { tone: "danger", icon: MicOff, label: (t) => t.recording.micBlocked },
  OFF: { tone: "neutral", icon: MicOff, label: (t) => t.recording.off },
};

const assistant: Record<string, ChipSpec> = {
  missing: { tone: "danger", icon: Download, label: (t) => t.assistant.missing },
  incompatible: { tone: "warning", icon: AlertTriangle, label: (t) => t.assistant.incompatible },
  login_required: { tone: "warning", icon: KeyRound, label: (t) => t.assistant.signIn },
  unverified: { tone: "neutral", icon: CircleHelp, label: (t) => t.assistant.unchecked },
  ready: { tone: "success", icon: CircleCheck, label: (t) => t.assistant.ready },
};

const tables: Record<ChipKind, Record<string, ChipSpec>> = { notes, transcription, recording, assistant };

const unknown: ChipSpec = { tone: "neutral", icon: CircleHelp, label: (t) => t.unknown };

export function chipFor(kind: ChipKind, status: string): ChipSpec {
  return tables[kind][status] ?? unknown;
}

/** Statuses listed in the chip table, for the gallery and tests. */
export function knownStatuses(kind: ChipKind): string[] {
  return Object.keys(tables[kind]);
}
