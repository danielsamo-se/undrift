/** Build-time jury profile; the existing local application defaults to operational. */
export const PUBLIC_JURY = import.meta.env.VITE_PUBLIC_JURY === '1';
let replay = 'clarification';
export function setReplay(id: string) { replay = id; }
export function replayRequest(path: string) {
  if (!PUBLIC_JURY || path === '/api/demo') return path;
  return `${path}${path.includes('?') ? '&' : '?'}replay=${encodeURIComponent(replay)}`;
}
