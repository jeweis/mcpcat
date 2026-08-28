import type { McpcatError } from "./errors.js";
import { redact } from "./redact.js";

export interface OutputWriter {
  success(data: unknown, humanMessage?: string): void;
  failure(error: McpcatError): void;
}

export class CommandOutput implements OutputWriter {
  constructor(
    private readonly writeOut: (value: string) => void,
    private readonly writeError: (value: string) => void,
    private readonly json: boolean,
    private readonly sensitiveValues: readonly string[] = [],
  ) {}

  success(data: unknown, humanMessage?: string): void {
    const safeData = redact(data, this.sensitiveValues);
    if (this.json) {
      this.writeOut(`${JSON.stringify({ ok: true, data: safeData })}\n`);
      return;
    }
    if (humanMessage !== undefined) {
      this.writeOut(`${humanMessage}\n`);
      return;
    }
    this.writeOut(`${JSON.stringify(safeData, null, 2)}\n`);
  }

  failure(error: McpcatError): void {
    const payload = redact(
      {
        ok: false,
        error: {
          code: error.code,
          message: error.message,
          ...(error.details === undefined ? {} : { details: error.details }),
        },
      },
      this.sensitiveValues,
    );
    const rendered = this.json
      ? JSON.stringify(payload)
      : `${error.code}: ${String((payload as { error: { message: unknown } }).error.message)}`;
    (this.json ? this.writeOut : this.writeError)(`${rendered}\n`);
  }
}
