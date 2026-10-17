"""本机真实浏览器夹具，不接入用户数据库或当前服务。"""

import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from app.services.app_asset_resolver import AppAssetResolver
from app.services.app_html_assembly import assemble_app_html

CSP = "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'"


def fixture_html(case: str) -> str:
    """固定测试脚本仅由客户端执行，服务器只装配字节。"""
    raw = 'const raw=String.raw`中文 </script> 😀`; document.getElementById("result").textContent=raw;'
    files = {
        "raw.js": raw.encode(),
        "first.js": b"window.fixtureOrder=['first'];",
        "second.js": b"window.fixtureOrder.push('second');",
        "third.js": b"window.fixtureOrder.push('third');document.getElementById('result').textContent=window.fixtureOrder.join(',')+'; '+getComputedStyle(document.getElementById('result')).color+'; '+document.currentScript.id;",
        "style.css": b"#result {color:rgb(0,128,0)}",
    }
    if case == "raw":
        body = '<script src="raw.js"></script>'
    elif case == "ordered":
        body = '<link rel="stylesheet" href="style.css"><script src="first.js"></script><script src="second.js"></script><script id="third-script" src="third.js"></script>'
    elif case == "responsive":
        body, images = responsive_case()
        files.update(images)
    elif case == "timing":
        body, scripts = timing_case()
        files.update(scripts)
    elif case in {"modules", "modules-js", "modules-v5"}:
        body, scripts = module_case()
        if case == "modules-js":
            scripts["js/lazy.js"] = b"export const value=7;"
        elif case == "modules-v5":
            body, scripts = advanced_module_case()
        files.update(scripts)
    else:
        raise ValueError("Unknown fixture case")
    html = (
        '<!DOCTYPE html><html><head><title>Apps assembly fixture</title></head><body><p id="result">Waiting</p>'
        + body
        + "</body></html>"
    )
    return assemble_app_html(
        html,
        resolver=AppAssetResolver(files, []),
        entrypoint="index.html",
        native_scripts=case in {"timing", "modules", "modules-js", "modules-v5"},
        module_scripts=case in {"modules", "modules-js", "modules-v5"},
        module_version=5 if case == "modules-v5" else 4,
    )


def module_case() -> tuple[str, dict[str, bytes]]:
    """真实浏览器验证循环、共享模块、动态 import、JSON 和附件。"""
    files = {
        "js/main.js": b"""import {a} from "./a.js"; import {b} from "./b.js"; import "./copy-a.js"; import "./copy-b.js";
const lazy=await import("./lazy.js");
const image=new Image(); image.src=new URL("../icon.svg",import.meta.url).href;
await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=reject;});
document.getElementById("result").textContent="module="+lazy.value+"; cycle="+(a()+b)+"; count="+window.fixtureCount+"; copies="+window.fixtureCopies+"; asset="+(image.naturalWidth===8)+"; meta="+import.meta.url.startsWith("mcpcat-app://");""",
        "js/a.js": b'import "./shared.js"; import {b} from "./b.js"; export function a(){return b+1;}',
        "js/b.js": b'import "./shared.js"; import {a} from "./a.js"; export const b=2;',
        "js/shared.js": b"window.fixtureCount=(window.fixtureCount||0)+1;",
        "js/copy-a.js": b"window.fixtureCopies=(window.fixtureCopies||0)+1;",
        "js/copy-b.js": b"window.fixtureCopies=(window.fixtureCopies||0)+1;",
        "js/lazy.js": b'import data from "../data.json" with {type:"json"}; export const value=data.value;',
        "data.json": b'{"value":7}',
        "icon.svg": b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"/>',
    }
    return (
        '<link rel="modulepreload" href="js/main.js"><script type="module" src="js/main.js"></script>',
        files,
    )


def responsive_case() -> tuple[str, dict[str, bytes]]:
    """浏览器自行按 DPR/sizes 选择候选，所有图片都来自包内字节。"""
    files = {
        f"{size}.svg": f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}"><rect width="100%" height="100%" fill="green"/></svg>'.encode()
        for size in (48, 96)
    }
    body = (
        '<img id="density" alt="density asset" srcset="48.svg 1x, 96.svg 2x" width="48">'
        '<picture><source srcset="48.svg 48w, 96.svg 96w" sizes="48px">'
        '<img id="width" alt="width asset" src="48.svg" width="48"></picture>'
        '<script>window.addEventListener("load",()=>{'
        'const images=[document.getElementById("density"),document.getElementById("width")];'
        'document.getElementById("result").textContent=images.map(image=>'
        'image.id+": loaded="+(image.complete&&image.naturalWidth>0)+"; data="+'
        'image.currentSrc.startsWith("data:image/svg+xml;base64,")).join(" | ");});</script>'
    )
    return body, files


def advanced_module_case() -> tuple[str, dict[str, bytes]]:
    """固定 v5 夹具：计算表达式只求值一次，映射/参数不串模块。"""
    _, files = module_case()
    files.update(
        {
            "js/main.js": b"""import chosen from "chosen"; import {a} from "./a.js";import {b} from "./b.js";
const path="./lazy.js?first#fragment"; const first=await import(path);
const again=await import(`${"./lazy.js"}?first#fragment`);const second=await import("./lazy.js?second");
const scoped=await import("./scoped/entry.js");const prefix=await import("pkg/one.js");
const data=await import("../data.json",{with:{type:"json"}});
let calls=0; const once=await import((()=>{calls++;return "./one.js";})());
const nested=await import((await import("./chooser.js")).path);
const queryPath="./query.js?scope=test#fragment";const query=await import(queryPath);
let coercions=0;const objectModule=await import({toString(){coercions++;return "./one.js";},valueOf(){throw new Error("wrong coercion");}});
const icon="../icon.svg";const image=new Image();image.src=new URL(icon,import.meta.url).href;
await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=reject;});
let denied=0;for(const name of ["./missing.js","blocked","https://unreviewed.example/module.js"]){try{await import(name);}catch{denied++;}}
let isolated="top";if(window.parent!==window){try{window.parent.document;isolated=false;}catch{isolated=true;}}
document.getElementById("result").textContent="computed="+data.default.value+"; variants="+first.n+","+second.n+"; same="+(first===again)+"; scoped="+scoped.staticValue+","+scoped.dynamicValue+"; prefix="+prefix.default+"; asset="+(image.naturalWidth===8)+"; once="+calls+"; nested="+nested.default+"; denied="+(denied===3)+"; meta="+first.url.endsWith("?first#fragment")+"; cycle="+(a()+b)+"; chosen="+chosen+"; queryScope="+query.staticValue+","+query.dynamicValue+"; coercion="+coercions+"; sandbox="+isolated;""",
            "js/lazy.js": b"window.variants=(window.variants||0)+1;export const n=window.variants;export const url=import.meta.url;",
            "js/one.js": b"export default 1;",
            "js/two.js": b"export default 2;",
            "js/scoped/entry.js": b'import value from "chosen";const name="chosen";export const staticValue=value;export const dynamicValue=(await import(name)).default;',
            "js/chooser.js": b'export const path="./one.js";',
            "js/query.js": b'import value from "chosen";const name="chosen";export const staticValue=value;export const dynamicValue=(await import(name)).default;',
        }
    )
    body = '<script type="importmap">{"imports":{"chosen":"./js/one.js","pkg/":"./js/","blocked":null},"scopes":{"./js/scoped/":{"chosen":"./js/two.js"},"./js/query.js?scope=test":{"chosen":"./js/two.js"}}}</script><link rel="modulepreload" href="js/main.js?entry=v5#root"><script type="module" src="js/main.js?entry=v5#root"></script>'
    return body, files


def timing_case() -> tuple[str, dict[str, bytes]]:
    """真实 parser-inserted defer/async，不用定时器模拟 DOM 生命周期。"""
    files = {
        "one.js": b"var shared=7;window.order.push('one');window.interactive=document.readyState==='interactive';window.late=!!document.getElementById('late');window.scriptID=document.currentScript.id;",
        "two.js": b"window.order.push('two');window.sharedOK=shared===7;",
        "async.js": b"window.asyncDone=true;",
    }
    body = (
        '<script>window.order=[];document.addEventListener("DOMContentLoaded",()=>{'
        'window.dclOK=window.order.join(",")==="one,two";});'
        'window.addEventListener("load",()=>{document.getElementById("result").textContent='
        '"order="+window.order.join(",")+"; interactive="+window.interactive+'
        '"; late="+window.late+"; global="+window.sharedOK+"; dcl="+window.dclOK+'
        '"; async="+window.asyncDone+"; onload="+window.asyncLoaded+"; id="+window.scriptID;});</script>'
        '<script id="defer-one" defer src="one.js"></script>'
        '<script defer src="two.js"></script>'
        '<script async src="async.js" onload="window.asyncLoaded=true"></script>'
        '<div id="late">Parsed after scripts</div>'
    )
    return body, files


class FixtureHandler(BaseHTTPRequestHandler):
    """仅本机静态测试页，没有上传或管理操作。"""

    def do_GET(self) -> None:
        """以与默认 UI CSP 相同的脚本/样式限制交付夹具。"""
        case = parse_qs(urlsplit(self.path).query).get("case", ["raw"])[0]
        try:
            html = fixture_html("modules-v5" if case == "modules-v5-sandbox" else case)
            if case == "modules-v5-sandbox":
                html = (
                    '<!doctype html><html><body><h1>Opaque App sandbox</h1><iframe title="Module sandbox" sandbox="allow-scripts" style="width:100%;height:240px" srcdoc="'
                    + escape(html, quote=True)
                    + '"></iframe></body></html>'
                )
            content = html.encode()
        except ValueError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        policy = (
            CSP.replace(
                "script-src 'self' 'unsafe-inline'",
                "script-src 'self' 'unsafe-inline' data:",
            )
            if case
            in {"timing", "modules", "modules-js", "modules-v5", "modules-v5-sandbox"}
            else CSP
        )
        if case == "modules-v5-sandbox":
            policy += "; frame-src 'self'"
        self.send_header("Content-Security-Policy", policy)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18763)
    arguments = parser.parse_args()
    HTTPServer(("127.0.0.1", arguments.port), FixtureHandler).serve_forever()
