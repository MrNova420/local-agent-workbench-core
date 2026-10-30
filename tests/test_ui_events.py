import unittest
from ui_events import AgentEventRelay, EventBus
class EventTests(unittest.TestCase):
 def test_stream_events_are_drained_in_order(self):
  bus=EventBus();bus.emit({'type':'phase','phase':'waiting'});bus.emit({'type':'text','text':'hi'})
  self.assertEqual([e['type'] for e in bus.drain()],['phase','text']);self.assertEqual(bus.drain(),[])
 def test_terminal_engine_event_is_released_only_after_persistence(self):
  bus=EventBus();relay=AgentEventRelay(bus.emit)
  relay({'type':'cancelled','elapsed':12.5})
  self.assertEqual(bus.drain(),[])
  bus.emit({'type':'session_saved'})
  relay.emit_terminal_after_persist()
  events=bus.drain()
  self.assertEqual([event['type'] for event in events],['session_saved','cancelled'])
if __name__=='__main__':unittest.main()
