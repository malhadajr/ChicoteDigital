#!/usr/bin/env python3
"""
chicote_digital.py - Chicote digital para o Windows.

Um chicote com física de corda (integração de Verlet) preso ao ponteiro do
mouse, desenhado por cima de todas as janelas. Quando a ponta ultrapassa a
velocidade de estalo, aparecem um anel de choque e faíscas e toca o estalo do
chicote (sintetizado no próprio script).

O desenho usa uma janela Win32 nativa com transparência por pixel
(UpdateLayeredWindow), do tamanho do chicote e não da tela: não há cobertura de
tela cheia, e a janela não captura cliques nem foco. Não envia teclas, não lê a tela e não usa rede.

Uso:
    pip install PyQt6
    python chicote_digital.py                # inicia na bandeja, chicote desligado
    python chicote_digital.py --ativo        # já inicia ligado
    python chicote_digital.py --mudo         # sem som
    python chicote_digital.py --modo-qt      # overlay do Qt (alternativa, tela cheia)

Atalho global: Ctrl+Alt+W alterna o chicote. Menu da bandeja: alternar, som,
testar estalo, iniciar com o Windows, sobre, sair.

Áudio próprio (opcional): coloque ao lado do script (ou do .exe) um
chicote_estalo.wav (WAV PCM de 16 bits) e ele substitui o estalo sintetizado.
Erros ficam em %LOCALAPPDATA%\\ChicoteDigital\\chicote_erro.log.
"""

import array
import ctypes
import io
import math
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import traceback
import wave

from PyQt6 import sip
from PyQt6.QtCore import QAbstractNativeEventFilter, QObject, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (
    QAction, QColor, QCursor, QIcon, QImage, QPainter, QPen, QPixmap, QRadialGradient,
)
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QWidget

__version__ = "1.0.0"
NOME_APP = "Chicote digital"
ID_APP = "ChicoteDigital"

CONGELADO = getattr(sys, "frozen", False)        # rodando como .exe (PyInstaller)
# pasta do programa: ao lado do .exe, ou ao lado do script
AQUI = os.path.dirname(os.path.abspath(sys.executable if CONGELADO else __file__))
# pasta de dados do usuário (gravável mesmo com o .exe em Arquivos de Programas)
DADOS = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), ID_APP)

# ----------------------------------------------------------------------------
# Parâmetros (ajuste à vontade)
# ----------------------------------------------------------------------------
NOS = 26                 # nós da corda
SEGMENTO = 12.0          # comprimento de cada segmento, em pixels lógicos (corda ~300 px)
SUBPASSOS = 3            # subpassos de física por quadro (estabilidade)
ITERACOES = 16           # iterações de restrição por subpasso
GRAVIDADE = 1500.0       # px/s²
AMORTECIMENTO = 0.995    # por subpasso (1.0 = sem perda)
RIGIDEZ_CABO = 0.30      # rigidez de flexão junto ao cabo (cai a zero na ponta)
CABO_NOS = 3             # segmentos iniciais desenhados como cabo grosso

LIMIAR_ESTALO = 2200.0   # velocidade da ponta (px/s) que dispara o estalo
RECARGA_ESTALO = 0.35    # segundos mínimos entre estalos
DURACAO_ANEL = 0.35      # duração do anel de choque, em segundos

ALVO_FPS = 60
CAP_LOGICO = 1000        # lado máximo (px lógicos) da janela do chicote

VOLUME = 0.9             # volume final do áudio (0 a 1)

ATALHO_ID = 0x5748       # id do atalho global (arbitrário)
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
VK_W = 0x57
WM_HOTKEY = 0x0312


def registrar_erro(contexto=""):
    """Grava o traceback atual em chicote_erro.log (na pasta DADOS)."""
    try:
        os.makedirs(DADOS, exist_ok=True)
        with open(caminho_log(), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), contexto,
                                      traceback.format_exc()))
    except OSError:
        pass


def caminho_log():
    return os.path.join(DADOS, "chicote_erro.log")


# ----------------------------------------------------------------------------
# Física (independente de Qt, para poder ser testada isoladamente)
# ----------------------------------------------------------------------------
class Chicote:
    def __init__(self, n=NOS, segmento=SEGMENTO):
        self.n = n
        self.seg = segmento
        self.x = [0.0] * n
        self.y = [0.0] * n
        self.px = [0.0] * n
        self.py = [0.0] * n
        self.ax = self.ay = 0.0          # âncora (cursor), coordenadas lógicas
        self.tempo = 0.0
        self.vel = 0.0                   # velocidade suavizada da ponta (px/s)
        self.armado = True
        self.ultimo_estalo = -10.0
        self.estalo_pendente = False     # o controlador consome este sinal (toca o som)
        self.anel = None                 # (x, y, instante)
        self.particulas = []             # [x, y, vx, vy, vida, vida_max]

    def reiniciar(self, cx, cy):
        for i in range(self.n):
            self.x[i] = self.px[i] = cx
            self.y[i] = self.py[i] = cy + i * self.seg
        self.ax, self.ay = cx, cy
        self.tempo = 0.0
        self.vel = 0.0
        self.armado = True
        self.ultimo_estalo = -10.0
        self.estalo_pendente = False
        self.anel = None
        self.particulas.clear()

    # -- passo de simulação -------------------------------------------------
    def passo(self, cx, cy, dt):
        ponta_x0, ponta_y0 = self.x[-1], self.y[-1]
        sx, sy = self.ax, self.ay
        h = dt / SUBPASSOS
        for s in range(1, SUBPASSOS + 1):
            f = s / SUBPASSOS
            self.ax = sx + (cx - sx) * f
            self.ay = sy + (cy - sy) * f
            self._subpasso(h)
        self.tempo += dt

        bruta = math.hypot(self.x[-1] - ponta_x0, self.y[-1] - ponta_y0) / dt
        self.vel = 0.4 * self.vel + 0.6 * bruta

        # histerese: rearma ao desacelerar, ou após um tempo mesmo em golpes contínuos
        if self.vel < LIMIAR_ESTALO * 0.6 or self.tempo - self.ultimo_estalo > 0.8:
            self.armado = True
        if (self.vel > LIMIAR_ESTALO and self.armado
                and self.tempo - self.ultimo_estalo > RECARGA_ESTALO):
            self._estalar()
            self.armado = False

        self._particulas(dt)

    def _subpasso(self, h):
        n, seg, x, y, px, py = self.n, self.seg, self.x, self.y, self.px, self.py
        g = GRAVIDADE * h * h
        for i in range(1, n):
            vx = (x[i] - px[i]) * AMORTECIMENTO
            vy = (y[i] - py[i]) * AMORTECIMENTO
            px[i], py[i] = x[i], y[i]
            x[i] += vx
            y[i] += vy + g
        x[0], y[0], px[0], py[0] = self.ax, self.ay, self.ax, self.ay

        # rigidez de flexão: o cabo é firme e a ponta é solta
        for i in range(1, n - 1):
            k = RIGIDEZ_CABO * (1.0 - i / n)
            dx, dy = x[i] - x[i - 1], y[i] - y[i - 1]
            d = math.hypot(dx, dy) or 1e-6
            # o deslocamento vai também para a posição anterior: assim a
            # rigidez só realinha a corda e não injeta velocidade (energia)
            cx_ = (x[i] + dx / d * seg - x[i + 1]) * k
            cy_ = (y[i] + dy / d * seg - y[i + 1]) * k
            x[i + 1] += cx_
            y[i + 1] += cy_
            px[i + 1] += cx_
            py[i + 1] += cy_

        # restrições de distância
        for _ in range(ITERACOES):
            x[0], y[0] = self.ax, self.ay
            for i in range(n - 1):
                dx, dy = x[i + 1] - x[i], y[i + 1] - y[i]
                d = math.hypot(dx, dy)
                if d < 1e-9:
                    continue
                diff = (d - seg) / d
                if i == 0:
                    x[1] -= dx * diff
                    y[1] -= dy * diff
                else:
                    hx, hy = dx * diff * 0.5, dy * diff * 0.5
                    x[i] += hx
                    y[i] += hy
                    x[i + 1] -= hx
                    y[i + 1] -= hy

    # -- efeito visual do estalo -------------------------------------------
    def _estalar(self):
        self.ultimo_estalo = self.tempo
        self.estalo_pendente = True
        tx, ty = self.x[-1], self.y[-1]
        self.anel = (tx, ty, self.tempo)
        for _ in range(16):
            ang = random.uniform(0, math.tau)
            v = random.uniform(250, 750)
            vida = random.uniform(0.25, 0.55)
            self.particulas.append([tx, ty, math.cos(ang) * v, math.sin(ang) * v, vida, vida])

    def _particulas(self, dt):
        vivas = []
        for p in self.particulas:
            p[4] -= dt
            if p[4] <= 0:
                continue
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[3] += 600.0 * dt
            p[2] *= 0.96
            vivas.append(p)
        self.particulas = vivas

    # -- utilidades de desenho ---------------------------------------------
    @property
    def brilho(self):
        return max(0.0, min(1.0, self.vel / LIMIAR_ESTALO))

    def anel_progresso(self):
        if self.anel is None:
            return None
        prog = (self.tempo - self.anel[2]) / DURACAO_ANEL
        return prog if 0.0 <= prog < 1.0 else None

    def limites(self):
        """Retângulo lógico (x0, y0, x1, y1) que cobre tudo o que é desenhado."""
        xs = list(self.x)
        ys = list(self.y)
        for p in self.particulas:
            xs.append(p[0])
            ys.append(p[1])
        folga = 30.0
        if self.anel_progresso() is not None:
            folga = 100.0
        return min(xs) - folga, min(ys) - folga, max(xs) + folga, max(ys) + folga


# ----------------------------------------------------------------------------
# Desenho
# ----------------------------------------------------------------------------
def _mistura(c1, c2, t):
    return QColor(*(int(a + (b - a) * t) for a, b in zip(c1, c2)))


def desenhar(p: QPainter, ch: Chicote):
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    n = ch.n
    pontos = [QPointF(ch.x[i], ch.y[i]) for i in range(n)]

    def largura(i):
        if i < CABO_NOS:
            return 9.0
        t = (i - CABO_NOS) / max(1, n - 2 - CABO_NOS)
        return 5.5 * (1 - t) + 1.2 * t

    def cor(i):
        if i < CABO_NOS:
            return QColor(38, 27, 20)
        t = (i - CABO_NOS) / max(1, n - 2 - CABO_NOS)
        return _mistura((96, 60, 34), (206, 160, 104), t)

    # contorno escuro (legível em fundo claro e escuro)
    for i in range(n - 1):
        pen = QPen(QColor(0, 0, 0, 120), largura(i) + 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(pontos[i], pontos[i + 1])
    # corpo
    for i in range(n - 1):
        pen = QPen(cor(i), largura(i))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(pontos[i], pontos[i + 1])
    # estalador (ponta clara)
    pen = QPen(QColor(238, 228, 206), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(pontos[n - 3], pontos[n - 1])

    # brilho na ponta quando a velocidade é alta
    b = ch.brilho
    if b > 0.4:
        raio = 10 + 18 * b
        grad = QRadialGradient(pontos[-1], raio)
        grad.setColorAt(0.0, QColor(255, 235, 170, int(200 * b)))
        grad.setColorAt(1.0, QColor(255, 235, 170, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(pontos[-1], raio, raio)
        p.setBrush(Qt.BrushStyle.NoBrush)

    # anel de choque
    prog = ch.anel_progresso()
    if prog is not None:
        ax, ay, _ = ch.anel
        alfa = int(255 * (1 - prog))
        pen = QPen(QColor(255, 240, 205, alfa), 1.0 + 3.0 * (1 - prog))
        p.setPen(pen)
        r = 8 + 80 * prog
        p.drawEllipse(QPointF(ax, ay), r, r)

    # faíscas
    for x, y, vx, vy, vida, vida_max in ch.particulas:
        alfa = int(255 * max(0.0, vida / vida_max))
        pen = QPen(QColor(255, 222, 140, alfa), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(x, y), QPointF(x - vx * 0.025, y - vy * 0.025))


class Pintor:
    """Desenha o chicote direto numa região de memória BGRA premultiplicada.

    A região tem capacidade cap_w x cap_h; a cada quadro usa só o retângulo
    do chicote (canto superior esquerdo), com o passo de linha da capacidade.
    """

    def __init__(self, endereco, cap_w, cap_h):
        self.endereco = endereco
        self.cap_w = cap_w
        self.cap_h = cap_h

    def renderizar(self, ch, escala):
        x0, y0, x1, y1 = ch.limites()
        ox = math.floor(x0 * escala)
        oy = math.floor(y0 * escala)
        w = max(1, min(self.cap_w, math.ceil(x1 * escala) - ox))
        h = max(1, min(self.cap_h, math.ceil(y1 * escala) - oy))
        img = QImage(sip.voidptr(self.endereco), w, h, self.cap_w * 4,
                     QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)                                  # tudo transparente
        p = QPainter(img)
        p.translate(-ox, -oy)
        p.scale(escala, escala)
        desenhar(p, ch)
        p.end()
        return ox, oy, w, h


# ----------------------------------------------------------------------------
# Janela Win32 nativa com transparência por pixel (Windows)
# ----------------------------------------------------------------------------
if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT), ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON),
        ]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                    ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 1)]

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
        ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
        wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                       wintypes.DWORD]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    WS_POPUP = 0x80000000
    WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x00000008, 0x00000020, 0x00000080
    WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x00080000, 0x08000000
    SW_SHOWNOACTIVATE = 4
    ULW_ALPHA = 2
    SWP_FLAGS_TOPO = 0x0001 | 0x0002 | 0x0010        # NOSIZE | NOMOVE | NOACTIVATE
    HWND_TOPMOST = wintypes.HWND(-1)
    NOME_CLASSE = "ChicoteDigitalOverlay"

    _wndproc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
    _classe_registrada = False

    def _registrar_classe():
        global _classe_registrada
        if _classe_registrada:
            return
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = _wndproc
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = NOME_CLASSE
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())
        _classe_registrada = True

    class JanelaWin32:
        """Janela em camadas (layered), transparente a cliques, sem foco, sempre no topo."""

        def __init__(self, cap_w, cap_h):
            _registrar_classe()
            self.visivel = False
            self.hwnd = None
            self.hbmp = None
            self.hdc_tela = user32.GetDC(None)
            self.hdc_mem = gdi32.CreateCompatibleDC(self.hdc_tela)
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = cap_w
            bmi.bmiHeader.biHeight = -cap_h              # negativo = de cima para baixo
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0              # BI_RGB
            bits = ctypes.c_void_p()
            self.hbmp = gdi32.CreateDIBSection(self.hdc_mem, ctypes.byref(bmi), 0,
                                               ctypes.byref(bits), None, 0)
            if not self.hbmp or not bits.value:
                self.fechar()
                raise ctypes.WinError(ctypes.get_last_error())
            self.endereco = bits.value
            self.antigo = gdi32.SelectObject(self.hdc_mem, self.hbmp)
            ex = (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
                  | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
            self.hwnd = user32.CreateWindowExW(ex, NOME_CLASSE, "Chicote digital", WS_POPUP,
                                               0, 0, 1, 1, None, None,
                                               kernel32.GetModuleHandleW(None), None)
            if not self.hwnd:
                self.fechar()
                raise ctypes.WinError(ctypes.get_last_error())

        def atualizar(self, ox, oy, w, h):
            blend = BLENDFUNCTION(0, 0, 255, 1)          # AC_SRC_OVER, alfa por pixel
            destino = wintypes.POINT(ox, oy)
            tamanho = wintypes.SIZE(w, h)
            origem = wintypes.POINT(0, 0)
            ok = user32.UpdateLayeredWindow(self.hwnd, self.hdc_tela, ctypes.byref(destino),
                                            ctypes.byref(tamanho), self.hdc_mem,
                                            ctypes.byref(origem), 0, ctypes.byref(blend),
                                            ULW_ALPHA)
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.visivel:
                user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
                self.visivel = True

        def reafirmar_topo(self):
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS_TOPO)

        def fechar(self):
            if self.hwnd:
                user32.DestroyWindow(self.hwnd)
                self.hwnd = None
            if self.hdc_mem:
                if getattr(self, "antigo", None):
                    gdi32.SelectObject(self.hdc_mem, self.antigo)
                if self.hbmp:
                    gdi32.DeleteObject(self.hbmp)
                    self.hbmp = None
                gdi32.DeleteDC(self.hdc_mem)
                self.hdc_mem = None
            if self.hdc_tela:
                user32.ReleaseDC(None, self.hdc_tela)
                self.hdc_tela = None

    class BackendWin32:
        """Janela nativa do tamanho do chicote. Física em px lógicos, saída em px físicos."""

        def __init__(self, escala):
            self.escala = escala
            cap = int(math.ceil(CAP_LOGICO * escala))
            self.janela = JanelaWin32(cap, cap)
            self.pintor = Pintor(self.janela.endereco, cap, cap)
            self._pt = wintypes.POINT()

        def cursor(self):
            user32.GetCursorPos(ctypes.byref(self._pt))
            return self._pt.x / self.escala, self._pt.y / self.escala

        def desenhar(self, ch):
            ox, oy, w, h = self.pintor.renderizar(ch, self.escala)
            self.janela.atualizar(ox, oy, w, h)

        def reafirmar_topo(self):
            self.janela.reafirmar_topo()

        def fechar(self):
            self.janela.fechar()


# ----------------------------------------------------------------------------
# Alternativa: overlay do Qt (uma janela por monitor)
# ----------------------------------------------------------------------------
class OverlayQt(QWidget):
    def __init__(self, ch, tela):
        super().__init__()
        self.ch = ch
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAutoFillBackground(False)
        # 1 px a menos na altura evita que o Windows trate a janela como tela cheia
        self.geo = tela.geometry().adjusted(0, 0, 0, -1)
        self.setGeometry(self.geo)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(ev.rect(), Qt.GlobalColor.transparent)       # limpa para transparente
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.translate(-self.geo.x(), -self.geo.y())
        desenhar(p, self.ch)
        p.end()


class BackendQt:
    escala = 1.0

    def __init__(self, ch):
        self.ch = ch
        self.overlays = [OverlayQt(ch, t) for t in QApplication.screens()]
        for w in self.overlays:
            w.show()
        self.ultimo_ret = None

    def cursor(self):
        p = QCursor.pos()
        return float(p.x()), float(p.y())

    def desenhar(self, ch):
        x0, y0, x1, y1 = ch.limites()
        ret = QRectF(x0, y0, x1 - x0, y1 - y0)
        area = ret if self.ultimo_ret is None else ret.united(self.ultimo_ret)
        self.ultimo_ret = ret
        for w in self.overlays:
            parte = area.intersected(QRectF(w.geo))
            if not parte.isEmpty():
                w.update(parte.translated(-w.geo.x(), -w.geo.y()).toAlignedRect())

    def reafirmar_topo(self):
        for w in self.overlays:
            w.raise_()

    def fechar(self):
        for w in self.overlays:
            w.close()
            w.deleteLater()
        self.overlays = []


# ----------------------------------------------------------------------------
# Som: estalo de chicote sintetizado em Python puro
# ----------------------------------------------------------------------------
TAXA = 22050


def _normalizar(v, pico=1.0):
    m = max((abs(x) for x in v), default=0.0) or 1.0
    k = pico / m
    return [x * k for x in v]


def sintetizar_estalo(rng):
    """Estampido seco (ruído agudo com decaimento rápido) + reflexo + pancada grave."""
    n = int(0.32 * TAXA)
    out = [0.0] * n
    ant = 0.0
    fase = 0.0
    cauda = 0.0
    for i in range(n):
        t = i / TAXA
        r = rng.uniform(-1.0, 1.0)
        hp = r - ant                         # diferenciador: realça os agudos
        ant = r
        v = hp * math.exp(-t / 0.009)        # estampido principal
        if t > 0.011:
            v += 0.45 * hp * math.exp(-(t - 0.011) / 0.012)      # reflexo
        fase += 2.0 * math.pi * (55.0 + 95.0 * math.exp(-t / 0.02)) / TAXA
        grave = 0.5 * math.sin(fase) * math.exp(-t / 0.035)       # pancada
        cauda += 0.2 * (rng.uniform(-1.0, 1.0) - cauda)           # eco difuso
        out[i] = v * 0.55 + grave + 0.45 * cauda * math.exp(-t / 0.09)
    return out


def ler_wav(caminho):
    """Lê um WAV PCM 16 bits e devolve amostras mono em ponto flutuante na TAXA interna."""
    with wave.open(caminho, "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("o WAV precisa ser PCM de 16 bits: " + caminho)
        canais, taxa = w.getnchannels(), w.getframerate()
        bruto = w.readframes(w.getnframes())
    a = array.array("h")
    a.frombytes(bruto)
    if sys.byteorder == "big":
        a.byteswap()
    mono = [sum(a[i:i + canais]) / canais / 32768.0 for i in range(0, len(a) - canais + 1, canais)]
    if taxa != TAXA and mono:
        passo = taxa / TAXA
        m = int(len(mono) / passo)
        res = [0.0] * m
        for i in range(m):
            j = i * passo
            i0 = int(j)
            f = j - i0
            i1 = min(i0 + 1, len(mono) - 1)
            res[i] = mono[i0] * (1.0 - f) + mono[i1] * f
        mono = res
    return mono


def montar_wav(estalo):
    """Converte o estalo (amostras em ponto flutuante) em um WAV mono de 16 bits."""
    mix = _normalizar(estalo, VOLUME) + [0.0] * int(0.05 * TAXA)
    pcm = array.array("h", (int(max(-1.0, min(1.0, v)) * 32767) for v in mix))
    if sys.byteorder == "big":
        pcm.byteswap()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TAXA)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def gerar_variantes(rng=None):
    """Devolve uma lista de WAVs (bytes) do estalo; o ruído muda a cada variante."""
    rng = rng or random.Random()
    for pasta in (AQUI, DADOS):                  # som próprio substitui o sintetizado
        caminho = os.path.join(pasta, "chicote_estalo.wav")
        if os.path.exists(caminho):
            try:
                return [montar_wav(ler_wav(caminho))]
            except Exception:
                registrar_erro("falha ao ler " + caminho)
    return [montar_wav(sintetizar_estalo(rng)) for _ in range(3)]


class Som:
    def __init__(self, ativo=True):
        self.ativo = ativo
        self.arquivos = []
        self.pasta = None
        if sys.platform == "win32":
            threading.Thread(target=self._preparar, daemon=True).start()

    def _preparar(self):
        try:
            self.pasta = tempfile.mkdtemp(prefix="chicote_")
            arquivos = []
            for k, wav in enumerate(gerar_variantes()):
                caminho = os.path.join(self.pasta, "estalo%d.wav" % k)
                with open(caminho, "wb") as f:
                    f.write(wav)
                arquivos.append(caminho)
            self.arquivos = arquivos
        except Exception:
            registrar_erro("falha ao preparar o som")

    def tocar(self):
        if not (self.ativo and self.arquivos) or sys.platform != "win32":
            return
        try:
            import winsound
            winsound.PlaySound(random.choice(self.arquivos),
                               winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception:
            registrar_erro("falha ao tocar o som")

    def encerrar(self):
        try:
            if sys.platform == "win32":
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
        if self.pasta:
            shutil.rmtree(self.pasta, ignore_errors=True)


# ----------------------------------------------------------------------------
# Controlador
# ----------------------------------------------------------------------------
class Controlador(QObject):
    def __init__(self, som, modo_qt=False):
        super().__init__()
        self.som = som
        self.modo_qt = modo_qt or sys.platform != "win32"
        self.avisar = lambda texto: None
        self.chicote = Chicote()
        self.backend = None
        self.ativo = False
        self.ultimo_t = 0.0
        self.quadros = 0
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(max(1, round(1000 / ALVO_FPS)))
        self.timer.timeout.connect(self._tick)

    def alternar(self):
        self.desligar() if self.ativo else self.ligar()

    def _criar_backend(self):
        if not self.modo_qt:
            try:
                escala = QApplication.primaryScreen().devicePixelRatio()
                return BackendWin32(escala)
            except Exception:
                registrar_erro("janela Win32 falhou; usando o overlay do Qt")
                self.modo_qt = True
                self.avisar("Janela nativa indisponível; usando o modo Qt. Veja " + caminho_log())
        return BackendQt(self.chicote)

    def ligar(self):
        if self.ativo:
            return
        self.backend = self._criar_backend()
        cx, cy = self.backend.cursor()
        self.chicote.reiniciar(cx, cy)
        self.ultimo_t = time.perf_counter()
        self.quadros = 0
        self.ativo = True
        self.timer.start()

    def desligar(self):
        if not self.ativo:
            return
        self.timer.stop()
        try:
            self.backend.fechar()
        except Exception:
            registrar_erro("falha ao fechar a janela")
        self.backend = None
        self.ativo = False

    def _tick(self):
        try:
            agora = time.perf_counter()
            dt = min(0.05, max(0.001, agora - self.ultimo_t))
            self.ultimo_t = agora
            cx, cy = self.backend.cursor()
            self.chicote.passo(cx, cy, dt)
            if self.chicote.estalo_pendente:
                self.chicote.estalo_pendente = False
                self.som.tocar()
            self.backend.desenhar(self.chicote)
            self.quadros += 1
            if self.quadros % 120 == 0:
                self.backend.reafirmar_topo()      # mantém "sempre no topo" sem roubar foco
        except Exception:
            registrar_erro("erro no quadro; chicote desligado")
            self.desligar()
            self.avisar("Erro no chicote; desligado. Veja " + caminho_log())


# ----------------------------------------------------------------------------
# Atalho global (Windows, sem dependências extras)
# ----------------------------------------------------------------------------
class FiltroAtalho(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, tipo, mensagem):
        if bytes(tipo) == b"windows_generic_MSG":
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(mensagem))
            if msg.message == WM_HOTKEY and msg.wParam == ATALHO_ID:
                self.callback()
                return True, 0
        return False, 0


def registrar_atalho():
    return bool(ctypes.windll.user32.RegisterHotKey(
        None, ATALHO_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_W))


def remover_atalho():
    ctypes.windll.user32.UnregisterHotKey(None, ATALHO_ID)


# ----------------------------------------------------------------------------
# Integração com o Windows: instância única, identidade e inicialização
# ----------------------------------------------------------------------------
_mutex = None


def primeira_instancia():
    """Cria um mutex nomeado; devolve False se outra cópia já está rodando."""
    global _mutex
    if sys.platform != "win32":
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    _mutex = k32.CreateMutexW(None, False, "Local\\" + ID_APP + "_instancia")
    return ctypes.get_last_error() != 183           # ERROR_ALREADY_EXISTS


def definir_id_app():
    """Faz a barra de tarefas e as notificações mostrarem o nome do programa."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ID_APP)
        except Exception:
            pass


CHAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"


def comando_inicio():
    if CONGELADO:
        return '"%s"' % sys.executable
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return '"%s" "%s"' % (pythonw, os.path.abspath(__file__))


def inicia_com_windows():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN) as k:
            winreg.QueryValueEx(k, ID_APP)
        return True
    except OSError:
        return False


def definir_inicio_com_windows(ligar):
    """Liga ou desliga a entrada em HKCU\\...\\Run (só para o usuário atual)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN, 0, winreg.KEY_SET_VALUE) as k:
            if ligar:
                winreg.SetValueEx(k, ID_APP, 0, winreg.REG_SZ, comando_inicio())
            else:
                try:
                    winreg.DeleteValue(k, ID_APP)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        registrar_erro("falha ao alterar a inicialização com o Windows")
        return False


# ----------------------------------------------------------------------------
# Ícone da bandeja e programa principal
# ----------------------------------------------------------------------------
def pixmap_icone(tam=64):
    """Desenha o ícone (chicote enrolado) em tam x tam; desenho base em 64 x 64."""
    pm = QPixmap(tam, tam)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.scale(tam / 64, tam / 64)
    pen = QPen(QColor(120, 78, 44), 6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawArc(10, 6, 46, 46, 90 * 16, -230 * 16)
    pen = QPen(QColor(38, 27, 20), 10)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(14, 56), QPointF(26, 40))
    p.end()
    return pm


def icone():
    ic = QIcon()
    for tam in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(pixmap_icone(tam))
    return ic


def mostrar_sobre():
    QMessageBox.about(
        None, "Sobre o " + NOME_APP,
        "<b>%s</b> %s<br><br>"
        "Um chicote com física de corda preso ao ponteiro do mouse.<br><br>"
        "Ctrl+Alt+W liga e desliga.<br>"
        "Registro de erros: %s" % (NOME_APP, __version__, caminho_log()))


def main():
    definir_id_app()
    app = QApplication(sys.argv)
    app.setApplicationName(NOME_APP)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(icone())

    if not primeira_instancia():
        QMessageBox.information(None, NOME_APP,
                                "O %s já está aberto.\nProcure o ícone na bandeja, "
                                "perto do relógio." % NOME_APP)
        return

    som = Som(ativo="--mudo" not in sys.argv)
    ctrl = Controlador(som, modo_qt="--modo-qt" in sys.argv)

    bandeja = QSystemTrayIcon(app.windowIcon(), app)
    ctrl.avisar = lambda texto: bandeja.showMessage(
        NOME_APP, texto, QSystemTrayIcon.MessageIcon.Warning, 6000)
    menu = QMenu()
    acao_alt = QAction("Alternar chicote (Ctrl+Alt+W)", menu)
    acao_alt.triggered.connect(ctrl.alternar)
    acao_som = QAction("Som", menu)
    acao_som.setCheckable(True)
    acao_som.setChecked(som.ativo)
    acao_som.toggled.connect(lambda v: setattr(som, "ativo", v))
    acao_teste = QAction("Testar estalo", menu)
    acao_teste.triggered.connect(lambda *_: som.tocar())
    acao_inicio = QAction("Iniciar com o Windows", menu)
    acao_inicio.setCheckable(True)
    acao_inicio.setChecked(sys.platform == "win32" and inicia_com_windows())
    acao_inicio.setEnabled(sys.platform == "win32")

    def alternar_inicio(v):
        if not definir_inicio_com_windows(v):
            acao_inicio.blockSignals(True)
            acao_inicio.setChecked(not v)
            acao_inicio.blockSignals(False)
            ctrl.avisar("Não consegui alterar a inicialização. Veja " + caminho_log())

    acao_inicio.toggled.connect(alternar_inicio)
    acao_sobre = QAction("Sobre...", menu)
    acao_sobre.triggered.connect(lambda *_: mostrar_sobre())
    acao_sair = QAction("Sair", menu)
    acao_sair.triggered.connect(app.quit)
    for item in (acao_alt, acao_som, acao_teste):
        menu.addAction(item)
    menu.addSeparator()
    menu.addAction(acao_inicio)
    menu.addAction(acao_sobre)
    menu.addSeparator()
    menu.addAction(acao_sair)
    bandeja.setContextMenu(menu)
    bandeja.setToolTip("%s %s" % (NOME_APP, __version__))
    # clique simples no ícone também liga e desliga
    bandeja.activated.connect(
        lambda motivo: ctrl.alternar()
        if motivo == QSystemTrayIcon.ActivationReason.Trigger else None)
    bandeja.show()

    filtro = None
    if sys.platform == "win32":
        filtro = FiltroAtalho(ctrl.alternar)
        app.installNativeEventFilter(filtro)
        if registrar_atalho():
            app.aboutToQuit.connect(remover_atalho)
            bandeja.showMessage(NOME_APP, "Ctrl+Alt+W liga e desliga.",
                                QSystemTrayIcon.MessageIcon.Information, 3000)
        else:
            bandeja.showMessage(NOME_APP,
                                "Ctrl+Alt+W já está em uso por outro programa. "
                                "Use o menu da bandeja.",
                                QSystemTrayIcon.MessageIcon.Warning, 6000)
    else:
        bandeja.showMessage(NOME_APP,
                            "Atalho global e som só no Windows. Use o menu da bandeja.",
                            QSystemTrayIcon.MessageIcon.Information, 4000)

    if "--ativo" in sys.argv:
        ctrl.ligar()

    app.aboutToQuit.connect(ctrl.desligar)
    app.aboutToQuit.connect(som.encerrar)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
