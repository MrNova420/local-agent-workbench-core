import asyncio, json, os
from pathlib import Path
from typing import Any

class MCPManager:
 def __init__(self,path:Path|str):self.path=Path(path);self.servers={};self.clients={};self.tools={}
 def load(self):
  if self.path.exists():self.servers=json.loads(self.path.read_text(encoding='utf8')).get('mcpServers',{})
  return self.servers
 def save(self):self.path.parent.mkdir(parents=True,exist_ok=True);self.path.write_text(json.dumps({'mcpServers':self.servers},indent=2),encoding='utf8')
 async def discover(self):
  try:
   from mcp import ClientSession,StdioServerParameters
   from mcp.client.stdio import stdio_client
  except ImportError as e:raise RuntimeError('Optional MCP SDK missing; install requirements-mcp.txt') from e
  found=[]
  for name,cfg in self.servers.items():
   if not isinstance(cfg,dict) or not cfg.get('command'):continue
   manager=stdio_client(StdioServerParameters(command=cfg['command'],args=cfg.get('args',[]),env={**os.environ,**cfg.get('env',{})}))
   streams=await manager.__aenter__();session=ClientSession(*streams);await session.__aenter__();await session.initialize();response=await session.list_tools()
   self.clients[name]=(session,manager)
   for tool in response.tools:
    key=f'mcp__{name}__{tool.name}';self.tools[key]=(name,tool.name)
    found.append({'type':'function','function':{'name':key,'description':(tool.description or '')+' (local MCP tool; requires approval)','parameters':tool.inputSchema or {'type':'object','properties':{}}}})
  return found
 async def call(self,name:str,args:dict[str,Any]):
  server,tool=self.tools[name];result=await self.clients[server][0].call_tool(tool,args)
  return '\n'.join(c.text for c in result.content if getattr(c,'text',None)) or json.dumps([getattr(c,'model_dump',lambda:{})() for c in result.content])
 async def close(self):
  for session,manager in list(self.clients.values()):
   try:await session.__aexit__(None,None,None)
   finally:await manager.__aexit__(None,None,None)
  self.clients.clear();self.tools.clear()
 def run(self,awaitable):return asyncio.run(awaitable)
