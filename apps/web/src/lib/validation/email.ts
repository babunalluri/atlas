export const EMAIL_ALREADY_IN_USE = "This email is already in use.";

export function isTakenEmail(email: string, taken: string[]): boolean {
  const normalized = email.trim().toLowerCase();
  if (!normalized) return false;
  return taken.some((item) => item.trim().toLowerCase() === normalized);
}
