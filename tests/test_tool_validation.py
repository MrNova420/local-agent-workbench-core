import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_engine import AgentEngine, _normalize_tool_history, _validate_tool_arguments


class ToolValidationTests(unittest.TestCase):
    def test_legacy_saved_tool_result_is_migrated_to_ollama_protocol(self):
        messages=[{"role":"assistant","tool_calls":[{"id":"old-call","function":{"name":"read_file","arguments":{"path":"a.py"}}}]},
                  {"role":"tool","tool_call_id":"old-call","content":"file body"}]
        _normalize_tool_history(messages)
        self.assertEqual(messages[1],{"role":"tool","tool_name":"read_file","content":"file body"})

    def test_json_schema_subset_checks_nested_types_bounds_and_extra_fields(self):
        schema={"type":"object","required":["items"],"additionalProperties":False,
                "properties":{"items":{"type":"array","items":{"type":"object"}},
                              "limit":{"type":"integer","minimum":1,"maximum":5}}}
        self.assertIsNone(_validate_tool_arguments({"items":[{}],"limit":5},schema))
        self.assertIn("at most 5",_validate_tool_arguments({"items":[],"limit":6},schema))
        self.assertIn("must be integer",_validate_tool_arguments({"items":[],"limit":True},schema))
        self.assertIn("not an accepted argument",_validate_tool_arguments({"items":[],"surprise":1},schema))

    def test_invalid_file_tool_arguments_never_reach_the_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);engine=AgentEngine(root,context=8192,max_tokens=128,access="edit");responses=[
                {"message":{"role":"assistant","tool_calls":[{"id":"call-1","function":{"name":"write_file","arguments":{"path":"x.txt","content":"must not write","extra":true}}}]}},
                {"message":{"role":"assistant","content":"The invalid arguments were rejected."}},
            ]
            engine.runtime.stream=lambda *args,**kwargs:responses.pop(0)
            history=engine.run("Check whether tool validation blocks malformed arguments.")
            self.assertFalse((root/"x.txt").exists())
            result=next(item["content"] for item in history if item.get("role")=="tool")
            self.assertIn("not an accepted argument",result)

    def test_ollama_second_request_uses_documented_tool_name_result(self):
        class Response:
            def __init__(self,message):self.lines=iter([(json.dumps({"message":message,"done":True})+"\n").encode()])
            def readline(self):return next(self.lines,b"")
            def close(self):pass
        with tempfile.TemporaryDirectory() as folder:
            engine=AgentEngine(Path(folder),context=8192,max_tokens=128,access="edit")
            first=Response({"role":"assistant","tool_calls":[{"id":"provider-call-id","function":{"name":"write_file","arguments":{"path":"result.txt","content":"created"}}}]})
            second=Response({"role":"assistant","content":"File created."})
            with patch("urllib.request.urlopen",side_effect=[first,second]) as open_url:
                engine.run("Check the second-request tool result transcript.")
            sent=json.loads(open_url.call_args_list[1].args[0].data)
            assistant=next(message for message in sent["messages"] if message.get("role")=="assistant" and message.get("tool_calls"))
            tool=next(message for message in sent["messages"] if message.get("role")=="tool")
            self.assertEqual(assistant["tool_calls"][0]["id"],"provider-call-id")
            self.assertEqual(tool,{"role":"tool","tool_name":"write_file","content":"Wrote result.txt"})


if __name__ == "__main__":
    unittest.main()
