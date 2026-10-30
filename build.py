"""Build script for a clean Windows Python/Tkinter distribution."""
from __future__ import annotations
import argparse, shutil, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def main():
 p=argparse.ArgumentParser();p.add_argument('--onefile',action='store_true',help='Create a standalone single-file exe with PyInstaller');p.add_argument('--install-mcp',action='store_true',help='Install official MCP SDK into current Python');a=p.parse_args()
 if a.install_mcp:subprocess.run([sys.executable,'-m','pip','install','-r',str(ROOT/'requirements-mcp.txt')],check=True)
 if a.onefile:
  subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed','--name','LocalAgentDesk',str(ROOT/'app.py')],cwd=ROOT,check=True)
  print(ROOT/'dist'/'LocalAgentDesk.exe')
 else:print('Run: python app.py   |   Optional MCP: python build.py --install-mcp')
if __name__=='__main__':main()
