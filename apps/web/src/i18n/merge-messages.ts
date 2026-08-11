type MessageTree = Record<string, unknown>;

function isPlainObject(value: unknown): value is MessageTree {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Deep-merge locale messages onto English so partial namespaces keep EN fallbacks. */
export function mergeMessages(
  enMessages: MessageTree,
  localeMessages: MessageTree,
): MessageTree {
  const merged: MessageTree = { ...enMessages };

  for (const [key, localeValue] of Object.entries(localeMessages)) {
    const enValue = enMessages[key];
    if (isPlainObject(enValue) && isPlainObject(localeValue)) {
      merged[key] = mergeMessages(enValue, localeValue);
      continue;
    }
    merged[key] = localeValue;
  }

  return merged;
}
