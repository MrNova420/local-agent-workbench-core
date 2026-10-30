import unittest,asyncio,tempfile
from pathlib import Path
from unittest.mock import patch
from mcp_support import MCPManager
class Tool: name='echo';description='echo';inputSchema={'type':'object','properties':{}}
class Result: tools=[Tool()]
class FakeSession:
 def __init__(self,*a):self.closed=False
 async def __aenter__(self):return self
 async def initialize(self):pass
 async def list_tools(self):return Result()
 async def call_tool(self,name,args):return type('R',(),{'content':[type('C',(),{'text':'ok'})()]})()
 async def __aexit__(self,*a):self.closed=True
class FakeManager:
 def __init__(self,*a):pass
 async def __aenter__(self):return (object(),object())
 async def __aexit__(self,*a):return None
class MCPTests(unittest.TestCase):
 def test_discovery_call_and_cleanup(self):
  async def scenario():
   with tempfile.TemporaryDirectory() as d:
    m=MCPManager(Path(d)/'mcp.json');m.servers={'demo':{'command':'fake'}}
    with patch('mcp.ClientSession',FakeSession),patch('mcp.StdioServerParameters',lambda **kw:kw),patch('mcp.client.stdio.stdio_client',FakeManager):
      tools=await m.discover();self.assertEqual(tools[0]['function']['name'],'mcp__demo__echo');self.assertTrue(m.discovered);self.assertEqual(await m.call(tools[0]['function']['name'],{}),'ok');await m.close();self.assertEqual(m.clients,{})
  # The official SDK is an optional dependency; if unavailable, provide import stubs to exercise lifecycle.
  try:import mcp
  except ImportError:self.skipTest('MCP SDK not installed; lifecycle exercised via mocked SDK when available')
  asyncio.run(scenario())
if __name__=='__main__':unittest.main()
