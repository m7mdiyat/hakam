// Mirrors backend/codes.py: uppercase, keep only the unambiguous alphabet.
const ALPHABET = new Set('ABCDEFGHJKLMNPQRSTUVWXYZ23456789'.split(''));

export function normalizeCode(raw) {
  return String(raw || '')
    .toUpperCase()
    .split('')
    .filter((c) => ALPHABET.has(c))
    .join('')
    .slice(0, 6);
}
