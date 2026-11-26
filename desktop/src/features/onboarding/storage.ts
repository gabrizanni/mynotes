/**
 * The app's own `onboarding.json` (UX-SPEC §2 Resume), kept in the webview's local storage:
 * Tauri persists it in the app data folder, a browser keeps it per origin. Every read is
 * validated; anything unreadable starts a fresh record instead of breaking the window.
 */
import { z } from "zod";

import { WELCOME_STEPS } from "../../state/routes";
import {
  COURSE_KINDS,
  DETAILS,
  EMPTY_RECORD,
  GOALS,
  IMAGE_POLICIES,
  LAYOUTS,
  LECTURE_LANGUAGES,
  PROVIDERS,
  RECAP_LANGUAGES,
  type OnboardingRecord,
} from "./model";

export const STORAGE_KEY = "mynotes.onboarding.v1";

const lang = z.enum(["en", "it"]);
const mark = z.enum(["done", "skipped"]);

const recordSchema = z.object({
  version: z.literal(1),
  language: lang.nullable(),
  provider: z.enum(PROVIDERS).nullable(),
  studies: z.object({ background: z.string().max(4000), goals: z.array(z.enum(GOALS)) }).nullable(),
  course: z
    .object({
      name: z.string().max(200),
      lectureLanguage: z.enum(LECTURE_LANGUAGES),
      notesLanguage: lang,
      kind: z.enum(COURSE_KINDS),
      examFormat: z.string().max(400),
      examUnknown: z.boolean(),
      hardTopics: z.array(z.string().max(120)).max(40),
      anythingElse: z.string().max(4000),
    })
    .nullable(),
  style: z
    .object({
      detail: z.enum(DETAILS),
      layout: z.enum(LAYOUTS),
      images: z.enum(IMAGE_POLICIES),
      imageLimit: z.number().int().min(1).max(8),
      recap: z.boolean(),
      recapLanguage: z.enum(RECAP_LANGUAGES),
      questions: z.number().int().min(1).max(5),
    })
    .nullable(),
  marks: z.partialRecord(z.enum(WELCOME_STEPS), mark),
  saved: z.partialRecord(z.enum(["language", "profile", "course"]), z.string()),
  courseCode: z.string().nullable(),
  folder: z.object({ path: z.string(), reviewFirst: z.boolean() }).nullable().default(null),
  calendar: z.object({ url: z.string(), sourceID: z.string() }).nullable().default(null),
  micTestAt: z.number().nullable().default(null),
  finished: z.boolean(),
});

export interface RecordStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function parseRecord(raw: string | null): OnboardingRecord {
  if (!raw) return EMPTY_RECORD;
  try {
    const parsed = recordSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : EMPTY_RECORD;
  } catch {
    return EMPTY_RECORD;
  }
}

export function loadRecord(storage: RecordStorage | null): OnboardingRecord {
  if (!storage) return EMPTY_RECORD;
  try {
    return parseRecord(storage.getItem(STORAGE_KEY));
  } catch {
    return EMPTY_RECORD;
  }
}

export function saveRecord(storage: RecordStorage | null, record: OnboardingRecord): void {
  if (!storage) return;
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(record));
  } catch {
    // Storage full or blocked: the draft lasts for this session only.
  }
}

export function browserStorage(): RecordStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}
