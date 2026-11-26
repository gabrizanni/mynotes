import type { LucideIcon } from "lucide-react";

export type IconSize = 14 | 16 | 20 | 24;

interface IconProps {
  icon: LucideIcon;
  size?: IconSize;
  className?: string;
  /** Only for standalone meaningful icons; decorative icons (the default) are hidden. */
  label?: string;
}

/** Lucide at the 1.75 px stroke that matches the hairline look (UX-SPEC §4 Icons). */
export function Icon({ icon: Glyph, size = 20, className, label }: IconProps) {
  return (
    <Glyph
      size={size}
      strokeWidth={1.75}
      absoluteStrokeWidth={size <= 16}
      className={className}
      aria-hidden={label ? undefined : true}
      aria-label={label}
      role={label ? "img" : undefined}
      focusable="false"
    />
  );
}
