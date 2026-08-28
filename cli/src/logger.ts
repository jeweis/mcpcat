import { redact } from "./redact.js";

export type LogLevel = "debug" | "info" | "warn" | "error";

export interface Logger {
  log(level: LogLevel, message: string, details?: Record<string, unknown>): void;
}

export class SafeLogger implements Logger {
  constructor(
    private readonly write: (value: string) => void,
    private readonly json = false,
    private readonly sensitiveValues: readonly string[] = [],
  ) {}

  log(level: LogLevel, message: string, details?: Record<string, unknown>): void {
    const payload = redact(
      { level, message, ...(details === undefined ? {} : { details }) },
      this.sensitiveValues,
    ) as Record<string, unknown>;
    if (this.json) {
      this.write(`${JSON.stringify(payload)}\n`);
      return;
    }
    const suffix = payload.details === undefined ? "" : ` ${JSON.stringify(payload.details)}`;
    this.write(`[${level}] ${String(payload.message)}${suffix}\n`);
  }
}
