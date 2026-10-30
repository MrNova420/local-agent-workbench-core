import json,unittest
from unittest.mock import patch
from desktop_engine import OllamaRuntime,CONTEXT_MAX
class MetadataTests(unittest.TestCase):
 def test_context_metadata_is_read_without_loading(self):
  runtime=OllamaRuntime();expected={'model_info':{'qwen35.context_length':262144}}
  with patch.object(runtime,'get',return_value=expected) as get:
   self.assertEqual(runtime.model_info('hf-tag'),expected)
   get.assert_called_once_with('/api/show',{'model':'hf-tag'})
if __name__=='__main__':unittest.main()
