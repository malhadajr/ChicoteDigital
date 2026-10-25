CHICOTE DIGITAL
===============

Um chicote com física de corda preso ao ponteiro do mouse, desenhado por cima
de todas as janelas do Windows. Balance o mouse rápido o bastante e a ponta
estala: aparece um anel de choque, saem faíscas e toca o som do estalo.

O programa fica na bandeja do sistema (perto do relógio) e não atrapalha o uso
do computador: a janela do chicote não recebe cliques nem rouba o foco.


COMO USAR
---------

1. Baixe o ChicoteDigital.exe na página de Releases do repositório (ou gere o
   seu, veja "Gerar o executável" abaixo) e abra. Não precisa instalar Python.
2. O ícone do chicote aparece na bandeja.
3. Ligue e desligue o chicote com Ctrl+Alt+W ou com um clique no ícone.

Menu da bandeja (botão direito no ícone):

  Alternar chicote        liga/desliga (o mesmo que Ctrl+Alt+W)
  Som                     liga/desliga o estalo
  Testar estalo           toca o som uma vez
  Iniciar com o Windows   abre o programa junto com o Windows (só para o
                          seu usuário; grava em HKCU\...\CurrentVersion\Run)
  Sobre...                versão e onde fica o registro de erros
  Sair

Opções de linha de comando:

  --ativo     já começa com o chicote ligado
  --mudo      começa sem som
  --modo-qt   usa o overlay do Qt no lugar da janela nativa (alternativa)

Som próprio: coloque um arquivo chicote_estalo.wav (WAV PCM de 16 bits) ao
lado do .exe ou em %LOCALAPPDATA%\ChicoteDigital\ e ele substitui o estalo
sintetizado.

Erros são gravados em %LOCALAPPDATA%\ChicoteDigital\chicote_erro.log.

Na primeira execução o Windows pode mostrar "O Windows protegeu o
computador", porque o .exe não tem assinatura digital. Clique em "Mais
informações" e depois em "Executar assim mesmo".


COMO FUNCIONA
-------------

Física (classe Chicote)
  O chicote é uma corda de 26 nós ligados por segmentos de 12 px, simulada
  por integração de Verlet. O primeiro nó segue o cursor; os outros sofrem
  gravidade e amortecimento. A cada quadro (60 por segundo) a física roda em
  3 subpassos, e em cada um 16 iterações corrigem o comprimento dos
  segmentos. Uma rigidez de flexão que diminui do cabo até a ponta deixa o
  cabo firme e a ponta solta, como num chicote de verdade.

Estalo
  A velocidade da ponta é medida a cada quadro. Quando passa de 2200 px/s o
  chicote estala: anel de choque, 16 faíscas e o som. Um intervalo mínimo e
  uma histerese evitam estalos repetidos no mesmo golpe.

Desenho (função desenhar e classe Pintor)
  O chicote é desenhado com QPainter: contorno escuro, corpo que vai do
  marrom escuro ao couro claro e afina até a ponta, brilho quando está
  rápido, anel e faíscas.

Janela (classe JanelaWin32)
  No Windows o desenho vai para uma janela nativa em camadas
  (UpdateLayeredWindow) com transparência por pixel. A janela tem só o
  tamanho do chicote, não da tela inteira, é transparente a cliques, nunca
  recebe foco e fica sempre no topo.

Som (sintetizar_estalo)
  O estalo é sintetizado na hora em Python puro, sem arquivos de áudio:
  ruído agudo com decaimento rápido, um reflexo, uma pancada grave e uma
  cauda de eco. São geradas três variações, para o som não ficar repetitivo.

Atalho global
  Ctrl+Alt+W é registrado com RegisterHotKey, da própria API do Windows.

Privacidade: o programa não usa a rede, não lê a tela, não captura o
teclado (só recebe o aviso do atalho) e não envia teclas.


ARQUIVOS
--------

  chicote_digital.py    o programa
  build.ps1             gera o .exe
  ChicoteDigital.spec   receita do PyInstaller (o que entra no .exe)
  gerar_icone.py        gera chicote.ico a partir do desenho do programa
  chicote.ico           ícone do .exe
  versao.txt            nome e versão gravados no .exe


RODAR A PARTIR DO CÓDIGO
------------------------

Precisa do Python 3 e do PyQt6:

  python -m pip install --user pyqt6
  python chicote_digital.py


GERAR O EXECUTÁVEL
------------------

Uma vez:

  python -m pip install --user pyqt6 pyinstaller pillow

Depois de cada alteração:

  powershell -ExecutionPolicy Bypass -File build.ps1

O resultado é dist\ChicoteDigital.exe, um arquivo único com cerca de 23 MB.
Para lançar uma versão nova, atualize __version__ em chicote_digital.py e os
números em versao.txt.

Os parâmetros da física e do estalo (número de nós, gravidade, velocidade do
estalo, volume etc.) ficam no início de chicote_digital.py e podem ser
ajustados à vontade.
