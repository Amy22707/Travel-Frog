# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project = Path(SPECPATH).parent
data_files = [(str(p), 'server/config') for p in (project / 'server/config').glob('*.json')
              if p.name != 'album_official_full_20260912.json']
data_files += [(str(project / 'server/offline_policy.json'), 'server'),
               (str(project / 'client/config/config.eab'), 'client/config')]

launcher = Analysis([str(project / 'desktop/launcher.py')], pathex=[str(project)],
                    binaries=[], datas=data_files, hiddenimports=[], hookspath=[],
                    excludes=[], noarchive=False)
server = Analysis([str(project / 'desktop/server_entry.py')], pathex=[str(project)],
                  binaries=[], datas=data_files, hiddenimports=[], hookspath=[],
                  excludes=['tkinter'], noarchive=False)

launcher_exe = EXE(PYZ(launcher.pure), launcher.scripts, [], exclude_binaries=True,
                   name='FrogLauncher', console=False, debug=False)
server_exe = EXE(PYZ(server.pure), server.scripts, [], exclude_binaries=True,
                name='FrogServer', console=True, debug=False)
release = COLLECT(launcher_exe, server_exe, launcher.binaries, launcher.datas,
                  server.binaries, server.datas, name='TravelFrog')
