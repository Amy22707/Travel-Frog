# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os

project = Path(SPECPATH).parent
edition = os.environ.get('FROG_BUILD_EDITION', 'normal')
name = 'FrogGallery' if edition == 'gallery' else 'FrogNormal'
data_files = [(str(p), 'server/config') for p in (project / 'server/config').glob('*.json')
              if p.name != 'album_official_full_20260912.json']
data_files += [(str(project / 'server/offline_policy.json'), 'server'),
               (str(project / 'client/config/config.eab'), 'client/config')]

launcher = Analysis([str(project / 'desktop' / (edition + '_entry.py'))], pathex=[str(project)],
                    binaries=[], datas=data_files, hiddenimports=[], hookspath=[],
                    excludes=[], noarchive=False)
server = Analysis([str(project / 'desktop/server_entry.py')], pathex=[str(project)],
                  binaries=[], datas=data_files, hiddenimports=[], hookspath=[],
                  excludes=['tkinter'], noarchive=False)

launcher_exe = EXE(PYZ(launcher.pure), launcher.scripts, [], exclude_binaries=True,
                   name=name, icon=str(project / '.build/icon.ico'), console=False, debug=False)
server_exe = EXE(PYZ(server.pure), server.scripts, [], exclude_binaries=True,
                name='FrogServer', icon=str(project / '.build/icon.ico'), console=True, debug=False)
release = COLLECT(launcher_exe, server_exe, launcher.binaries, launcher.datas,
                  server.binaries, server.datas, name='TravelFrogGallery' if edition == 'gallery' else 'TravelFrogNormal')
