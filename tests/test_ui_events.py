import unittest
from ui_events import AgentEventRelay, EventBus
class EventTests(unittest.TestCase):
 def test_stream_events_are_drained_in_order(self):
  bus=EventBus();bus.emit({'type':'phase','phase':'waiting'});bus.emit({'type':'text','text':'hi'})
  self.assertEqual([e['type'] for e in bus.drain()],['phase','text']);self.assertEqual(bus.drain(),[])
 def test_terminal_engine_event_is_released_only_after_persistence(self):
  bus=EventBus();relay=AgentEventRelay(bus.emit)
  terminal={'type':'error','error':'provider failed'}
  relay(terminal)
  self.assertEqual(relay.terminal_event,terminal)
  self.assertEqual(bus.drain(),[])
  bus.emit({'type':'session_saved'})
  relay.emit_terminal_after_persist()
  events=bus.drain()
  self.assertEqual([event['type'] for event in events],['session_saved','error'])
  self.assertIsNone(relay.terminal_event)
 def test_safe_stop_event_is_persisted_before_it_is_released(self):
  bus=EventBus();relay=AgentEventRelay(bus.emit);event={'type':'stopped_after_operation','elapsed':3.2}
  relay(event)
  self.assertEqual(relay.terminal_event,event);self.assertEqual(bus.drain(),[])
  bus.emit({'type':'session_saved'});relay.emit_terminal_after_persist()
  self.assertEqual([row['type'] for row in bus.drain()],['session_saved','stopped_after_operation'])
if __name__=='__main__':unittest.main()
