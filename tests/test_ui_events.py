import unittest
from ui_events import EventBus
class EventTests(unittest.TestCase):
 def test_stream_events_are_drained_in_order(self):
  bus=EventBus();bus.emit({'type':'phase','phase':'waiting'});bus.emit({'type':'text','text':'hi'})
  self.assertEqual([e['type'] for e in bus.drain()],['phase','text']);self.assertEqual(bus.drain(),[])
if __name__=='__main__':unittest.main()
