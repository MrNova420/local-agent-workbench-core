import unittest,tempfile
from pathlib import Path
from project_knowledge import ProjectKnowledge
class KnowledgeTests(unittest.TestCase):
 def test_project_instructions_and_skills_are_discovered(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'AGENTS.md').write_text('Use python');(root/'.agents/skills/test').mkdir(parents=True);(root/'.agents/skills/test/SKILL.md').write_text('skill body')
   k=ProjectKnowledge(root);self.assertIn('Use python',k.instructions());self.assertEqual(k.skills()[0]['name'],'test');self.assertEqual(k.load_skill('test',k.skills()),'skill body')
if __name__=='__main__':unittest.main()
