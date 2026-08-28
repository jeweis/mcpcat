import type { ReadStream, WriteStream } from "node:tty";

import { ErrorCode, ExitCode, McpcatError } from "./errors.js";

export type HiddenPrompt = (message: string) => Promise<string>;

export function createHiddenPrompt(
  input: ReadStream = process.stdin,
  output: WriteStream = process.stderr,
): HiddenPrompt {
  return async (message: string): Promise<string> => {
    if (!input.isTTY || typeof input.setRawMode !== "function") {
      throw new McpcatError(
        ErrorCode.nonInteractiveInput,
        "当前终端无法安全隐藏输入，请设置 MCPCAT_API_KEY",
        ExitCode.nonInteractive,
      );
    }
    output.write(message);
    input.setEncoding("utf8");
    input.setRawMode(true);
    input.resume();
    let value = "";
    try {
      return await new Promise<string>((resolve, reject) => {
        const onData = (chunk: string): void => {
          for (const character of chunk) {
            if (character === "\u0003") {
              cleanup();
              reject(new McpcatError(ErrorCode.usage, "操作已取消", ExitCode.usage));
              return;
            }
            if (character === "\r" || character === "\n") {
              cleanup();
              output.write("\n");
              resolve(value);
              return;
            }
            if (character === "\u007f" || character === "\b") {
              value = value.slice(0, -1);
            } else {
              value += character;
            }
          }
        };
        const cleanup = (): void => {
          input.off("data", onData);
        };
        input.on("data", onData);
      });
    } finally {
      input.setRawMode(false);
      input.pause();
    }
  };
}
