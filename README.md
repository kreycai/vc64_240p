# vc64_240p

**Português** · [English](README.en.md)

**240p de verdade nos canais de Virtual Console de Nintendo 64 do Wii**, sem filtro de
borrão, rodando no emulador oficial da Nintendo.

Patcheia um canal de N64 do VC pra sair em 240p em vez de 480i — do jeito que o jogo
aparecia num N64 de verdade numa TV de tubo. Opcionalmente também remove o **filtro escuro**
do emulador.

Você continua com tudo que o emulador oficial te dá — a compatibilidade que a Nintendo
ajustou jogo a jogo, saves nativos, suspend — e ganha a resolução que só os emuladores
homebrew davam. Até agora era escolher um ou outro.

> **Por que não tem foto de antes e depois.** Foto não mostra isso. Em qualquer exposição
> longa o bastante pra capturar um quadro inteiro, a câmera integra os dois campos do
> entrelaçado, então o 480i sai como imagem progressiva completa. E o defeito real do 480i é
> o tremor, que é temporal: existe entre os campos, não dentro de um. Na CRT a diferença é
> óbvia ao vivo e invisível num JPEG. Um vídeo curto mostraria; screenshot nunca vai.

---

## Por que isso existe

O VC de N64 do Wii é travado em 480i, e não é questão de configuração: o emulador desenha
480 linhas internamente, então não existe sinal de 240p pra forçar. NES, SNES e Mega Drive
do VC saem em 240p quando o console está em 480i — o N64 é a exceção.

Todos os outros caminhos estão fechados:

| caminho | por que não serve |
|---|---|
| vWii (Wii U) | não faz 240p de jeito nenhum — limitação de hardware |
| Not64 / Wii64 | fazem 240p, mas jogos pesados sofrem e vários têm travamento documentado |
| Swiss / Nintendont | só forçam 480p/576p; 240p forçado foi pedido e nunca implementado |
| desenhar 480 e reduzir | o display copy do GX **não faz downscale vertical**, só upscale |

Por isso este programa patcheia o binário do emulador.

## O que ele NÃO é

**A interface não é injetor de ROM.** Ela patcheia WADs de VC de N64 que já existem —
canais de varejo, ou injects feitos por outra pessoa.

Pra criar um canal a partir de uma ROM, use o
[FriishProduce](https://github.com/CatmanFan/FriishProduce): ele escolhe a base certa pra
revisão do emulador, comprime `romc` e ajusta a alocação de tamanho da ROM. Depois é só
passar a saída dele por aqui pro 240p.

Os dois se encaixam — nenhum injetor faz 240p, e a interface não injeta.

> A linha de comando (`src/vc64tool.py`) tem sim um comando `inject` **experimental**, com
> suporte a `romc`. Ele está aqui porque o que descobrimos em volta dele está documentado no
> [docs/TECNICO.md](docs/TECNICO.md), não porque seja melhor que o FriishProduce — não é. A
> parede é a compatibilidade jogo por jogo, não o injetor: a maioria das ROMs falha em
> qualquer base, independente da ferramenta que montou o canal. Gravar `romc` também exige o
> `romc.exe` do Jurai ao lado do script ou em `./tools`; esse binário é de terceiros e não é
> redistribuído aqui.

---

## Como usar

1. Abra o `vc64_240p.exe`
2. **Escolher WAD...** e aponte pro canal de N64
3. O painel diz o estado dos dois patches, separadamente:
   ```
   240p          : APLICAVEL
   filtro escuro : APLICAVEL
   ```
4. Marque a caixa do filtro escuro se quiser, e converta
5. O resultado é gravado **na mesma pasta da original**, com o nome descrevendo o que foi
   aplicado de fato. O arquivo original não é alterado.

A saída mantém o mesmo ID de canal, então ao instalar ela **substitui o canal original e
preserva os saves**. Dá pra passar uma WAD já convertida de novo, só pra adicionar o filtro.

### O que você precisa

- A **chave comum** do Wii (`common-key.bin`, 16 bytes). **Não vem junto.** Gere uma vez
  com o [gzinject](https://github.com/PracticeROM/gzinject):
  ```
  gzinject -a genkey
  ```
  > Atenção: o `genkey` pede pra você **digitar `45e` e dar enter**. Se você não digitar,
  > ele imprime "successfully generated" e gera uma chave **ERRADA** — esses três
  > caracteres são o IV de decriptação. Isso não está documentado em lugar nenhum.
- Um Wii com **cIOS 249** e **Priiloader** (ou BootMii).

### Instalando o resultado

WAD em `SD:/wad/`, instalar com o **YAWM ModMii Edition** — o Wii Mod Lite não tem seletor
de IOS. Escolha **IOS249**. Erro `-1017` quer dizer que aquele IOS não tem trucha; tente
outro slot de cIOS.

**Tenha o Priiloader instalado.** O jeito realista de um canal ruim dar problema é *banner
brick* — o Menu do Wii travar ao desenhar o banner. Recuperação: segure RESET ao ligar →
Priiloader → Homebrew Channel → desinstale o canal.

### Duas exigências do patch

- **Deixe o console em 480i.** Em 480p o emulador escolhe a entrada `NTSC_PROG` da tabela
  de modos, que não é patcheada. É de propósito — você não ia querer um patch de 240p
  brigando com um modo progressivo.
- **PAL não é suportado.** O caminho PAL sobrescreve as alturas em tempo de execução com
  574, então patchear aquela entrada da tabela não faz nada. E o alvo lá seria 288p, não 240p.

---

## O que ele faz

Seis mudanças no binário do emulador dentro da WAD. São 14 bytes por formato de TV e, como
ele grava os quatro formatos entrelaçados, 44 bytes no total:

| # | mudança | por quê |
|---|---|---|
| 1 | `viTVmode` `NTSC_INT` → `NTSC_DS` | double-strike, ou seja 240p |
| 2 | `viHeight` 480 → 240 | a janela do VI |
| 3 | `efbHeight` / `xfbHeight` **intocados** | o emulador continua desenhando 480 linhas, então nada é cortado nem ampliado |
| 4 | `xFBmode` **mantido em DF** | o stride de 2 linhas é o que faz a decimação 2:1, e portanto a geometria certa |
| 5 | `vfilter` → perfil progressivo | deflicker **desligado**, nitidez preservada |
| 6 | **NOP** no `add` que desloca a segunda base de campo do VI em uma linha | sem isso dá 240p com tremor forte: o VI alterna entre as linhas pares e ímpares a cada quadro |

O item 6 foi o mais difícil de achar, e é por causa dele que uma tentativa ingênua disso
parece não funcionar.

**Nada é fixo por jogo.** Todos os alvos são achados por padrão estrutural, então funciona
em builds de emulador que o programa nunca viu. Os detalhes técnicos, os offsets e as dez
hipóteses que estavam erradas antes estão em **[docs/TECNICO.md](docs/TECNICO.md)**
(a mesma coisa em inglês: [docs/TECHNICAL.md](docs/TECHNICAL.md)).

### Remoção do filtro escuro (opcional, 4 bytes)

O emulador escurece a imagem em relação ao hardware real. O patch escreve um `blr` por cima
do prólogo da função responsável, fazendo ela retornar na hora. É global — clareia tudo,
não é um ajuste seletivo.

Crédito do método: **NoobletCheese / Maeson**, como implementado no FriishProduce. Aqui ele
é localizado por padrão estrutural em vez de offset fixo.

---

## Confirmado funcionando

| jogo | build do emulador |
|---|---|
| Majora's Mask (USA, VC de varejo) | `content1` em LZ77 |
| Majora's Mask (tradução PT-BR) | mesmo build |
| Ocarina of Time (tradução PT-BR) | DOL cru, offsets completamente outros |
| F-Zero X (tradução PT-BR) | revisão de SDK diferente, `text1` em `0x800070C0` |
| Spider-Man (inject sobre base de Mario Party) | quinto build, confirmado depois da correção abaixo |

Cinco builds diferentes, todos confirmados em hardware real numa CRT. O localizador achou
todos os alvos sozinho em cada caso.

### Isto ainda está em fase de teste

Só os títulos acima foram verificados ao vivo. A biblioteca de VC de N64 mais os injects são
uma superfície bem maior do que uma pessoa com uma CRT consegue cobrir, e os builds de
emulador mudam de canal pra canal — que é exatamente o motivo de os alvos serem localizados
por estrutura em vez de offset fixo.

O programa se recusa a gravar em vez de gravar algo quebrado: se não achar um alvo, ele para
e diz. Então a falha que você deve esperar é "ele avisou que não conseguiu patchear essa
WAD", e não um canal que trava o console.

**Se alguma WAD não funcionar pra você, diz qual.** Um relato que nomeia o jogo e o que
aconteceu — se o programa recusou, ou se patcheou mas a TV continuou em 480i — vale mais que
um relato de que deu certo. É o único jeito de isso passar de cinco títulos.

### Um bug que vale conhecer, já corrigido

Até há pouco o patch era escrito na estrutura de render mode de **um** formato de TV, o
escolhido na interface. O emulador carrega NTSC, PAL, MPAL e EURGB60 lado a lado e escolhe um
em tempo de execução pela configuração **do console**, não pela WAD. Se o seu console usasse
um formato diferente do selecionado, o patch entrava numa estrutura que ninguém lê — e o
programa dizia que deu certo. Falha silenciosa, a pior de todas.

Agora ele patcheia todos os formatos entrelaçados de uma vez. Escrever num formato que o
console nunca seleciona é inerte, então isso é estritamente mais seguro que adivinhar. Se você
tentou uma versão anterior e não teve 240p, vale tentar de novo.

O seletor de formato de TV **foi removido da interface**: ele não escolhia mais nada, e um
controle que parece decidir algo sem decidir é pior que não existir.

Isso **não** faz o PAL funcionar — veja "Duas exigências do patch" acima. O caminho de código
do PAL sobrescreve as alturas em tempo de execução, então aquela estrutura é escrita mas não
tem efeito. Ela entra porque escrever não custa nada, e deixar de fora seria voltar a
adivinhar.

A **remoção do filtro escuro está confirmada in-game** no Majora's Mask (tradução PT-BR),
em hardware real, e o alvo é localizado corretamente nos 8 builds testados aqui. Ela tem
menos horas de jogo atrás dela que o patch de 240p, que tem quatro títulos confirmados.

---

## Compilando

No Windows, com Python 3.8+ e `pip install cryptography pyinstaller`:

```
build\build.bat
```

Gera `dist\vc64_240p.exe`, standalone. A interface usa tkinter, que já vem no instalador
padrão do Python pra Windows.

---

## Créditos

- **BirdonWheels** — mostrou no r/crtgaming em 2026 que 240p no VC de N64 era possível,
  patcheando vários emuladores com radare2. Aqueles patches nunca foram publicados e nunca
  cobriram Ocarina of Time nem Majora's Mask. Esta é uma implementação independente, com
  localizador automático.
- **NoobletCheese / Maeson** — o método do filtro escuro.
- **[FriishProduce](https://github.com/CatmanFan/FriishProduce)** (CatmanFan) — injeção, e
  onde o método do filtro escuro está implementado.
- **[gzinject](https://github.com/PracticeROM/gzinject)** (KrimtonZ) — referência de
  manipulação de WAD e gerador da chave comum.

## Licença

MIT — veja o [LICENSE](LICENSE).
