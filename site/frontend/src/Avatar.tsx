import { CSSProperties, useEffect, useState } from "react";

const TONES = [
  "av-tone-0",
  "av-tone-1",
  "av-tone-2",
  "av-tone-3",
  "av-tone-4",
  "av-tone-5",
  "av-tone-6",
  "av-tone-7",
] as const;

function hashSeed(value: string | number): number {
  const s = String(value);
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

export function getInitials(name: string, maxLetters = 2): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length >= 2 && maxLetters >= 2) {
    return `${parts[0][0] ?? ""}${parts[1][0] ?? ""}`.toUpperCase();
  }
  return parts[0].slice(0, maxLetters).toUpperCase();
}

export function Avatar({
  name,
  src,
  seed,
  size = 36,
  shape = "circle",
  maxLetters = 2,
  className = "",
  style,
}: {
  name: string;
  src?: string | null;
  seed?: string | number;
  size?: number;
  shape?: "circle" | "rounded";
  maxLetters?: 1 | 2;
  className?: string;
  style?: CSSProperties;
}) {
  const [broken, setBroken] = useState(false);
  useEffect(() => {
    setBroken(false);
  }, [src]);

  const tone = TONES[hashSeed(seed ?? name) % TONES.length];
  const radius = shape === "circle" ? "50%" : `${Math.max(10, Math.round(size * 0.34))}px`;
  const fontSize = Math.max(11, Math.round(size * (maxLetters === 1 ? 0.42 : 0.36)));
  const showImage = Boolean(src && !broken);

  return (
    <span
      className={`app-avatar ${showImage ? "has-img" : tone} ${className}`.trim()}
      style={{ width: size, height: size, borderRadius: radius, fontSize, ...style }}
      aria-hidden="true"
    >
      {showImage ? (
        <img
          src={src!}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setBroken(true)}
        />
      ) : (
        getInitials(name, maxLetters)
      )}
    </span>
  );
}
