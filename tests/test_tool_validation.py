import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_engine import AgentEngine, CONTEXT_MAX, _normalize_tool_history, _validate_tool_arguments
from tool_schema_capabilities import unsupported_schema_features


class ToolValidationTests(unittest.TestCase):
    def test_mcp_schema_capability_report_is_recursive_bounded_and_deterministic(self):
        schema={"type":"object","properties":{"settings":{"type":"object","if":{"required":["mode"]},"then":{"properties":{"mode":{"type":"string","format":"date-time"}}}}}}
        report=unsupported_schema_features(schema)
        self.assertEqual(report, sorted(report))
        self.assertTrue(any(".if: unsupported keyword" in item for item in report))
        self.assertFalse(any("format" in item for item in report))
        self.assertIsNone(_validate_tool_arguments({"when":"not-a-date"},{"type":"object","properties":{"when":{"type":"string","format":"date-time"}}}))
        self.assertTrue(unsupported_schema_features({"type":"file"}))
        self.assertTrue(unsupported_schema_features({"$ref":"https://example.test/schema"}))
        deeply_nested={}
        cursor=deeply_nested
        for _ in range(66):
            cursor["properties"]={}
            cursor=cursor["properties"]
        self.assertTrue(any("nesting" in item for item in unsupported_schema_features(deeply_nested)))

    def test_json_schema_2020_conditionals_dependencies_property_names_and_arrays(self):
        conditional={"type":"object","if":{"required":["mode"]},
                    "then":{"required":["payload"]},"else":{"required":["fallback"]}}
        self.assertIsNone(_validate_tool_arguments({"mode":"write","payload":{}},conditional))
        self.assertIn("payload is required",_validate_tool_arguments({"mode":"write"},conditional))
        self.assertIn("fallback is required",_validate_tool_arguments({},conditional))
        dependencies={"type":"object","dependentRequired":{"token":["user"]},
                      "dependentSchemas":{"config":{"required":["enabled"]}},
                      "propertyNames":{"pattern":"^[a-z]+$"}}
        self.assertIsNone(_validate_tool_arguments({"token":"x","user":"kayde","config":{"enabled":True}},dependencies))
        self.assertIn("user is required",_validate_tool_arguments({"token":"x"},dependencies))
        self.assertIn("enabled is required",_validate_tool_arguments({"config":{}},dependencies))
        self.assertIn("property name",_validate_tool_arguments({"BadKey":1},dependencies))
        arrays={"type":"array","prefixItems":[{"type":"string"},{"type":"integer"}],
                "items":{"type":"boolean"},"contains":{"const":"required"},"minContains":1}
        self.assertIsNone(_validate_tool_arguments(["label",2,True,"required"],arrays))
        self.assertIn("must be integer",_validate_tool_arguments(["label","two",True,"required"],arrays))
        self.assertIn("at least 1 item",_validate_tool_arguments(["label",2,True],arrays))

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

    def test_json_schema_validation_covers_strings_arrays_and_object_schemas(self):
        schema={"type":"object","properties":{
            "name":{"type":"string","minLength":2,"maxLength":8,"pattern":"^[a-z]+$"},
            "tags":{"type":"array","minItems":1,"maxItems":3,"uniqueItems":True,"items":{"type":"string"}},
            "metadata":{"type":"object","additionalProperties":{"type":"integer"}},
        },"additionalProperties":False}
        self.assertIsNone(_validate_tool_arguments({"name":"ember","tags":["rye"],"metadata":{"count":2}},schema))
        self.assertIn("at least 2",_validate_tool_arguments({"name":"x","tags":["rye"]},schema))
        self.assertIn("pattern",_validate_tool_arguments({"name":"Ember","tags":["rye"]},schema))
        self.assertIn("unique",_validate_tool_arguments({"name":"ember","tags":["rye","rye"]},schema))
        self.assertIn("must be integer",_validate_tool_arguments({"name":"ember","tags":["rye"],"metadata":{"count":"2"}},schema))

    def test_json_schema_validation_covers_composition_and_rejects_unresolved_refs(self):
        union={"oneOf":[{"type":"string","const":"plan"},{"type":"integer","minimum":1}]}
        self.assertIsNone(_validate_tool_arguments("plan",union))
        self.assertIsNone(_validate_tool_arguments(3,union))
        self.assertIn("exactly one",_validate_tool_arguments(True,union))
        self.assertIn("missing JSON Schema definition",_validate_tool_arguments({}, {"$ref":"#/$defs/args"}))
        self.assertIn("unsupported JSON Schema type",_validate_tool_arguments("x", {"type":"unrecognized"}))

    def test_json_schema_validation_resolves_local_definition_references_only(self):
        schema={"type":"object","$defs":{"name":{"type":"string","minLength":3}},
                "properties":{"name":{"$ref":"#/$defs/name"}},"required":["name"]}
        self.assertIsNone(_validate_tool_arguments({"name":"Ember"},schema))
        self.assertIn("at least 3",_validate_tool_arguments({"name":"Ry"},schema))
        self.assertIn("external",_validate_tool_arguments("x", {"$ref":"https://example.com/schema.json"}))

    def test_json_schema_uses_json_type_equality_and_fails_closed_on_unknown_keywords(self):
        self.assertIsNone(_validate_tool_arguments(2.0,{"type":"integer"}))
        self.assertIn("constant",_validate_tool_arguments(True,{"const":1}))
        self.assertIn("one of",_validate_tool_arguments(True,{"enum":[1]}))
        self.assertIn("unsupported JSON Schema keyword",_validate_tool_arguments({"name":"x"},{"type":"object","propertyNames":{"type":"string"}}))
        self.assertIn("unique",_validate_tool_arguments([1,1.0],{"type":"array","uniqueItems":True}))
        self.assertIn("finite JSON number",_validate_tool_arguments(float("nan"),{"type":"number"}))

    def test_invalid_file_tool_arguments_never_reach_the_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);engine=AgentEngine(root,context=CONTEXT_MAX,access="edit");responses=[
                {"message":{"role":"assistant","tool_calls":[{"id":"call-1","function":{"name":"write_file","arguments":{"path":"x.txt","content":"must not write","extra":True}}}]}},
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
            engine=AgentEngine(Path(folder),context=CONTEXT_MAX,access="edit")
            first=Response({"role":"assistant","tool_calls":[{"id":"provider-call-id","function":{"name":"write_file","arguments":{"path":"result.txt","content":"created"}}}]})
            second=Response({"role":"assistant","content":"File created."})
            with patch("desktop_engine.open_http_stream",side_effect=[first,second]) as open_url:
                engine.run("Check the second-request tool result transcript.")
            sent=json.loads(open_url.call_args_list[1].args[0].data)
            assistant=next(message for message in sent["messages"] if message.get("role")=="assistant" and message.get("tool_calls"))
            tool=next(message for message in sent["messages"] if message.get("role")=="tool")
            self.assertEqual(assistant["tool_calls"][0]["id"],"provider-call-id")
            self.assertEqual(tool,{"role":"tool","tool_name":"write_file","content":"Wrote result.txt"})


if __name__ == "__main__":
    unittest.main()
