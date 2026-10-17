// 固定客户端装载器；只在 App sandbox 运行，不是服务端 JS 执行器。
function context(base) {
  const identity = new URL(base).hostname;
  const element = document.querySelector(`script[data-mcpcat-module-config="${identity}"]`);
  if (!element) throw new TypeError("App module configuration unavailable");
  return JSON.parse(element.textContent);
}

function external(value, config) {
  const url = new URL(value);
  if (url.protocol === "data:") return url.href;
  if (url.protocol !== "https:" || url.username || url.password || !config.resourceDomains.includes(url.origin)) {
    throw new TypeError("App external module/resource origin not reviewed");
  }
  return url.href;
}

function normalized(value, base, config) {
  if (/[\s\\\u0000-\u001f]/u.test(value)) throw new TypeError("Invalid App module path");
  if (value.startsWith(".") || value.startsWith("/") || /^[A-Za-z][A-Za-z0-9+.-]*:/u.test(value)) {
    const url = new URL(value, base);
    return url.href.startsWith(config.prefix) ? url.href : external(url.href, config);
  }
  return value;
}

function match(value, entries) {
  if (Object.hasOwn(entries, value)) return { matched: true, value: entries[value] };
  for (const key of Object.keys(entries).sort((a, b) => b.length - a.length)) {
    if (!key.endsWith("/") || !value.startsWith(key)) continue;
    const target = entries[key];
    if (target === null) return { matched: true, value: null };
    const result = new URL(value.slice(key.length), target).href;
    if (!result.startsWith(target)) throw new TypeError("App import map prefix traversal");
    return { matched: true, value: result };
  }
  return { matched: false };
}

function resolve(value, base, config) {
  const key = normalized(value, base, config);
  const scopes = Object.keys(config.maps.scopes).sort((a, b) => b.length - a.length);
  const mappings = scopes.filter(scope => base.startsWith(scope)).map(scope => config.maps.scopes[scope]);
  mappings.push(config.maps.imports);
  for (const entries of mappings) {
    const result = match(key, entries);
    if (!result.matched) continue;
    if (result.value === null) throw new TypeError("App import map blocks this module");
    return result.value;
  }
  if (key.startsWith(config.prefix) || /^[A-Za-z][A-Za-z0-9+.-]*:/u.test(key)) return key;
  throw new TypeError("App module specifier not mapped");
}

function clean(value) {
  const url = new URL(value);
  url.search = "";
  url.hash = "";
  return url.href;
}

function moduleTarget(value, config) {
  const key = Object.keys(config.modules).filter(uri => value.startsWith(uri) && clean(uri) === clean(value)).sort((a, b) => b.length - a.length)[0];
  const mapped = config.modules[value] ?? config.modules[key] ?? config.modules[clean(value)];
  if (!mapped) throw new TypeError("App package module unavailable");
  // query/fragment 不改源码，但必须保留为不同的浏览器模块实例。
  return mapped.split("#", 1)[0] + "#" + encodeURIComponent(value);
}

function jsonOptions(options) {
  if (options === undefined) return undefined;
  if (options === null || typeof options !== "object") throw new TypeError("Invalid import options");
  const attributes = options.with;
  if (attributes === undefined) return undefined;
  if (attributes === null || typeof attributes !== "object" ||
      Object.keys(attributes).length !== 1 || attributes.type !== "json") {
    throw new TypeError("App JSON import supports only type: json");
  }
  return undefined;
}

export async function importModule(base, specifier, options) {
  const config = context(base);
  if (typeof specifier === "symbol") throw new TypeError("Invalid module specifier");
  const value = resolve(String(specifier), base, config);
  if (!value.startsWith(config.prefix)) return import(external(value, config), options);
  const target = moduleTarget(value, config);
  const isJSON = new URL(value).pathname.toLowerCase().endsWith(".json");
  return import(target, isJSON ? jsonOptions(options) : options);
}

export function assetURL(base, reference) {
  const config = context(base);
  if (typeof reference === "symbol") throw new TypeError("Invalid resource reference");
  const value = normalized(String(reference), base, config);
  if (!value.startsWith(config.prefix)) return external(value, config);
  const asset = config.assets[clean(value)];
  if (!asset) throw new TypeError("App package asset unavailable");
  return asset + new URL(value).hash;
}
