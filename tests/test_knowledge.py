import unittest,tempfile
from pathlib import Path
from desktop_engine import AgentEngine, ProjectKnowledge
class KnowledgeTests(unittest.TestCase):
 def test_project_instructions_and_skills_are_discovered(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'AGENTS.md').write_text('Use python');(root/'.agents/skills/test').mkdir(parents=True);(root/'.agents/skills/test/SKILL.md').write_text('skill body')
   k=ProjectKnowledge(root);self.assertIn('Use python',k.instructions());self.assertEqual(k.skills()[0]['name'],'test');self.assertIn('skill body',k.load_skill('test',k.skills()))
 def test_frontmatter_description_is_discoverable_and_invalid_skill_is_ignored(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);good=root/'.opencode/skills/release';good.mkdir(parents=True);(good/'SKILL.md').write_text('---\nname: release\ndescription: Prepare a release and changelog\n---\nSteps here',encoding='utf8')
   (good/'references').mkdir();(good/'references/checklist.md').write_text('release checklist',encoding='utf8')
   bad=root/'.agents/skills/Invalid_Name';bad.mkdir(parents=True);(bad/'SKILL.md').write_text('---\nname: Invalid_Name\ndescription: bad\n---\nno',encoding='utf8')
   k=ProjectKnowledge(root);skills=k.skills([root/'.opencode/skills',root/'.agents/skills']);self.assertEqual([s['name'] for s in skills],['release']);self.assertIn('Prepare a release',k.skill_catalog(skills));self.assertIn('Steps here',k.load_skill('release',skills));self.assertEqual(k.read_skill_resource('release','references/checklist.md',skills),'release checklist')
   with self.assertRaisesRegex(ValueError,'without'):
    k.read_skill_resource('release','../outside.txt',skills)
 def test_skill_source_filter_keeps_disabled_sources_out_of_the_catalog(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);project=root/'.agents/skills/project-check';project.mkdir(parents=True);(project/'SKILL.md').write_text('Project skill body',encoding='utf8')
   user=root/'.codex/skills/user-check';user.mkdir(parents=True);(user/'SKILL.md').write_text('User skill body',encoding='utf8')
   from unittest.mock import patch
   with patch('project_knowledge.Path.home',return_value=root), patch('project_knowledge.GLOBAL_SKILL_ROOTS',('.codex/skills',)):
    knowledge=ProjectKnowledge(root)
    self.assertEqual([row['name'] for row in knowledge.skills(sources={'project'})],['project-check'])
    self.assertEqual([row['name'] for row in knowledge.skills(sources={'user'})],['user-check'])
    self.assertEqual(knowledge.skills(sources=set()),[])
 def test_disabled_skills_are_not_exposed_as_a_model_tool(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);skill=root/'.agents/skills/unsafe';skill.mkdir(parents=True);(skill/'SKILL.md').write_text('Do something',encoding='utf8')
   engine=AgentEngine(root,skills_enabled=False)
   self.assertFalse(any(row['function']['name']=='skill' for row in engine.state['registry'].schemas))
   self.assertEqual(engine.state['registry'].skill_rows,[])
   self.assertIn('skills are disabled',engine.state['registry'].run('skill',{'action':'list'}).lower())
if __name__=='__main__':unittest.main()
