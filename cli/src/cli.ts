import {
  AGENT_ADAPTERS,
  AgentStore,
  assertAgentId,
  assertScope,
  createAgentContext,
  getAgentAdapter,
  type AgentContext,
  type AgentId,
} from "./agents.js";
import { createHash } from "node:crypto";
import { selectAgents, type AgentChoicePrompt } from "./agents/select.js";
import { parseArgs, type GlobalOptions } from "./args.js";
import { createAgentChoicePrompt } from "./choice-prompt.js";
import { resolveConnection } from "./connection.js";
import {
  createKeychainCredentialStore,
  type CredentialStore,
} from "./credentials.js";
import { ErrorCode, ExitCode, McpcatError, toMcpcatError } from "./errors.js";
import { runDoctor } from "./doctor.js";
import { RegistryClient, type Fetch } from "./http.js";
import { InstallationStore } from "./install-lock.js";
import { installSkill } from "./installer.js";
import {
  rollbackInstallations,
  setInstallationPins,
  updateInstallations,
} from "./lifecycle.js";
import { SafeLogger } from "./logger.js";
import { CommandOutput } from "./output.js";
import { ProfileStore, validateProfileName, type Profile } from "./profiles.js";
import { createHiddenPrompt, type HiddenPrompt } from "./prompt.js";
import { normalizeBaseUrl } from "./url.js";
import { CLI_VERSION } from "./version.js";

const HELP = `mcpcat ${CLI_VERSION}

用法:
  mcpcat connect [profile] <url> [--allow-http] [--non-interactive]
  mcpcat profiles [--json]
  mcpcat use <profile> [--json]
  mcpcat agents detect|list|use [agent] [--json]
  mcpcat skills list [--profile <name>] [--json]
  mcpcat skills info <slug> [--version <semver>]
  mcpcat skills install <slug> [--version <semver>] [--agent <id> ...]
    [--all-detected-agents] [--scope user|project] [--target-dir <path>]
  mcpcat skills update [<slug>|--all]
  mcpcat skills pin|unpin <slug> [--version <semver>]
  mcpcat skills rollback <slug> [--version <semver>]
  mcpcat doctor [--profile <name>]
  mcpcat -V

全局选项:
  --profile <name>    显式覆盖默认 Profile
  --json              输出稳定 JSON envelope
  --non-interactive   禁止任何交互输入
  --allow-http        允许非 localhost 的明文 HTTP
  --agent <id>        安装目标 Agent，可重复
  --all-detected-agents 安装到全部已检测 Agent
  --all               更新当前 Profile 的全部安装
  --scope <scope>     user（默认）或 project
  --target-dir <path> 显式覆盖 Skills 目录；Generic 必填
  --version <semver>  选择明确 Skill 版本
  -h, --help          显示帮助
  -V                  显示 CLI 版本

环境变量:
  MCPCAT_URL           临时覆盖实例地址
  MCPCAT_API_KEY       临时提供凭证（不会持久化）`;

export interface CliDependencies {
  env?: NodeJS.ProcessEnv;
  fetch?: Fetch;
  profileStore?: ProfileStore;
  agentStore?: AgentStore;
  agentContext?: AgentContext;
  installationStore?: InstallationStore;
  agentPrompt?: AgentChoicePrompt;
  credentialStore?: CredentialStore | null;
  prompt?: HiddenPrompt;
  stdinIsTTY?: boolean;
  now?: () => Date;
  stdout?: (value: string) => void;
  stderr?: (value: string) => void;
}

interface Runtime {
  env: NodeJS.ProcessEnv;
  profileStore: ProfileStore;
  agentStore: AgentStore;
  agentContext: AgentContext;
  installationStore: InstallationStore;
  agentPrompt: AgentChoicePrompt;
  credentialStore?: CredentialStore;
  prompt: HiddenPrompt;
  stdinIsTTY: boolean;
  now: () => Date;
  stdout: (value: string) => void;
  stderr: (value: string) => void;
  fetch?: Fetch;
  credentialStoreWasSpecified: boolean;
}

function runtime(dependencies: CliDependencies): Runtime {
  const fetch = dependencies.fetch;
  return {
    env: dependencies.env ?? process.env,
    profileStore: dependencies.profileStore ?? new ProfileStore(),
    agentStore: dependencies.agentStore ?? new AgentStore(),
    agentContext:
      dependencies.agentContext ??
      createAgentContext({
        ...(dependencies.env === undefined ? {} : { env: dependencies.env }),
      }),
    installationStore: dependencies.installationStore ?? new InstallationStore(),
    agentPrompt: dependencies.agentPrompt ?? createAgentChoicePrompt(),
    ...(dependencies.credentialStore == null
      ? {}
      : { credentialStore: dependencies.credentialStore }),
    prompt: dependencies.prompt ?? createHiddenPrompt(),
    stdinIsTTY: dependencies.stdinIsTTY ?? process.stdin.isTTY,
    now: dependencies.now ?? (() => new Date()),
    stdout: dependencies.stdout ?? ((value) => process.stdout.write(value)),
    stderr: dependencies.stderr ?? ((value) => process.stderr.write(value)),
    ...(fetch === undefined ? {} : { fetch }),
    credentialStoreWasSpecified: dependencies.credentialStore !== undefined,
  };
}

function nonInteractive(options: GlobalOptions, state: Runtime): boolean {
  const ci = state.env.CI?.toLowerCase();
  return options.nonInteractive || !state.stdinIsTTY || ci === "true" || ci === "1";
}

function connectArguments(
  positionals: readonly string[],
  options: GlobalOptions,
  env: NodeJS.ProcessEnv,
): { profileName: string; inputUrl: string } {
  if (positionals.length > 2) {
    throw new McpcatError(ErrorCode.usage, "connect 参数过多", ExitCode.usage);
  }
  if (positionals.length === 2 && options.profile !== undefined) {
    throw new McpcatError(
      ErrorCode.usage,
      "请勿同时使用位置 Profile 和 --profile",
      ExitCode.usage,
    );
  }
  const inputUrl = positionals.at(-1) ?? env.MCPCAT_URL;
  if (inputUrl === undefined) {
    throw new McpcatError(
      ErrorCode.usage,
      "connect 需要实例 URL，或设置 MCPCAT_URL",
      ExitCode.usage,
    );
  }
  const positionalProfile = positionals.length === 2 ? positionals[0] : undefined;
  const profileName = validateProfileName(options.profile ?? positionalProfile ?? "default");
  return { profileName, inputUrl };
}

async function resolveCredentialStore(state: Runtime): Promise<CredentialStore | undefined> {
  if (state.credentialStore !== undefined || state.credentialStoreWasSpecified) {
    return state.credentialStore;
  }
  return createKeychainCredentialStore();
}

async function connect(
  positionals: readonly string[],
  options: GlobalOptions,
  state: Runtime,
  output: CommandOutput,
  logger: SafeLogger,
  sensitiveValues: string[],
): Promise<void> {
  const { profileName, inputUrl } = connectArguments(positionals, options, state.env);
  const normalized = normalizeBaseUrl(inputUrl, options.allowHttp);
  if (normalized.insecure) {
    logger.log("warn", "正在使用非加密 HTTP 连接；API Key 可能被网络观察者截获", {
      url: normalized.url,
    });
  }
  const clientOptions = {
    allowHttp: options.allowHttp,
    ...(state.fetch === undefined ? {} : { fetch: state.fetch }),
  };
  const client = new RegistryClient(clientOptions);
  const bootstrap = await client.bootstrap(normalized.url);
  let apiKey = state.env.MCPCAT_API_KEY;
  const fromEnvironment = apiKey !== undefined && apiKey.length > 0;
  if (!fromEnvironment) {
    if (nonInteractive(options, state)) {
      throw new McpcatError(
        ErrorCode.nonInteractiveInput,
        "非交互模式需要 MCPCAT_API_KEY",
        ExitCode.nonInteractive,
      );
    }
    apiKey = (await state.prompt(`请输入 ${bootstrap.instance_name} API Key: `)).trim();
    if (apiKey.length === 0) {
      throw new McpcatError(
        ErrorCode.authRequired,
        "API Key 不能为空",
        ExitCode.authentication,
      );
    }
  }
  const verifiedApiKey = apiKey;
  if (verifiedApiKey === undefined || verifiedApiKey.length === 0) {
    throw new McpcatError(ErrorCode.authRequired, "API Key 不能为空", ExitCode.authentication);
  }
  sensitiveValues.push(verifiedApiKey);
  await client.registry(bootstrap, verifiedApiKey);

  const timestamp = state.now().toISOString();
  const canonical = normalizeBaseUrl(bootstrap.base_url, options.allowHttp).url;
  const profile: Profile = {
    name: profileName,
    baseUrl: canonical,
    instanceName: bootstrap.instance_name,
    apiVersion: bootstrap.api_version,
    registrySchemaVersion: bootstrap.registry_schema_version,
    authHeaderName: bootstrap.auth_header_name,
    registryPath: bootstrap.registry_path,
    createdAt: timestamp,
    updatedAt: timestamp,
  };
  await state.profileStore.save(profile);

  let credentialPersistence: "environment" | "keychain" | "session-only";
  if (fromEnvironment) {
    credentialPersistence = "environment";
  } else {
    const credentialStore = await resolveCredentialStore(state);
    if (credentialStore === undefined) {
      credentialPersistence = "session-only";
      logger.log("warn", "系统 Keychain 不可用；凭证仅用于本次进程且未写入文件");
    } else {
      try {
        await credentialStore.set(profileName, verifiedApiKey);
        credentialPersistence = "keychain";
      } catch (error) {
        credentialPersistence = "session-only";
        logger.log("warn", "系统 Keychain 保存失败；凭证未持久化", {
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }
  }
  output.success(
    {
      profile: profileName,
      instanceName: bootstrap.instance_name,
      baseUrl: canonical,
      apiVersion: bootstrap.api_version,
      registrySchemaVersion: bootstrap.registry_schema_version,
      credentialPersistence,
    },
    `已连接 ${bootstrap.instance_name}，Profile：${profileName}（凭证：${credentialPersistence}）`,
  );
}

async function listProfiles(
  positionals: readonly string[],
  state: Runtime,
  output: CommandOutput,
): Promise<void> {
  if (positionals.length !== 0) {
    throw new McpcatError(ErrorCode.usage, "profiles 不接受位置参数", ExitCode.usage);
  }
  const result = await state.profileStore.list();
  const data = {
    defaultProfile: result.defaultProfile ?? null,
    profiles: result.profiles.map((profile) => ({
      name: profile.name,
      isDefault: profile.name === result.defaultProfile,
      instanceName: profile.instanceName,
      baseUrl: profile.baseUrl,
      apiVersion: profile.apiVersion,
      registrySchemaVersion: profile.registrySchemaVersion,
    })),
  };
  const human =
    data.profiles.length === 0
      ? "尚未配置 Profile"
      : data.profiles
          .map((profile) => `${profile.isDefault ? "*" : " "} ${profile.name}\t${profile.baseUrl}`)
          .join("\n");
  output.success(data, human);
}

async function useProfile(
  positionals: readonly string[],
  state: Runtime,
  output: CommandOutput,
): Promise<void> {
  const name = positionals[0];
  if (positionals.length !== 1 || name === undefined) {
    throw new McpcatError(ErrorCode.usage, "use 需要一个 Profile 名称", ExitCode.usage);
  }
  const profile = await state.profileStore.use(name);
  output.success(
    { defaultProfile: profile.name, baseUrl: profile.baseUrl },
    `默认 Profile 已切换为 ${profile.name}`,
  );
}

async function registrySession(
  options: GlobalOptions,
  state: Runtime,
  sensitiveValues: string[],
): Promise<{
  client: RegistryClient;
  bootstrap: Awaited<ReturnType<RegistryClient["bootstrap"]>>;
  apiKey: string;
  profileId: string;
  profileName: string | null;
  source: "environment" | "profile";
}> {
  const credentialStore = await resolveCredentialStore(state);
  const connection = await resolveConnection(state.profileStore, credentialStore, {
    env: state.env,
    allowHttp: options.allowHttp,
    ...(options.profile === undefined ? {} : { profileName: options.profile }),
  });
  sensitiveValues.push(connection.apiKey);
  const client = new RegistryClient({
    allowHttp: options.allowHttp,
    ...(state.fetch === undefined ? {} : { fetch: state.fetch }),
  });
  const bootstrap = connection.profile === undefined
    ? await client.bootstrap(connection.baseUrl)
    : {
        instance_name: connection.profile.instanceName,
        base_url: connection.profile.baseUrl,
        api_version: connection.profile.apiVersion,
        registry_schema_version: connection.profile.registrySchemaVersion,
        auth_header_name: connection.profile.authHeaderName,
        registry_path: connection.profile.registryPath,
        min_cli_version: CLI_VERSION,
        recommended_cli_version: CLI_VERSION,
      };
  const profileId = connection.profile?.name ??
    `environment:${createHash("sha256").update(connection.baseUrl).digest("hex").slice(0, 16)}`;
  return {
    client,
    bootstrap,
    apiKey: connection.apiKey,
    profileId,
    profileName: connection.profile?.name ?? null,
    source: connection.source,
  };
}

async function skills(
  positionals: readonly string[],
  options: GlobalOptions,
  state: Runtime,
  output: CommandOutput,
  sensitiveValues: string[],
): Promise<void> {
  const subcommand = positionals[0];
  if (subcommand === "list") {
    if (positionals.length !== 1) {
      throw new McpcatError(ErrorCode.usage, "用法：mcpcat skills list", ExitCode.usage);
    }
    const session = await registrySession(options, state, sensitiveValues);
    const result = await session.client.registry(session.bootstrap, session.apiKey);
    const visibleSkills = result.registry?.skills ?? [];
    const human = visibleSkills.length === 0
      ? "没有可见的已发布 Skill"
      : visibleSkills
          .map((skill) => `${skill.slug}\t${skill.latest_published_version}\t${skill.status}`)
          .join("\n");
    output.success(
      {
        source: session.source,
        profile: session.profileName,
        etag: result.etag ?? null,
        skills: visibleSkills,
      },
      human,
    );
    return;
  }
  if (["update", "pin", "unpin", "rollback"].includes(subcommand ?? "")) {
    const lifecycleSlug = positionals[1];
    if (subcommand === "update") {
      if (
        positionals.length > 2 ||
        (options.all && lifecycleSlug !== undefined) ||
        (!options.all && lifecycleSlug === undefined)
      ) {
        throw new McpcatError(
          ErrorCode.usage,
          "用法：mcpcat skills update <slug> 或 mcpcat skills update --all",
          ExitCode.usage,
        );
      }
    } else if (positionals.length !== 2 || lifecycleSlug === undefined) {
      throw new McpcatError(
        ErrorCode.usage,
        `用法：mcpcat skills ${subcommand} <slug>`,
        ExitCode.usage,
      );
    }
    const session = await registrySession(options, state, sensitiveValues);
    const filter = {
      ...(lifecycleSlug === undefined ? {} : { slug: lifecycleSlug }),
      ...(options.agents.length === 0
        ? {}
        : { agents: options.agents.map((agent) => assertAgentId(agent)) }),
      ...(options.scope === undefined ? {} : { scope: assertScope(options.scope) }),
    };
    const context = {
      client: session.client,
      bootstrap: session.bootstrap,
      apiKey: session.apiKey,
      profileId: session.profileId,
      environment: state.agentContext,
      store: state.installationStore,
      now: state.now,
    };
    const results = subcommand === "update"
      ? await updateInstallations(context, filter)
      : subcommand === "rollback"
        ? await rollbackInstallations(context, filter, options.skillVersion)
        : await setInstallationPins(
            state.installationStore,
            session.profileId,
            filter,
            subcommand === "pin",
            options.skillVersion,
          );
    if (results.length === 0) {
      throw new McpcatError(
        ErrorCode.skillNotFound,
        "当前 Profile 没有匹配的安装记录",
        ExitCode.configuration,
        { profile: session.profileId, filter },
      );
    }
    const failures = results.filter((result) => result.status === "failed");
    if (failures.length > 0) {
      throw new McpcatError(
        ErrorCode.partialFailure,
        `${subcommand} 存在失败目标`,
        ExitCode.installation,
        { results },
      );
    }
    output.success(
      { operation: subcommand, results },
      results.map((result) =>
        `${result.installation.agent}/${result.installation.scope}/${result.installation.skill}: ${result.status}${
          result.toVersion === undefined ? "" : ` -> ${result.toVersion}`
        }`,
      ).join("\n"),
    );
    return;
  }
  const slug = positionals[1];
  if (slug === undefined || positionals.length !== 2) {
    throw new McpcatError(
      ErrorCode.usage,
      "用法：mcpcat skills info|install <slug>",
      ExitCode.usage,
    );
  }
  const session = await registrySession(options, state, sensitiveValues);
  if (subcommand === "info") {
    const detail = await session.client.skillDetail(session.bootstrap, session.apiKey, slug);
    const selected = options.skillVersion === undefined
      ? detail.versions.find((version) => version.status === "published") ?? detail.versions[0]
      : detail.versions.find((version) => version.version === options.skillVersion);
    if (selected === undefined) {
      throw new McpcatError(
        ErrorCode.skillNotFound,
        `Skill 版本不存在：${options.skillVersion ?? "latest"}`,
        ExitCode.configuration,
        { slug, version: options.skillVersion ?? null },
      );
    }
    output.success(
      { ...detail, selectedVersion: selected },
      `${detail.slug}\t${selected.version}\t${selected.status}\n${detail.description}`,
    );
    return;
  }
  if (subcommand !== "install") {
    throw new McpcatError(
      ErrorCode.usage,
      "用法：mcpcat skills list|info|install",
      ExitCode.usage,
    );
  }
  const selectedAgents = await selectAgents({
    explicit: options.agents,
    allDetected: options.allDetectedAgents,
    nonInteractive: nonInteractive(options, state),
    environment: state.agentContext,
    store: state.agentStore,
    prompt: state.agentPrompt,
  });
  const scope = assertScope(options.scope ?? "user");
  const targets = selectedAgents.map((agent: AgentId) => ({
    agent,
    scope,
    ...(options.targetDir === undefined ? {} : { targetDir: options.targetDir }),
  }));
  const results = await installSkill({
    client: session.client,
    bootstrap: session.bootstrap,
    apiKey: session.apiKey,
    profileId: session.profileId,
    slug,
    targets,
    environment: state.agentContext,
    installationStore: state.installationStore,
    now: state.now,
    ...(options.skillVersion === undefined ? {} : { version: options.skillVersion }),
  });
  const failures = results.filter((result) => result.status === "failed");
  if (failures.length > 0) {
    const lines = results.map((result) =>
      `${result.agent}: ${result.status}${result.error === undefined ? "" : ` (${result.error.message})`}`,
    );
    throw new McpcatError(
      ErrorCode.partialFailure,
      `安装完成但存在失败目标：\n${lines.join("\n")}`,
      ExitCode.installation,
      { slug, results },
    );
  }
  output.success(
    { slug, version: results[0]?.version ?? options.skillVersion ?? null, results },
    results.map((result) => `${result.agent}: ${result.target}`).join("\n"),
  );
}

async function doctor(
  positionals: readonly string[],
  options: GlobalOptions,
  state: Runtime,
  output: CommandOutput,
): Promise<void> {
  if (positionals.length !== 0) {
    throw new McpcatError(ErrorCode.usage, "用法：mcpcat doctor", ExitCode.usage);
  }
  const credentialStore = await resolveCredentialStore(state);
  const checks = await runDoctor({
    profileStore: state.profileStore,
    installationStore: state.installationStore,
    agentEnvironment: state.agentContext,
    env: state.env,
    allowHttp: options.allowHttp,
    ...(credentialStore === undefined ? {} : { credentialStore }),
    ...(options.profile === undefined ? {} : { profileName: options.profile }),
    ...(state.fetch === undefined ? {} : { fetch: state.fetch }),
  });
  const healthy = checks.every((check) => check.status !== "error");
  output.success(
    { healthy, checks },
    checks.map((check) => `[${check.status}] ${check.name}: ${check.message}`).join("\n"),
  );
}

async function agents(
  positionals: readonly string[],
  state: Runtime,
  output: CommandOutput,
): Promise<void> {
  const subcommand = positionals[0];
  if (subcommand === "list") {
    if (positionals.length !== 1) {
      throw new McpcatError(ErrorCode.usage, "用法：mcpcat agents list", ExitCode.usage);
    }
    const defaultAgent = await state.agentStore.getDefault();
    const agents = [...AGENT_ADAPTERS.values()].map((adapter) => ({
      id: adapter.id,
      displayName: adapter.displayName,
      isDefault: adapter.id === defaultAgent,
    }));
    output.success({ defaultAgent: defaultAgent ?? null, agents });
    return;
  }
  if (subcommand === "detect") {
    if (positionals.length !== 1) {
      throw new McpcatError(ErrorCode.usage, "用法：mcpcat agents detect", ExitCode.usage);
    }
    const detections = await Promise.all(
      [...AGENT_ADAPTERS.values()].map(async (adapter) => ({
        agent: adapter.id,
        displayName: adapter.displayName,
        detected: await adapter.detect(state.agentContext),
        userDir: adapter.resolveUserDir(state.agentContext) ?? null,
        projectDir: adapter.resolveProjectDir(state.agentContext) ?? null,
      })),
    );
    output.success({ detections });
    return;
  }
  if (subcommand === "use") {
    const agent = positionals[1];
    if (positionals.length !== 2 || agent === undefined) {
      throw new McpcatError(ErrorCode.usage, "用法：mcpcat agents use <agent>", ExitCode.usage);
    }
    const adapter = getAgentAdapter(assertAgentId(agent));
    await state.agentStore.use(adapter.id);
    output.success({ defaultAgent: adapter.id }, `默认 Agent 已切换为 ${adapter.displayName}`);
    return;
  }
  throw new McpcatError(
    ErrorCode.usage,
    "用法：mcpcat agents detect|list|use",
    ExitCode.usage,
  );
}

export async function runCli(
  args: readonly string[],
  dependencies: CliDependencies = {},
): Promise<number> {
  const state = runtime(dependencies);
  const sensitiveValues: string[] = [];
  let json = args.includes("--json");
  const logger = new SafeLogger(state.stderr, false, sensitiveValues);
  try {
    const parsed = parseArgs(args);
    json = parsed.options.json;
    const output = new CommandOutput(state.stdout, state.stderr, json, sensitiveValues);
    if (parsed.options.version) {
      output.success({ version: CLI_VERSION }, CLI_VERSION);
      return ExitCode.success;
    }
    if (parsed.options.help || parsed.command === undefined) {
      output.success({ version: CLI_VERSION, help: HELP }, HELP);
      return ExitCode.success;
    }
    if (parsed.command === "connect") {
      await connect(parsed.positionals, parsed.options, state, output, logger, sensitiveValues);
    } else if (parsed.command === "profiles") {
      await listProfiles(parsed.positionals, state, output);
    } else if (parsed.command === "use") {
      await useProfile(parsed.positionals, state, output);
    } else if (parsed.command === "agents") {
      await agents(parsed.positionals, state, output);
    } else if (parsed.command === "skills") {
      await skills(parsed.positionals, parsed.options, state, output, sensitiveValues);
    } else if (parsed.command === "doctor") {
      await doctor(parsed.positionals, parsed.options, state, output);
    } else {
      throw new McpcatError(
        ErrorCode.usage,
        `未知命令：${parsed.command}`,
        ExitCode.usage,
      );
    }
    return ExitCode.success;
  } catch (error) {
    const failure = toMcpcatError(error);
    new CommandOutput(state.stdout, state.stderr, json, sensitiveValues).failure(failure);
    return failure.exitCode;
  }
}
