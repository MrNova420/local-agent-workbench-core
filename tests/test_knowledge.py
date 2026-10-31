import unittest,tempfile
from pathlib import Path
from project_knowledge import ProjectKnowledge
class KnowledgeTests(unittest.TestCase):
 def test_project_instructions_and_skills_are_discovered(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'AGENTS.md').write_text('Use python');(root/'.agents/skills/test').mkdir(parents=True);(root/'.agents/skills/test/SKILL.md').write_text('skill body')
   k=ProjectKnowledge(root);self.assertIn('Use python',k.instructions());self.assertEqual(k.skills()[0]['name'],'test');self.assertIn('skill body',k.load_skill('test',k.skills()))
 def test_frontmatter_description_is_discoverable_and_invalid_skill_is_ignored(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);good=root/'.opencode/skills/release';good.mkdir(parents=True);(good/'SKILL.md').write_text('---\nname: release\ndescription: Prepare a release and changelog\n---\nSteps here',encoding='utf8')
   bad=root/'.agents/skills/Invalid_Name';bad.mkdir(parents=True);(bad/'SKILL.md').write_text('---\nname: Invalid_Name\ndescription: bad\n---\nno',encoding='utf8')
   k=ProjectKnowledge(root);skills=k.skills();self.assertEqual([s['name'] for s in skills],['release']);self.assertIn('Prepare a release',k.skill_catalog(skills));self.assertIn('Steps here',k.load_skill('release',skills))
if __name__=='__main__':unittest.main()
