# Notas técnicas

[English](TECHNICAL.md) · **Português**

Como cada alvo é localizado, o que tem dentro do WAD, e — mais útil que tudo isso — **as
hipóteses que estavam erradas**, pra ninguém precisar gastar uma noite nelas de novo.

---

## 1. O WAD

Um WAD do Wii é: cabeçalho, cadeia de certificados, ticket, TMD, o bloco de conteúdos e um
rodapé — cada seção alinhada em `0x40`.

- A **chave de título** é embrulhada com a chave comum do Wii, IV = title id + 8 bytes zero.
- Cada **conteúdo** é AES-128-CBC com IV = índice do conteúdo em `u16` big endian + 14 zeros.
- O TMD guarda o tamanho e o SHA-1 de cada conteúdo. Mexeu num conteúdo, os dois têm que ser
  reescritos.
- As assinaturas são resolvidas por **trucha / fakesign**: zera a assinatura RSA e faz força
  bruta num campo de padding até o SHA-1 do bloco assinado começar com `0x00`. O padding do
  TMD fica em `0x1E2`, o do ticket em `0x1F2`. É por isso que instalar exige o cIOS 249.

Num canal de N64 do VC:

| conteúdo | o que é |
|---|---|
| 0 | banner — **não mexer**, é o que dá banner brick |
| **1** | **o emulador de N64**, DOL cru ou comprimido em LZ77 (tipo `0x10`) |
| 2, 3, 4, 6 | recursos compartilhados (wwwlib, fonte, botão HOME) |
| 5 | arquivo U8 com a ROM (`rom` ou `romc`), textos de save, TPL do banner |
| 7 | DOL de boot — não é o emulador |

Quando o `content1` é comprimido, o resultado patcheado é gravado **descomprimido**, sem
recomprimir. É o que o projeto gz faz, e funciona em hardware real.

---

## 2. Localizando a tabela de modos de vídeo

O emulador carrega uma tabela de structs `GXRenderModeObj`, de `0x3C` bytes cada, uma por
formato de TV. Achada por estrutura, não por offset:

- `fbWidth == 640`
- `efbHeight == xfbHeight`
- `viHeight == efbHeight`
- `viTVmode` é um valor válido de `(formato << 2) | modo`
- os 7 taps do `vfilter` somam **64**

Layout dos campos:

```
+0x00 viTVmode     +0x04 fbWidth      +0x06 efbHeight   +0x08 xfbHeight
+0x0A viXOrigin    +0x0C viYOrigin    +0x0E viWidth     +0x10 viHeight
+0x14 xFBmode      +0x18 field_rendering                +0x19 aa
+0x1A sample_pattern[24]               +0x32 vfilter[7]
```

`viTVmode = (formato << 2) | modo`, onde modo `0` = INT, `1` = DS (240p), `2` = PROG.

Resultado típico: cinco entradas — `NTSC_INT`, `NTSC_PROG`, `MPAL_INT`, `PAL_INT`,
`EURGB60_INT`. Só a `NTSC_INT` é patcheada.

**O vfilter tem que continuar somando 64 depois do patch.** Um `09 09 0A 0A 0A 09 09` chapado
soma 66 e está errado; `00 00 15 16 15 00 00` (o perfil da entrada progressiva) está certo e
desliga o deflicker.

## 3. Localizando o `add` das bases de campo do VI

Esse é o que importa, e é o difícil.

Os registradores de framebuffer do VI (`0xCC002000 + 0x1C..0x28`) **nunca aparecem como
offset literal em instrução nenhuma** — o SDK escreve neles num laço, a partir de um array
sombra. Procurar por offset de registrador não acha absolutamente nada.

O que funciona: procurar **quatro `srwi r0,r0,5` (`0x5400D97E`) consecutivos, espaçados de
12 bytes**. São os quatro registradores de framebuffer sendo empacotados como `endereço >> 5`.

A partir daí, o `VISetNextFramebuffer` monta um struct HorVer e chama um `calcFbbs` com cinco
ponteiros de saída. A semântica do struct, recuperada **pelo chamador** e não por chute:

| offset | significado |
|---|---|
| `+0x0A` | paridade de campo (causa swap) |
| `+0x20` | flag de double-field |
| `+0x2C` | unidade de linha (`<< 5`) |
| `+0x30` | framebuffer principal |
| `+0x34`, `+0x38` | **as duas bases de campo** |
| `+0x44`, `+0x48` | flag e buffer do 3D estereoscópico |
| `+0x4C`, `+0x50` | versões do olho direito |

Dentro do `calcFbbs`, o deslocamento entre os dois campos é uma instrução só:

```
stw   r9,0(r4)      campo 1 = base
bne   +8            se HorVer[0x20] != 0
add   r9,r9,r31       campo 2 = base + UMA LINHA     <-- NOP aqui
stw   r9,0(r5)      campo 2
```

O padrão de instruções pra casar: `stw rS,0(rA)` / `bne +8` (`0x40820008`) / `b +8`
(`0x48000008`) / `add rD,rA,rB` com `rD == rA == rS`.

**Ele aparece duas vezes.** Um é o caminho do framebuffer principal (precedido de
`lwz rX,0x30(rY)`); o outro é do 3D estereoscópico e é **código morto** — o bloco é pulado
quando a flag de 3D é zero, o que é sempre. Patchear o primeiro. Errar isso produz um patch
que não muda nada e faz parecer que a ideia não funciona.

## 4. Localizando o filtro escuro

O corpo da função compara dois bytes contra `0xFF`:

```
lwz    r0,4(r4)      80 04 00 04
cmpwi  r0,255        2C 00 00 FF
bne    +0x10         40 82 00 10
lwz    r0,8(r4)      80 04 00 08
cmpwi  r0,255        2C 00 00 FF
```

Dali, voltar **de trás pra frente** até o prólogo da função `stwu r1,-32(r1)` (`9421FFE0`) e
escrever `blr` (`4E800020`) por cima.

Um detalhe que vale saber: depois de aplicado, o `blr` sobrescreve exatamente o prólogo que
você procurava, então uma checagem ingênua de "o prólogo está lá?" reporta um build já
patcheado como *não encontrado*. Procurar por um ou por outro, e dizer qual.

Confirmado in-game em hardware real (Majora's Mask, tradução PT-BR) e localizado corretamente
nos 8 builds de emulador disponíveis aqui.

Crédito do método: NoobletCheese / Maeson, como implementado no FriishProduce.

---

## 5. O que estava errado antes

Dez becos sem saída, mantidos porque conhecê-los vale mais que o patch que funcionou.

| hipótese | como ela morreu |
|---|---|
| pôr `efbHeight` em 240 e deixar o display copy reduzir | **tela preta.** O display copy do GX não faz downscale vertical, só upscale. É limite de hardware, não bug |
| pôr `efbHeight` = `xfbHeight` = 240 | boota, UI correta, mas o jogo sai com **zoom** — o emulador continua desenhando 480 linhas num EFB de 240, então corta em vez de reduzir |
| a caixa de clip `-640,-480,640,480` do `data4` é o viewport | patcheei; nada mudou |
| `field_rendering = 1` | nada mudou |
| injetar uma chamada a `GXSetDispCopyYScale(0.5)` | impossível — ver a primeira linha; injeção de código nenhuma resolve limite de hardware |
| ligar o deflicker pra esconder o tremor | funciona, mas suaviza a imagem. Rejeitado: 240p borrado perde pra 480i nítido |
| o ponteiro de campo `+0x48` é o campo de baixo | é o **buffer do 3D estereoscópico**, num bloco que nunca executa. Adivinhei a semântica pelo offset em vez de rastrear o chamador |
| "o emulador nunca chama `GXSetDispCopyYScale`" | generalizei a partir de **um** de quatro sítios de display copy. Dois deles leem as duas alturas e calculam escala. O resultado empírico não muda, mas não tratar `efb == xfb` como lei universal |
| o emulador tem o tamanho da ROM hardcoded | Mario Kart 64 e Star Fox 64 (ROMs de 12 MB) **não contêm** nenhuma constante `0xC00000`. O tamanho vem do U8 |
| o emulador identifica ou valida a ROM dele | nenhum build contém o CRC1/CRC2 da própria ROM, o nome interno, o código de cartucho, a tabela de CRC do IPL3 nem tabela de seed de CIC |

**A que funcionou** foi manter `efb = xfb = 480` — deixando o desenho do emulador
completamente intocado — e fazer a decimação 2:1 no **VI**, via o stride de double-field, e
então remover o deslocamento de uma linha entre as duas bases de campo, pra que leiam o mesmo
conjunto de linhas. Geometria de 240p, imagem inteira, tremor zero, sem borrão.

---

## 6. Notas sobre injeção de ROM (não implementada aqui)

Mantidas porque foram medidas e as conclusões não são óbvias.

- **Os builds do emulador de N64 do VC vêm em revisões**, e a compatibilidade muda bastante
  entre elas. O FriishProduce classifica pelo prefixo do title id: rev 0 (F-Zero X,
  Super Mario 64), rev 1 (`NAB`/`NAC`/`NAD` — Star Fox 64, Mario Kart 64, Ocarina of Time),
  rev 2 (`NAK`/`NAJ`/`NAH` — Pokémon Snap, Sin & Punishment, Yoshi's Story), rev 3
  (`NA3`/`NAE`/`NAP`/`NAU`/`NAY`/`NAZ` — as que usam `romc`). **A rev 2 é a mais capaz.**
  Cuidado: classificar por title id **falha em WADs renomeadas** — um inject que trocou o ID
  reporta a revisão errada. O certo é identificar pelo binário do emulador.
- **Injeção cross-game funciona** — Kirby 64 numa base de Ocarina of Time bootou e rodou
  aqui. Mas é **por jogo**: a lista de compatibilidade do GBAtemp relata algo como 70% das
  ROMs travando na tela do Classic Controller, e essa taxa foi reproduzida aqui.
- **Nenhum jogo da Rareware foi lançado no VC do Wii** (a Microsoft comprou a Rare em 2002;
  o Donkey Kong 64 saiu no Wii U), então nenhum build de emulador foi ajustado contra um
  motor da Rare. O DK64 foi testado aqui em cinco bases diferentes, cobrindo CIC
  6101/6102/6105/6106 e slots de 8 a 32 MB: trava nas cinco, uns 6 segundos depois do boot, e
  então fica girando — laço infinito, não crash.
- O código do jogo no header do N64 fica em **`0x3C..0x3D`**, não em `0x3B`. `0x3B` é o tipo
  de mídia. Ler dois bytes de `0x3B` colide feio: Mario Kart `NKT` e Kirby `NK4` viram os
  dois `NK`, e Perfect Dark `NPD` vira `NP` e para de casar.
- Ler o header **depois** de converter pra z64. Lendo de um `.v64`/`.n64` o código sai com os
  bytes trocados — "Silicon Valley" vira `iSiloc naVllye`, código `VS` em vez de `SV`.
- Jogos que exigem os 8 MB do Expansion Pak: Donkey Kong 64 (`DO`), Majora's Mask (`ZS`),
  Perfect Dark (`PD`). Os outros apenas usam se estiver presente.
- Procurar as strings `EEPROM\0` / `SRAM\0` / `FLASH\0` no emulador **não** diz quais chips de
  save ele implementa — é uma tabela de nomes genérica presente em todo build. O Super Mario
  64 usa EEPROM de verdade e o build dele "reprova" nesse teste.

### romc, o formato comprimido de rom

Quase metade dos builds de emulador abre um arquivo chamado `romc` em vez de `rom` — mesma
rom, guardada comprimida. Cabeçalho, confirmado contra WADs de varejo e contra o `romchu.c`
do gzinject:

```
bytes 0..2   tamanho descomprimido / 64, big endian
byte  3      tipo
```

O Paper Mario de varejo lê `0x0A0000 * 64` = 40 MB, o Majora's Mask `0x080000 * 64` = 32 MB.

**As bases de varejo trazem tipo 1 e tipo 2. Nenhum compressor público produz tipo 2** — o
`romc.exe` do Jurai emite tipo 1, o `romc0.exe` emite tipo 0 (armazenado), e o `romchu` só
descomprime tipo 2. O FriishProduce tem a mesma limitação.

**Tipo 1 funciona numa base de tipo 2.** Verificado in-game: Bomberman 64 injetado com romc
tipo 1 nas bases Bomberman Hero e Ogre Battle 64 — ambas tipo 2 — bootou e rodou. O byte de
tipo não decide o descompressor. Isso não parece estar documentado em nenhum outro lugar.

Vale fazer ao gravar um romc: descomprimir a própria saída com a mesma ferramenta e comparar
com a entrada antes de empacotar. Um romc errado em silêncio gera um canal que boota e morre
depois, que é a pior coisa de depurar.

Duas peculiaridades por base:

- A base do **Bomberman Hero** (`NA3`) não inicia se o cart code da rom em `0x3B` não for
  `NBD`. Um byte. O FriishProduce faz o mesmo.
- **A compatibilidade é por jogo, e a lista do GBAtemp está certa.** O Bomberman 64 consta lá
  como funcionando nas bases Bomberman Hero e Ogre Battle com "glitchy screen", e é
  exatamente o que ele faz. Falhou nas dez bases de `rom` cru testadas aqui, cobrindo
  revisões 0, 1 e 2 do emulador e slots de 8 a 32 MB. Também rodou no **Mario Party 2**, que
  aquela lista não menciona.

---

## 7. Verificando um build no Dolphin

O Dolphin roda WADs de VC direto (`Dolphin.exe -b -e arquivo.wad`) e instala na NAND dele,
o que dá uma bancada de teste utilizável. Detalhes que custaram caro:

- Um canal rodando emite `IOS_ES ReadContent` continuamente, enquanto o emulador streama a
  ROM da NAND. Um travado despenca pra perto de zero. Ligar o canal de log `IOS_ES` **e pôr
  `Verbosity = 4`** — `ReadContent` é nível INFO e é invisível em NOTICE.
- **Esse sinal não é veredito sozinho.** Um Kirby 64 funcionando marcou pico de 76 leituras/s
  enquanto um Majora's Mask funcionando sustentou 570. Um limiar calibrado em um jogo só
  descarta sucessos reais.
- O patch de 240p faz o Dolphin renderizar **magenta** — a emulação de VI dele não lida com
  double-strike. A emulação continua correta. Teste injeção com o patch **desligado** se
  quiser ver a imagem.
- A NAND virtual do Dolphin enche rápido, a ~40 MB por canal, e NAND cheia faz canais falharem
  de um jeito idêntico a patch quebrado. Limpar `Wii/title/00010001/<id>` entre as rodadas.
- O GDB stub do Dolphin (`[General] GDBPort` no `Dolphin.ini`) funciona com o
  `powerpc-eabi-gdb`. Duas armadilhas: ele aceita **uma** conexão, então testar a porta com
  `/dev/tcp` antes consome ela e o gdb depois leva timeout; e o `GDBPort` fica gravado no ini,
  congelando **todo** boot seguinte enquanto espera um debugger.
