export const ExitCode = {
  success: 0,
  failure: 1,
  usage: 2,
  configuration: 3,
  authentication: 4,
  network: 5,
  compatibility: 6,
  insecureTransport: 7,
  schema: 8,
  credentialStore: 9,
  nonInteractive: 10,
  installation: 11,
  integrity: 12,
} as const;

export type ExitCodeValue = (typeof ExitCode)[keyof typeof ExitCode];

export const ErrorCode = {
  usage: "MCPCAT_USAGE",
  profileNotFound: "MCPCAT_PROFILE_NOT_FOUND",
  profileInvalid: "MCPCAT_PROFILE_INVALID",
  configInvalid: "MCPCAT_CONFIG_INVALID",
  authRequired: "MCPCAT_AUTH_REQUIRED",
  authRejected: "MCPCAT_AUTH_REJECTED",
  network: "MCPCAT_NETWORK_ERROR",
  http: "MCPCAT_HTTP_ERROR",
  incompatible: "MCPCAT_INCOMPATIBLE_API",
  insecureTransport: "MCPCAT_INSECURE_TRANSPORT",
  schema: "MCPCAT_INVALID_RESPONSE",
  credentialStore: "MCPCAT_CREDENTIAL_STORE_ERROR",
  nonInteractiveInput: "MCPCAT_NON_INTERACTIVE_INPUT_REQUIRED",
  agentNotFound: "MCPCAT_AGENT_NOT_FOUND",
  agentAmbiguous: "MCPCAT_AGENT_AMBIGUOUS",
  targetInvalid: "MCPCAT_TARGET_INVALID",
  skillNotFound: "MCPCAT_SKILL_NOT_FOUND",
  integrity: "MCPCAT_INTEGRITY_ERROR",
  packageInvalid: "MCPCAT_PACKAGE_INVALID",
  installFailed: "MCPCAT_INSTALL_FAILED",
  partialFailure: "MCPCAT_PARTIAL_FAILURE",
  lockBusy: "MCPCAT_INSTALL_LOCK_BUSY",
  internal: "MCPCAT_INTERNAL_ERROR",
} as const;

export type ErrorCodeValue = (typeof ErrorCode)[keyof typeof ErrorCode];

export class McpcatError extends Error {
  readonly code: ErrorCodeValue;
  readonly exitCode: ExitCodeValue;
  readonly details?: Record<string, unknown>;

  constructor(
    code: ErrorCodeValue,
    message: string,
    exitCode: ExitCodeValue,
    details?: Record<string, unknown>,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "McpcatError";
    this.code = code;
    this.exitCode = exitCode;
    if (details !== undefined) {
      this.details = details;
    }
  }
}

export function toMcpcatError(error: unknown): McpcatError {
  if (error instanceof McpcatError) {
    return error;
  }
  return new McpcatError(
    ErrorCode.internal,
    "发生未预期错误",
    ExitCode.failure,
    undefined,
    { cause: error },
  );
}
