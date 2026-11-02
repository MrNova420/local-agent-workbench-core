import unittest,asyncio,tempfile,os,json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
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

 def test_discovery_exposes_unsupported_schema_features_to_the_model_and_catalog(self):
  async def scenario():
   m=MCPManager(Path('unused.json'));m.servers={'demo':{'command':'fake'}}
   unsupported=SimpleNamespace(name='schedule',description='create a schedule',inputSchema={
       'type':'object','properties':{'when':{'type':'string','x-customConstraint':True}}})
   class SchemaSession(FakeSession):
    async def list_tools(self):return SimpleNamespace(tools=[unsupported])
   with patch('mcp.ClientSession',SchemaSession),patch('mcp.StdioServerParameters',lambda **kw:kw),patch('mcp.client.stdio.stdio_client',FakeManager):
    tools=await m.discover()
   name='mcp__demo__schedule'
   self.assertIn(name,m.schema_warnings)
   self.assertIn('x-customConstraint',tools[0]['function']['description'])
   self.assertEqual(tools[0]['function']['parameters'],unsupported.inputSchema)
   await m.close()
  try:import mcp
  except ImportError:self.skipTest('MCP SDK not installed; discovery schema reporting is exercised via mocked SDK when available')
  asyncio.run(scenario())

 def test_remote_streamable_http_resolves_env_backed_headers_without_persisting_secrets(self):
  async def scenario():
   m=MCPManager(Path('unused.json'));m.servers={'remote':{'transport':'streamable-http','url':'https://tools.example.test/mcp','headers':{'Authorization':'Bearer ${ENV:WORKBENCH_MCP_TOKEN}'}}}
   captured={}
   class RemoteManager:
    async def __aenter__(self):return (object(),object())
    async def __aexit__(self,*_):pass
   class FakeHttpClient:
    def __init__(self,**kwargs):self.headers=kwargs['headers']
    async def __aenter__(self):return self
    async def __aexit__(self,*_):pass
   class RemoteSession(FakeSession):
    async def list_tools(self):return SimpleNamespace(tools=[SimpleNamespace(name='search',description='search docs',inputSchema={'type':'object','properties':{}})])
   def transport(url,**kwargs):captured.update({'url':url,**kwargs});return RemoteManager()
   with patch.dict(os.environ,{'WORKBENCH_MCP_TOKEN':'test-token'}),patch('httpx.AsyncClient',FakeHttpClient),patch('mcp.ClientSession',RemoteSession),patch('mcp.client.streamable_http.streamable_http_client',transport):
    tools=await m.discover()
   self.assertEqual(tools[0]['function']['name'],'mcp__remote__search')
   self.assertEqual(captured['http_client'].headers['Authorization'],'Bearer test-token')
   self.assertNotIn('test-token',json.dumps(m.servers))
   await m.close()
  try:import mcp
  except ImportError:self.skipTest('MCP SDK not installed')
  asyncio.run(scenario())

 def test_remote_cleartext_http_rejects_non_loopback_host(self):
  async def scenario():
   m=MCPManager(Path('unused.json'));m.servers={'remote':{'transport':'streamable-http','url':'http://remote.example.test/mcp'}}
   self.assertEqual(await m.discover(),[])
   self.assertIn('HTTPS',m.errors['remote'])
  asyncio.run(scenario())
 def test_failed_discovery_is_retryable_and_refresh_closes_stale_clients(self):
  async def scenario():
   m=MCPManager(Path('unused.json'));m.servers={'demo':{'command':'fake'}}
   attempts={'initialize':0};sessions=[]
   class FlakySession(FakeSession):
    def __init__(self,*a):super().__init__(*a);sessions.append(self)
    async def initialize(self):
     attempts['initialize']+=1
     if attempts['initialize']==1:raise ConnectionError('server starting')
   with patch('mcp.ClientSession',FlakySession),patch('mcp.StdioServerParameters',lambda **kw:kw),patch('mcp.client.stdio.stdio_client',FakeManager):
    self.assertEqual(await m.discover(),[])
    self.assertTrue(m.needs_discovery)
    tools=await m.discover()
    self.assertEqual([tool['function']['name'] for tool in tools],['mcp__demo__echo'])
    self.assertFalse(m.needs_discovery)
    self.assertTrue(sessions[0].closed)
    self.assertIn('demo',m.clients)
    m.servers={'other':{'command':'fake'}}
    tools=await m.discover()
    self.assertEqual([tool['function']['name'] for tool in tools],['mcp__other__echo'])
    self.assertTrue(sessions[1].closed)
    self.assertEqual(set(m.tools),{'mcp__other__echo'})
    await m.close()
  try:import mcp
  except ImportError:self.skipTest('MCP SDK not installed; retry lifecycle requires the optional SDK interfaces')
  asyncio.run(scenario())

 def test_failed_dispatched_mcp_call_marks_server_for_next_task_reconnect(self):
  async def scenario():
   m=MCPManager(Path('unused.json'));m.tools={'mcp__demo__echo':('demo','echo')}
   class BrokenSession:
    async def call_tool(self,*_):raise ConnectionError('pipe closed')
   m.clients={'demo':(BrokenSession(),object())};m.discovered=True
   with self.assertRaisesRegex(Exception,'server-side effects may have occurred'):
    await m.call('mcp__demo__echo',{})
   self.assertTrue(m.needs_discovery)
   self.assertIn('demo',m.errors)
  asyncio.run(scenario())

 def test_invalid_mcp_configuration_does_not_retry_connecting_on_every_task(self):
  async def scenario():
   m=MCPManager(Path('unused.json'));m.servers={'bad':{'transport':'unknown'}}
   self.assertEqual(await m.discover(),[])
   self.assertIn('Unsupported MCP transport',m.errors['bad'])
   self.assertFalse(m.needs_discovery)
  try:import mcp
  except ImportError:self.skipTest('MCP SDK not installed; configuration retry state requires its optional interface')
  asyncio.run(scenario())

 def test_close_attempts_every_mcp_client_even_if_one_exit_raises(self):
  async def scenario():
   m=MCPManager(Path('unused.json'));closed=[]
   class Session:
    def __init__(self,name,fail=False):self.name=name;self.fail=fail
    async def __aexit__(self,*_):
     closed.append('session:'+self.name)
     if self.fail:raise RuntimeError('session close failed')
   class Transport:
    def __init__(self,name):self.name=name
    async def __aexit__(self,*_):closed.append('transport:'+self.name)
   m.clients={'bad':(Session('bad',True),Transport('bad')),'good':(Session('good'),Transport('good'))}
   m.tools={'mcp__bad__write':('bad','write'),'mcp__good__read':('good','read')};m.discovered=True
   with self.assertRaisesRegex(RuntimeError,'1 MCP client connection failed to close'):
    await m.close()
   self.assertEqual(set(closed),{'session:bad','transport:bad','session:good','transport:good'})
   self.assertEqual(m.clients,{})
   self.assertEqual(m.tools,{})
   self.assertFalse(m.discovered)
  asyncio.run(scenario())

 def test_run_closes_coroutine_if_mcp_loop_cannot_start(self):
  m=MCPManager(Path('unused.json'))
  async def pending():return None
  awaitable=pending()
  with patch.object(m,'_ensure_loop',side_effect=RuntimeError('event loop unavailable')):
   with self.assertRaisesRegex(RuntimeError,'event loop unavailable'):
    m.run(awaitable)
  self.assertIsNone(awaitable.cr_frame)

if __name__=='__main__':unittest.main()
