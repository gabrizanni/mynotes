import { useEffect } from "react";

import { parseRoute, routeToHash } from "../state/routes";
import { useActions, useAppState } from "../state";

/** Two-way binding between the URL hash and `ui.route` (back/forward work, links are plain hrefs). */
export function useHashRoute(): void {
  const route = useAppState((state) => state.ui.route);
  const { ui } = useActions();

  useEffect(() => {
    const onHash = () => ui.navigate(parseRoute(window.location.hash, window.location.pathname));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [ui]);

  useEffect(() => {
    if (window.location.pathname.replace(/\/+$/, "") === "/__gallery") return;
    const hash = routeToHash(route);
    if (window.location.hash !== hash) window.history.pushState(null, "", `${window.location.pathname}${window.location.search}${hash}`);
  }, [route]);
}
