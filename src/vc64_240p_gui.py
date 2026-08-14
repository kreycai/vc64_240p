#!/usr/bin/env python3
"""
vc64 240p -- janela unica: escolhe uma WAD de Virtual Console de N64, valida se
ela aceita o patch de 240p, e grava a versao 240p NA MESMA PASTA da original.

Sem injecao de ROM, sem escolha de base, sem catalogo. So a parte que esta
provada em hardware real.
"""
import os
import queue
import sys
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk                                        # noqa: E402
from tkinter import filedialog, ttk                         # noqa: E402

import vc64tool as T                                        # noqa: E402

APP = 'vc64 240p'
VERSION = '1.0'

L = {
    'pt': {
        'pick':      'Escolher WAD...',
        'nofile':    'Nenhuma WAD escolhida.',
        'key':       'Chave comum (common-key.bin):',
        'browse':    'Procurar...',
        'convert':   'Converter para 240p',
        'analysing': 'analisando...',
        'working':   'convertendo...',
        'log':       'Registro',
        'dark':      'Remover tambem o filtro escuro (deixa a imagem no brilho original)',
        'darkno':    'filtro escuro: alvo nao encontrado neste build',
        'darkdone':  'filtro escuro: ja removido',
        'tv':        'Formato de TV do console:',
        'okhdr':     'ESTA WAD ACEITA O PATCH DE 240p',
        'badhdr':    'ESTA WAD NAO ACEITA O PATCH',
        'donehdr':   'JA ESTA EM 240p',
        'unreadhdr': 'NAO CONSEGUI LER ESTA WAD',
        'saved':     'Pronto. Gravado em:',
        'samefolder': 'A WAD 240p e gravada na mesma pasta da original.',
        'replaces':  'Ela mantem o mesmo ID de canal, entao ao instalar SUBSTITUI o canal\n'
                     'original e preserva os saves. O arquivo original nao e alterado.',
        'need480i':  'Deixe o console em 480i. Em 480p o emulador escolhe outra entrada\n'
                     'da tabela de video, que nao e patcheada.',
        'nokey':     'Falta a common-key.bin. Gere uma vez com:  gzinject -a genkey\n'
                     '(ele pede pra digitar 45e e dar enter -- se voce nao digitar,\n'
                     'ele gera uma chave ERRADA dizendo que deu certo)',
        'hashbad':   'Os hashes desta WAD nao batem com o TMD. Recuso mexer nela.',
        'noemu':     'Nao achei o binario do emulador dentro desta WAD.',
        'notarget':  'Achei o emulador, mas nao os alvos do patch neste build.',
        'already':   'Os alvos do 240p nao foram encontrados -- normalmente porque a WAD\n'
                     'ja esta patcheada. O filtro escuro ainda pode ser removido.',
        'onlydark':  'JA ESTA EM 240p -- DA PRA REMOVER SO O FILTRO ESCURO',
        'nothing':   'Nada a fazer nesta WAD: ja esta em 240p e sem filtro escuro.',
        'convdark':  'Remover o filtro escuro',
        'l240':      '240p          ',
        'ldark':     'filtro escuro ',
        's_ok':      'APLICAVEL',
        's_done':    'ja aplicado, nada a fazer',
        's_gone':    'ja removido, nada a fazer',
        's_no':      'alvo NAO encontrado neste build',
    },
    'en': {
        'pick':      'Choose a WAD...',
        'nofile':    'No WAD chosen.',
        'key':       'Wii common key (common-key.bin):',
        'browse':    'Browse...',
        'convert':   'Convert to 240p',
        'analysing': 'analysing...',
        'working':   'converting...',
        'log':       'Log',
        'dark':      'Also remove the dark filter (restores the original brightness)',
        'darkno':    'dark filter: target not found in this build',
        'darkdone':  'dark filter: already removed',
        'tv':        "Console's TV format:",
        'okhdr':     'THIS WAD ACCEPTS THE 240p PATCH',
        'badhdr':    'THIS WAD DOES NOT ACCEPT THE PATCH',
        'donehdr':   'ALREADY 240p',
        'unreadhdr': 'COULD NOT READ THIS WAD',
        'saved':     'Done. Written to:',
        'samefolder': 'The 240p WAD is written to the same folder as the original.',
        'replaces':  'It keeps the same channel id, so installing it REPLACES the original\n'
                     'channel and keeps your saves. The original file is left untouched.',
        'need480i':  'Set the console to 480i. In 480p the emulator picks a different entry\n'
                     'in the video mode table, which is not patched.',
        'nokey':     'common-key.bin is missing. Generate it once with:  gzinject -a genkey\n'
                     '(it asks you to type 45e and press enter -- if you do not type it,\n'
                     'it produces a WRONG key while reporting success)',
        'hashbad':   'This WAD\'s hashes do not match its TMD. Refusing to touch it.',
        'noemu':     'Could not find the emulator binary inside this WAD.',
        'notarget':  'Found the emulator, but not the patch targets in this build.',
        'already':   'The 240p targets were not found, usually because the WAD is already\n'
                     'patched. The dark filter can still be removed.',
        'onlydark':  'ALREADY 240p -- THE DARK FILTER CAN STILL BE REMOVED',
        'nothing':   'Nothing to do: already 240p and the dark filter is already gone.',
        'convdark':  'Remove the dark filter',
        'l240':      '240p        ',
        'ldark':     'dark filter ',
        's_ok':      'CAN BE APPLIED',
        's_done':    'already applied, nothing to do',
        's_gone':    'already removed, nothing to do',
        's_no':      'target NOT found in this build',
    },
}

BG = '#1e1e22'
FG = '#e8e8ea'
SUB = '#9a9aa2'
GREEN = '#2e7d32'
RED = '#b3261e'
AMBER = '#8a6d1f'
BLUE = '#2f4f7f'


def app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


class Gui:
    def __init__(self, root):
        self.root = root
        self.lang = 'pt'
        self.wad = None
        self.state = None
        self.q = queue.Queue()
        root.title(f'{APP} {VERSION}')
        root.configure(bg=BG)
        root.geometry('720x660')
        root.minsize(640, 600)

        top = tk.Frame(root, bg=BG)
        top.pack(fill='x', padx=16, pady=(14, 6))
        tk.Label(top, text=APP, bg=BG, fg=FG,
                 font=('Segoe UI', 17, 'bold')).pack(side='left')
        self.langbtn = tk.Button(top, text='EN', width=4, relief='flat',
                                 bg='#33333a', fg=FG, activebackground='#44444c',
                                 command=self.toggle_lang, cursor='hand2')
        self.langbtn.pack(side='right')

        self.sub = tk.Label(root, text='', bg=BG, fg=SUB, justify='left',
                            font=('Segoe UI', 9))
        self.sub.pack(fill='x', padx=16, anchor='w')

        # ---- arquivo
        f = tk.Frame(root, bg=BG)
        f.pack(fill='x', padx=16, pady=(14, 4))
        self.pickbtn = tk.Button(f, text='', command=self.pick, relief='flat',
                                 bg=BLUE, fg='white', activebackground='#3d6499',
                                 font=('Segoe UI', 10, 'bold'), padx=14, pady=7,
                                 cursor='hand2')
        self.pickbtn.pack(side='left')
        self.fname = tk.Label(f, text='', bg=BG, fg=SUB, anchor='w',
                              font=('Segoe UI', 9))
        self.fname.pack(side='left', padx=12, fill='x', expand=True)

        # ---- veredito
        self.panel = tk.Frame(root, bg=BG, height=128)
        self.panel.pack(fill='x', padx=16, pady=10)
        self.panel.pack_propagate(False)
        self.vhdr = tk.Label(self.panel, text='', bg=BG, fg=FG, anchor='w',
                             font=('Segoe UI', 11, 'bold'))
        self.vhdr.pack(fill='x', padx=12, pady=(10, 2))
        self.vtxt = tk.Label(self.panel, text='', bg=BG, fg=FG, anchor='w',
                             justify='left', font=('Consolas', 9))
        self.vtxt.pack(fill='both', padx=12, pady=(0, 8))

        # ---- opcoes
        o = tk.Frame(root, bg=BG)
        o.pack(fill='x', padx=16)
        self.tvlbl = tk.Label(o, text='', bg=BG, fg=SUB, font=('Segoe UI', 9))
        self.tvlbl.pack(side='left')
        self.tv = ttk.Combobox(o, values=['NTSC', 'MPAL'], width=7, state='readonly')
        self.tv.set('NTSC')
        self.tv.pack(side='left', padx=8)

        k = tk.Frame(root, bg=BG)
        k.pack(fill='x', padx=16, pady=(8, 0))
        self.keylbl = tk.Label(k, text='', bg=BG, fg=SUB, font=('Segoe UI', 9))
        self.keylbl.pack(side='left')
        self.keyvar = tk.StringVar(value=os.path.join(app_dir(), 'common-key.bin'))
        tk.Entry(k, textvariable=self.keyvar, bg='#2a2a30', fg=FG, relief='flat',
                 insertbackground=FG).pack(side='left', fill='x', expand=True, padx=8)
        self.keybtn = tk.Button(k, text='...', width=4, relief='flat', bg='#33333a',
                                fg=FG, command=self.pick_key, cursor='hand2')
        self.keybtn.pack(side='left')

        # O tkinter pinta o texto de um botao desabilitado com 'disabledforeground',
        # que no Windows sai quase da cor do fundo -- verde sobre verde, ilegivel.
        # Por isso a cor de fundo E a do texto sao trocadas junto com o estado,
        # sempre pelo set_go() abaixo, nunca por configure(state=...) direto.
        self.darkvar = tk.BooleanVar(value=False)
        self.darkbox = tk.Checkbutton(root, variable=self.darkvar, text='', bg=BG, fg=FG,
                                      selectcolor='#2a2a30', activebackground=BG,
                                      activeforeground=FG, anchor='w',
                                      font=('Segoe UI', 9), state='disabled',
                                      disabledforeground='#6e6e78')
        self.darkbox.pack(fill='x', padx=14, pady=(10, 0))

        self.gobtn = tk.Button(root, text='', command=self.convert, relief='flat',
                               font=('Segoe UI', 11, 'bold'), pady=10,
                               cursor='hand2', bd=0, highlightthickness=0,
                               disabledforeground='#6e6e78')
        self.gobtn.pack(fill='x', padx=16, pady=14)
        self.set_go(False)

        self.loglbl = tk.Label(root, text='', bg=BG, fg=SUB, anchor='w',
                               font=('Segoe UI', 9))
        self.loglbl.pack(fill='x', padx=16)
        self.log = tk.Text(root, height=10, bg='#141417', fg='#c9c9d0', relief='flat',
                           font=('Consolas', 9), wrap='word')
        self.log.pack(fill='both', expand=True, padx=16, pady=(2, 14))
        self.log.configure(state='disabled')

        self.retext()
        self.root.after(100, self.drain)

    # ------------------------------------------------------------------ i18n
    def t(self, k):
        return L[self.lang][k]

    def toggle_lang(self):
        self.lang = 'en' if self.lang == 'pt' else 'pt'
        self.langbtn.configure(text='PT' if self.lang == 'en' else 'EN')
        self.retext()
        if self.wad:
            self.analyse()

    def retext(self):
        self.pickbtn.configure(text=self.t('pick'))
        self.set_go(getattr(self, '_go_enabled', False))
        self.keylbl.configure(text=self.t('key'))
        self.tvlbl.configure(text=self.t('tv'))
        self.loglbl.configure(text=self.t('log'))
        self.darkbox.configure(text=self.t('dark'))
        self.sub.configure(text=self.t('samefolder') + '\n' + self.t('replaces'))
        if not self.wad:
            self.fname.configure(text=self.t('nofile'))

    # ------------------------------------------------------------------ log
    def say(self, msg=''):
        self.log.configure(state='normal')
        self.log.insert('end', msg + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == 'log':
                    self.say(payload)
                elif kind == 'verdict':
                    self.show_verdict(*payload)
                elif kind == 'onlydark':
                    self.darkvar.set(True)
                    self.set_go(True, self.t('convdark'))
                elif kind == 'dark':
                    if payload == 'can-remove':
                        self.darkbox.configure(state='normal', fg=FG)
                    else:
                        self.darkvar.set(False)
                        self.darkbox.configure(state='disabled')
                elif kind == 'busy':
                    self.set_go(False, payload)
        except queue.Empty:
            pass
        self.root.after(100, self.drain)

    # ------------------------------------------------------------------ ui
    def pick(self):
        p = filedialog.askopenfilename(title=self.t('pick'),
                                       filetypes=[('Wii WAD', '*.wad'), ('*', '*')])
        if not p:
            return
        self.wad = p
        self.fname.configure(text=os.path.basename(p))
        self.analyse()

    def pick_key(self):
        p = filedialog.askopenfilename(title='common-key.bin',
                                       filetypes=[('key', '*.bin'), ('*', '*')])
        if p:
            self.keyvar.set(p)
            if self.wad:
                self.analyse()

    def set_go(self, enabled, text=None):
        """Estado E cores do botao juntos -- ver o comentario onde ele e criado."""
        self._go_enabled = enabled
        if enabled:
            self.gobtn.configure(state='normal', bg=GREEN, fg='#ffffff',
                                 activebackground='#3a9440', activeforeground='#ffffff')
        else:
            self.gobtn.configure(state='disabled', bg='#3a3a42', fg='#6e6e78')
        self.gobtn.configure(text=text if text is not None else self.t('convert'))

    def show_verdict(self, colour, header, body, can_go):
        self.panel.configure(bg=colour)
        self.vhdr.configure(bg=colour, text=header)
        self.vtxt.configure(bg=colour, text=body)
        self.set_go(can_go)

    # ------------------------------------------------------------------ work
    def analyse(self):
        self.set_go(False)
        self.show_verdict(BG, self.t('analysing'), '', False)
        threading.Thread(target=self._analyse, daemon=True).start()

    def _analyse(self):
        try:
            key = self.keyvar.get()
            if not os.path.exists(key):
                self.q.put(('verdict', (RED, self.t('unreadhdr'), self.t('nokey'), False)))
                return
            v = T.verdict(self.wad, key, self.tv.get())
            self.state = v
            if not v['ok']:
                reason = v.get('reason') or self.t('noemu')
                self.q.put(('verdict', (RED, self.t('unreadhdr'), reason, False)))
                self.q.put(('log', f'-- {os.path.basename(self.wad)}: {reason}'))
                return
            if not v['hashes']:
                self.q.put(('verdict', (RED, self.t('unreadhdr'), self.t('hashbad'), False)))
                return
            # dois patches independentes: dizer o estado de CADA um, senao nao da
            # pra saber o que o botao vai fazer
            s240 = self.t('s_ok') if v['patchable'] else self.t('s_done')
            sdark = {'can-remove': self.t('s_ok'),
                     'already-removed': self.t('s_gone'),
                     'not-found': self.t('s_no')}[v['dark']]
            info = (f"{self.t('l240')}: {s240}\n"
                    f"{self.t('ldark')}: {sdark}\n"
                    f"canal {v['code']}   "
                    f"{'LZ77' if v['compressed'] else 'DOL cru / raw DOL'}")
            self.q.put(('dark', v['dark']))
            can_dark = v['dark'] == 'can-remove'
            if v['patchable']:
                self.q.put(('verdict', (GREEN, self.t('okhdr'),
                                        info + '\n' + self.t('need480i'), True)))
            elif can_dark:
                # ja esta em 240p, mas o filtro escuro continua aplicavel -- deixar seguir
                self.q.put(('verdict', (AMBER, self.t('onlydark'), info, True)))
                self.q.put(('onlydark', True))
            else:
                self.q.put(('verdict', (AMBER, self.t('donehdr'),
                                        info + '\n' + self.t('nothing'), False)))
            self.q.put(('log', f"-- {os.path.basename(self.wad)}  [{v['code']}]  "
                               f"{'patchavel / patchable' if v['patchable'] else 'sem alvos / no targets'}"))
        except Exception:
            self.q.put(('verdict', (RED, self.t('unreadhdr'),
                                    traceback.format_exc(limit=1), False)))

    def convert(self):
        self.q.put(('busy', self.t('working')))
        threading.Thread(target=self._convert, daemon=True).start()

    def _convert(self):
        try:
            key = T.load_key(self.keyvar.get())
            w = T.Wad(self.wad, key)
            idx, emu, comp = w.find_emulator()
            t = T.Targets(emu, self.tv.get())
            ops = []
            dark_off = None
            if t.ok:
                # Todos os formatos entrelacados, nao so o selecionado: quem
                # escolhe qual struct e lida e a configuracao do console, nao a
                # WAD. Patchear um formato que o console nao usa e inerte;
                # patchear so o errado sai em silencio, com o programa dizendo
                # que deu certo.
                modos = ', '.join(m['name'] for m in t.interlaced)
                self.q.put(('log', f"   render mode {modos} @ 0x{t.mode['off']:06X}"
                                   f"   NOP @ 0x{t.nop:06X}"))
                ops = list(t.patch_ops(every_tv=True))
            else:
                self.q.put(('log', '   ' + self.t('already')))
            if self.darkvar.get():
                dk = T.find_dark_filter(emu)
                if dk is None:
                    self.q.put(('log', '   ' + self.t('darkno')))
                else:
                    dark_off = dk
                    ops = list(ops) + [(dk, 4, T._BLR)]
                    self.q.put(('log', f'   filtro escuro / dark filter: blr @ 0x{dk:06X}'))
            if not ops:
                self.q.put(('log', self.t('nothing')))
                self.q.put(('verdict', (AMBER, self.t('donehdr'), self.t('nothing'), False)))
                return
            contents = dict(w.contents)
            contents[idx] = T.apply_ops(emu, ops)

            # Nomear pelo que o patch REALMENTE fez, e nao repetir sufixo que ja
            # esta no nome -- senao reaplicar so o filtro numa WAD ja convertida
            # produzia "... 240p 240p".
            base, ext = os.path.splitext(self.wad)
            did_240 = bool(t.ok)
            did_dark = any(o[0] == dark_off for o in ops) if dark_off is not None else False
            suffix = ''
            if did_240 and not base.lower().rstrip().endswith('240p'):
                suffix += ' 240p'
            if did_dark:
                suffix += ' sem filtro' if self.lang == 'pt' else ' no dark filter'
            out = f'{base}{suffix}{ext}'
            n = 2
            while os.path.exists(out):
                out = f'{base}{suffix} ({n}){ext}'
                n += 1
            written = w.write(out, title_id=None, contents=contents)

            # confere o que foi gravado, em vez de confiar
            chk = T.Wad(out, key)
            _i2, emu2, _c2 = chk.find_emulator()
            diff = sum(1 for a, b in zip(emu, emu2) if a != b)
            self.q.put(('log', f"   verificado: hashes {'OK' if chk.sha_ok else 'FALHOU'}, "
                               f"{diff} bytes alterados no emulador"))
            self.q.put(('log', f"{self.t('saved')} {out}  ({written:,} bytes)".replace(',', '.')))
            self.q.put(('log', ''))
            self.q.put(('verdict', (GREEN, self.t('saved'), os.path.basename(out), False)))
        except SystemExit as e:
            self.q.put(('log', f'erro / error: {e}'))
            self.q.put(('verdict', (RED, self.t('badhdr'), str(e), False)))
        except Exception:
            tb = traceback.format_exc()
            self.q.put(('log', tb))
            self.q.put(('verdict', (RED, self.t('badhdr'), tb.splitlines()[-1], False)))


def main():
    root = tk.Tk()
    Gui(root)
    root.mainloop()


if __name__ == '__main__':
    main()
