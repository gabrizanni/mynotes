/**
 * English is the source language. One file per feature so parallel work never touches the
 * same file. `Messages` is derived from these objects; Italian must match it exactly.
 */
import { activity } from "./activity";
import { assistant } from "./assistant";
import { calendar } from "./calendar";
import { capture } from "./capture";
import { common } from "./common";
import { courses } from "./courses";
import { gallery } from "./gallery";
import { lesson } from "./lesson";
import { library } from "./library";
import { nav } from "./nav";
import { onboarding } from "./onboarding";
import { palette } from "./palette";
import { settings } from "./settings";
import { status } from "./status";
import { today } from "./today";

export const en = {
  common,
  nav,
  status,
  today,
  calendar,
  courses,
  library,
  activity,
  lesson,
  settings,
  onboarding,
  assistant,
  capture,
  gallery,
  palette,
};

export type Messages = typeof en;
export type Namespace = keyof Messages;
