/**
 * The backend serialises naive UTC datetimes, with no offset. `new Date()`
 * reads those as *local* time, which on a UTC+2 machine dates every project two
 * hours in the future and prints "just now" for an hour. Stamp the Z back on.
 */
export function parseUtc(isoString: string): Date {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(isoString);
  return new Date(hasZone ? isoString : `${isoString}Z`);
}

export function relativeDate(isoString: string): string {
  const diff = Date.now() - parseUtc(isoString).getTime();
  const seconds = Math.max(0, Math.floor(diff / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes > 1 ? 's' : ''} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours > 1 ? 's' : ''} ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} day${days > 1 ? 's' : ''} ago`;
  const months = Math.floor(days / 30);
  return `${months} month${months > 1 ? 's' : ''} ago`;
}

export function absoluteDate(isoString: string): string {
  return parseUtc(isoString).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}
