import { ActivityPage } from "../features/activity";
import { CalendarPage } from "../features/calendar";
import { CoursePage, CoursesPage } from "../features/courses";
import { GalleryPage } from "../features/gallery";
import { LessonPage } from "../features/lesson";
import { LibraryPage } from "../features/library";
import { WelcomePage } from "../features/onboarding";
import { SettingsPage } from "../features/settings";
import { TodayPage } from "../features/today";
import type { Route } from "../state/routes";

/** One case per route variant; the switch is exhaustive, so a new route fails `tsc` here. */
export function AppRoutes({ route }: { route: Route }) {
  switch (route.name) {
    case "today":
      return <TodayPage />;
    case "calendar":
      return <CalendarPage route={route} />;
    case "courses":
      return <CoursesPage />;
    case "course":
      return <CoursePage route={route} />;
    case "library":
      return <LibraryPage route={route} />;
    case "activity":
      return <ActivityPage />;
    case "lesson":
      return <LessonPage route={route} />;
    case "settings":
      return <SettingsPage route={route} />;
    case "welcome":
      return <WelcomePage route={route} />;
    case "gallery":
      return <GalleryPage />;
  }
}
