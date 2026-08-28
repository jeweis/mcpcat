import { ErrorCode, ExitCode, McpcatError } from "./errors.js";

export interface GlobalOptions {
  json: boolean;
  nonInteractive: boolean;
  allowHttp: boolean;
  help: boolean;
  version: boolean;
  profile?: string;
  agents: string[];
  allDetectedAgents: boolean;
  all: boolean;
  scope?: string;
  targetDir?: string;
  skillVersion?: string;
}

export interface ParsedArgs {
  command?: string;
  positionals: string[];
  options: GlobalOptions;
}

function takeValue(args: readonly string[], index: number, flag: string): string {
  const value = args[index + 1];
  if (value === undefined || value.startsWith("--")) {
    throw new McpcatError(
      ErrorCode.usage,
      `${flag} 需要一个值`,
      ExitCode.usage,
    );
  }
  return value;
}

export function parseArgs(args: readonly string[]): ParsedArgs {
  const options: GlobalOptions = {
    json: false,
    nonInteractive: false,
    allowHttp: false,
    help: false,
    version: false,
    agents: [],
    allDetectedAgents: false,
    all: false,
  };
  const positionals: string[] = [];
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (argument === "--json") {
      options.json = true;
    } else if (argument === "--non-interactive") {
      options.nonInteractive = true;
    } else if (argument === "--allow-http") {
      options.allowHttp = true;
    } else if (argument === "--help" || argument === "-h") {
      options.help = true;
    } else if (argument === "--version") {
      const value = args[index + 1];
      if (value !== undefined && !value.startsWith("--")) {
        options.skillVersion = value;
        index += 1;
      } else {
        options.version = true;
      }
    } else if (argument === "-V") {
      options.version = true;
    } else if (argument === "--profile") {
      options.profile = takeValue(args, index, argument);
      index += 1;
    } else if (argument === "--agent") {
      options.agents.push(takeValue(args, index, argument));
      index += 1;
    } else if (argument === "--all-detected-agents") {
      options.allDetectedAgents = true;
    } else if (argument === "--all") {
      options.all = true;
    } else if (argument === "--scope") {
      options.scope = takeValue(args, index, argument);
      index += 1;
    } else if (argument === "--target-dir") {
      options.targetDir = takeValue(args, index, argument);
      index += 1;
    } else if (argument === "--skill-version") {
      options.skillVersion = takeValue(args, index, argument);
      index += 1;
    } else if (argument?.startsWith("--")) {
      throw new McpcatError(
        ErrorCode.usage,
        `未知选项：${argument}`,
        ExitCode.usage,
      );
    } else if (argument !== undefined) {
      positionals.push(argument);
    }
  }
  const command = positionals.shift();
  return { positionals, options, ...(command === undefined ? {} : { command }) };
}
