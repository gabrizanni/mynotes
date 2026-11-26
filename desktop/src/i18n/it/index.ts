/** Italian. Typed as `Messages`: a missing or extra key fails `npm run typecheck`. */
import type { Messages } from "../en";
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
import { palette } from "./palette"; // workspace screens
import { settings } from "./settings";
import { status } from "./status";
import { today } from "./today";

export const it: Messages = {
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
  // workspace screens
  palette,
};
