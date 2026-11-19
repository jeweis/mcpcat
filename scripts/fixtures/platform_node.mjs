// 使用真实 Node 进程的旧协议夹具；不下载 SDK，不访问外部服务。
import readline from 'node:readline';
for await (const line of readline.createInterface({input: process.stdin})) {
  const request = JSON.parse(line);
  if (!('id' in request)) continue;
  let result;
  if (request.method === 'server/discover') {
    console.log(JSON.stringify({jsonrpc: '2.0', id: request.id, error: {code: -32601, message: 'legacy'}}));
    continue;
  }
  if (request.method === 'initialize') result = {protocolVersion: '2025-11-25', capabilities: {tools: {}}, serverInfo: {name: 'node-fixture', version: '1'}};
  else if (request.method === 'tools/list') result = {tools: [{name: 'echo', inputSchema: {type: 'object', properties: {message: {type: 'string'}}}}]};
  else if (request.method === 'tools/call') {
    const value = {message: request.params?.arguments?.message, pid: process.pid};
    result = {content: [{type: 'text', text: JSON.stringify(value)}], structuredContent: value};
  } else result = {};
  console.log(JSON.stringify({jsonrpc: '2.0', id: request.id, result}));
}
