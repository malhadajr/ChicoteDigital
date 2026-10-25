# -*- mode: python ; coding: utf-8 -*-
# Receita do PyInstaller: gera um único ChicoteDigital.exe, sem janela de console.

# módulos do Qt que o programa não usa (deixam o .exe bem menor)
EXCLUIR = [
    "PyQt6.QtNetwork", "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtSvg",
    "PyQt6.QtPdf", "PyQt6.QtMultimedia", "PyQt6.QtWebEngineCore", "PyQt6.QtDBus",
    "PyQt6.QtOpenGL", "PyQt6.QtSql", "PyQt6.QtTest", "PyQt6.QtXml",
    "tkinter", "unittest", "pydoc", "PIL",
]

a = Analysis(
    ["chicote_digital.py"],
    excludes=EXCLUIR,
    noarchive=False,
)
# DLLs e arquivos do Qt sem uso: renderização OpenGL por software, compilador de
# shaders, traduções e plugins de formatos de imagem (o desenho é todo QPainter)
SEM_USO = ("opengl32sw.dll", "d3dcompiler_47.dll", "qt6pdf", "qt6svg", "qt6network",
           "qt6opengl", "qt6qml", "qt6quick", "/translations/", "\\translations\\",
           "imageformats", "qt6virtualkeyboard")
a.binaries = [b for b in a.binaries if not any(s in b[0].lower() for s in SEM_USO)]
a.datas = [d for d in a.datas if not any(s in d[0].lower() for s in SEM_USO)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="ChicoteDigital",
    icon="chicote.ico",
    version="versao.txt",
    console=False,
    upx=False,
    debug=False,
    strip=False,
)
