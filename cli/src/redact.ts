const SENSITIVE_KEY = /(?:api[_-]?key|authorization|password|passwd|secret|token|cookie)$|^credential$/i;
const ENV_SECRET = /\b(MCPCAT_API_KEY|API_KEY|ACCESS_TOKEN|CLIENT_SECRET)=([^\s]+)/gi;
const AUTH_VALUE = /\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+/gi;

export const REDACTED = "[REDACTED]";

function redactString(value: string, sensitiveValues: readonly string[]): string {
  let result = value
    .replace(ENV_SECRET, (_match, name: string) => `${name}=${REDACTED}`)
    .replace(AUTH_VALUE, (_match, scheme: string) => `${scheme} ${REDACTED}`);
  for (const secret of sensitiveValues) {
    if (secret.length > 0) {
      result = result.replaceAll(secret, REDACTED);
    }
  }
  return result;
}

export function redact(
  value: unknown,
  sensitiveValues: readonly string[] = [],
  seen = new WeakSet<object>(),
): unknown {
  if (typeof value === "string") {
    return redactString(value, sensitiveValues);
  }
  if (value === null || typeof value !== "object") {
    return value;
  }
  if (seen.has(value)) {
    return "[CIRCULAR]";
  }
  seen.add(value);
  if (Array.isArray(value)) {
    return value.map((item) => redact(item, sensitiveValues, seen));
  }
  const output: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    output[key] = SENSITIVE_KEY.test(key)
      ? REDACTED
      : redact(item, sensitiveValues, seen);
  }
  return output;
}
