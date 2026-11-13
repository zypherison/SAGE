/** Small inline icon set, 1.6 px stroke, inheriting currentColor. */
type IconProps = { size?: number };

const base = (size: number) => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
});

export const IconCursor = ({ size = 15 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M5 3l14 7-6 1.6L10.5 18z" />
  </svg>
);

export const IconSquare = ({ size = 15 }: IconProps) => (
  <svg {...base(size)}>
    <rect x="4" y="4" width="16" height="16" rx="2" />
  </svg>
);

export const IconPolygon = ({ size = 15 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M12 3l7.8 5.6-3 9.4H7.2l-3-9.4z" />
  </svg>
);

export const IconLayers = ({ size = 15 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M12 3l9 5-9 5-9-5z" />
    <path d="M3 13l9 5 9-5" />
  </svg>
);

export const IconChart = ({ size = 15 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M4 20V4" />
    <path d="M4 20h16" />
    <path d="M8 16v-5M12 16V7M16 16v-8" />
  </svg>
);

export const IconExternal = ({ size = 14 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M14 4h6v6" />
    <path d="M20 4l-8 8" />
    <path d="M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5" />
  </svg>
);

export const IconTrash = ({ size = 14 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M4 7h16" />
    <path d="M9 7V5h6v2" />
    <path d="M6 7l1 12h10l1-12" />
  </svg>
);

export const IconMark = ({ size = 22 }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M12 2.5l9 5.2v8.6l-9 5.2-9-5.2V7.7z" stroke="currentColor" strokeWidth="1.5" />
    <path d="M12 8l4.2 2.4v4.2L12 17l-4.2-2.4v-4.2z" fill="currentColor" opacity="0.9" />
  </svg>
);

export const IconEmpty = ({ size = 26 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M12 3l9 5-9 5-9-5z" />
    <path d="M3 12.5l9 5 9-5" />
    <path d="M3 17l9 5 9-5" opacity="0.45" />
  </svg>
);
