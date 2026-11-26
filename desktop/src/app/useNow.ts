import { useEffect, useState } from "react";

/** The current time, refreshed every `intervalMs` (1 s for live clocks, 30 s for "in 18 min"). */
export function useNow(intervalMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}
