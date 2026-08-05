# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: 将 Hemlock 后端打包为独立可执行文件。

打包命令：
    cd backend
    pyinstaller hemlock-backend.spec

输出：
    dist/hemlock-backend/hemlock-backend.exe  (主程序)
    dist/hemlock-backend/  (依赖文件目录)

打包后的 exe 放到 Tauri 的 resources 目录中。
"""

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['hemlock/run_server.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # 打包策略库和渗透测试相关数据
        ('hemlock/core/pentest_strategies.py', 'hemlock/core'),
        # 打包 mitmproxy addon（抓包核心模块）
        ('hemlock_addon', 'hemlock_addon'),
        # 打包内置数据（FlowTag 规则 + 89个安全技能）
        ('hemlock/data', 'hemlock/data'),
    ],
    hiddenimports=[
        # 确保所有模块被打包
        'hemlock.core.config',
        'hemlock.core.database',
        'hemlock.core.llm_client',
        'hemlock.core.vuln_verifier',
        'hemlock.core.pentest_context',
        'hemlock.core.pentest_strategies',
        'hemlock.core.logging_config',
        'hemlock.core.scope',
        'hemlock.core.proxy_controller',
        'hemlock.services.agent_service',
        'hemlock.services.ai_service',
        'hemlock.services.intruder_service',
        'hemlock.services.request_service',
        'hemlock.services.session_service',
        'hemlock.services.retest_service',
        'hemlock.services.report_service',
        'hemlock.services.event_bus',
        'hemlock.services.flow_service',
        'hemlock.api.flows',
        'hemlock.api.request',
        'hemlock.api.intruder',
        'hemlock.api.ai',
        'hemlock.api.agent',
        'hemlock.api.ws',
        'hemlock.api.proxy',
        'hemlock.api.settings',
        'hemlock.api.skills',
        'hemlock.api.projects',
        'hemlock.models.flow',
        'hemlock.models.finding',
        'hemlock.models.evidence',
        'hemlock.models.task',
        'hemlock.models.project',
        'hemlock.models.skill',
        'hemlock.models.mcp_server',
        # mitmproxy addon（抓包核心）
        'hemlock_addon',
        'hemlock_addon.capture',
        'hemlock_addon.runner',
        # 第三方依赖
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'fastapi',
        'fastapi.middleware',
        'fastapi.responses',
        'sqlalchemy',
        'sqlalchemy.dialects.sqlite',
        'pydantic',
        'pydantic_settings',
        'openai',
        'httpx',
        'httpcore',
        'anyio',
        'sniffio',
        'h11',
        'httptools',
        'uvicorn.protocols.http.httptools_impl',
        # mitmproxy 及其依赖（抓包核心）
        'mitmproxy',
        'mitmproxy.addonmanager',
        'mitmproxy.command',
        'mitmproxy.ctx',
        'mitmproxy.exceptions',
        'mitmproxy.flow',
        'mitmproxy.http',
        'mitmproxy.net',
        'mitmproxy.options',
        'mitmproxy.proxy',
        'mitmproxy.proxy.layers',
        'mitmproxy.proxy.layers.http',
        'mitmproxy.proxy.mode_servers',
        'mitmproxy.proxy.server',
        'mitmproxy.connection',
        'mitmproxy.tls',
        'mitmproxy.utils',
        'mitmproxy.tools',
        'mitmproxy.tools.dump',
        'mitmproxy.tools.console',
        'mitmproxy.tools.web',
        'mitmproxy.certs',
        'mitmproxy_rs',
        'mitmproxy_windows',
        'cryptography',
        'pyOpenSSL',
        'ssl',
        'h2',
        'hpack',
        'hyperframe',
        'aioquic',
        'wsproto',
        'ldap3',
        'kaitaistruct',
        'pydivert',
        'publicsuffix2',
        'sortedcontainers',
        'blinker',
        'tornado',
        'urwid',
        'flask',
        'jinja2',
        'msgpack',
        'zstandard',
        'passlib',
        'pyperclip',
        'ruamel.yaml',
        'pyparsing',
        'pyasn1',
        'pyasn1.modules',
        'service_identity',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的模块
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'PIL',
        'pytest',
        # jedi 路径过长导致打包失败，且运行时不需要
        'jedi',
        'parso',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='hemlock-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 隐藏控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='hemlock-backend',
)
