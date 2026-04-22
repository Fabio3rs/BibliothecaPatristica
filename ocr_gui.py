import io
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
import difflib
from pathlib import Path
import argparse
import json
import re
import unicodedata
import os
import argparse
import json
import re
import unicodedata
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
import difflib
from pathlib import Path
import xml.etree.ElementTree as ET

# Caminho base dos volumes
BASE_DIR = Path("teste")

# Funções utilitárias


def get_volumes():
    return sorted([d for d in BASE_DIR.iterdir() if d.is_dir()])


def get_pages(volume):
    text_dir = volume / "text"
    if not text_dir.exists():
        return []
    return sorted(text_dir.glob("*.txt"))


def get_image_for_page(volume, txt_path):
    # Tenta encontrar a imagem correspondente pelo sufixo
    page_num = txt_path.stem.split("-")[-1]
    img_dir = volume / "images"
    if not img_dir.exists():
        return None
    for img in img_dir.glob(f"*-{page_num}.png"):
        return img
    return None


def read_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def diff_text(a, b):
    # Gera diff HTML simples
    d = difflib.HtmlDiff()
    return d.make_table(
        a.splitlines(), b.splitlines(), "Antes", "Depois", context=True, numlines=2
    )


def normalize_whitespace(text: str) -> str:
    # normaliza espaços, quebras e alguns caracteres comuns
    t = text.replace("\u00a0", " ")
    t = unicodedata.normalize("NFC", t)
    # smart quotes -> ascii quotes
    t = (
        t.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    # múltiplos espaços para um
    t = re.sub(r"[ \t]+", " ", t)
    # remove espaços no fim das linhas
    t = "\n".join([ln.rstrip() for ln in t.splitlines()])
    return t


def _escape_bare_ampersands(text: str) -> str:
    """Escapa '&' que não fazem parte de uma entidade XML válida (ex: &amp; &#123; &lt;).
    A LLM ocasionalmente emite '&' solto no meio do texto, o que torna o XML malformado.
    """
    return re.sub(
        r"&(?!(?:[a-zA-Z][a-zA-Z0-9]*|#[0-9]+|#x[0-9a-fA-F]+);)", "&amp;", text
    )

def seems_like_ends_with_abbreviation(text: str) -> bool:
    """Verifica se o texto parece concluir com uma palavra de abreviação"""
    text = text.strip()
    text = text.split()
    return text[-1] in ("etc.", "i.e.", "e.g.") or len(text[-1]) < 3

def seems_unfinished_text(text: str) -> bool:
    """Verifica se o texto parece estar inacabado."""
    text = text.strip()
    return text.endswith(("-", "—", "…", "...", "(", "[", "{", "<")) and (seems_like_ends_with_abbreviation(text) or not text.endswith("."))


def auto_fix_text(text: str, path: str):
    fixes = []
    orig = text
    t = text

    resxml = None

    try:
        sanitized = _escape_bare_ampersands(text)
        resxml = ET.parse(io.StringIO(sanitized))
    except Exception as e:
        return t, fixes

    if resxml is None:
        return t, fixes

    changed = False

    to_search = ["A ", "B ", "C ", "D "]
    found = [False, False, False, False]

    nota_marginal_letra_encontrada = False
    for bloco in resxml.findall(".//bloco[@tipo='nota_marginal']"):
        if bloco is None:
            continue

        text = (bloco.text or "").strip()
        if text and text[0] in "ABCD":
            nota_marginal_letra_encontrada = True

    if not nota_marginal_letra_encontrada:
        return t, fixes

    last_continuation = {

    }

    for bloco in resxml.findall(".//bloco[@tipo='texto_principal']"):
        if not bloco.text or len(bloco.text.strip()) == 0:
            continue

        script = bloco.get("script", "desconhecido")

        new_lines = bloco.text.splitlines()
        lines = [ln.strip() for ln in new_lines]

        local_changed = False
        first_text_line = False
        seems_unfinished = False
        for i, n in enumerate(lines):
            for j, s in enumerate(to_search):
                if len(n) == 0:
                    continue

                if not seems_unfinished and first_text_line and s == "A ":
                    continue

                first_text_line = True
                seems_unfinished = seems_unfinished_text(n)

                if n.startswith(s):
                    if found[j]:
                        fixes.append("duplicata " + s + "  " + n,)
                        print("Duplicata encontrada:", s, n, path, t)
                    else:
                        fixes.append("remoção " + s + "  " + n)
                        found[j] = True
                        # n = n[len(s) :]

                    changed = True
                    local_changed = True

                    new_lines[i] = new_lines[i].replace(s, "", 1)

        if local_changed:
            bloco.text = "\n".join(new_lines)

        if seems_unfinished_text(bloco.text):
            last_continuation[script] = True
        else:
            last_continuation[script] = False

    if changed:
        t = ET.tostring(resxml.getroot(), encoding="unicode")

    # Trim trailing whitespace / normalize
    # new_t = normalize_whitespace(t)
    # if new_t != orig:
    #     fixes.append('normalize_whitespace')
    #     t = new_t
    # # remove repeated blank lines (>2 -> 2)
    # new_t = re.sub(r'\n{3,}', '\n\n', t)
    # if new_t != t:
    #     fixes.append('collapse_blank_lines')
    #     t = new_t
    return t, fixes


def scan_pages(base_dir: Path):
    """Percorre volumes e páginas e retorna lista de tasks dicts:
    {'path': str(path), 'old': old_text, 'new': new_text, 'fixes': [...]}"""
    tasks = []
    if not base_dir.exists():
        return tasks
    for vol in sorted([d for d in base_dir.iterdir() if d.is_dir()]):
        if "/PG" in str(vol):
            # Temporário, pular a PG
            continue

        if "/PO" in str(vol):
            continue

        text_dir = vol / "text"
        if not text_dir.exists():
            continue
        for txt in sorted(text_dir.glob("*.txt")):
            old = read_file(txt)
            new, fixes = auto_fix_text(old, txt)

            if new != old and fixes:
                tasks.append({"path": str(txt), "old": old, "new": new, "fixes": fixes})
    return tasks


def evaluate_all(base_dir: Path):
    """Função de loop solicitada que avalia todo o material e retorna lista de dicts."""
    return scan_pages(base_dir)


class OCRGui(tk.Tk):
    def __init__(self, should_scan: bool = False):
        super().__init__()
        self.title("OCR Review GUI")
        self.geometry("1200x700")
        self.create_widgets()
        self.populate_volumes()
        # carrega tasks de avaliação

        self.tasks = []
        if should_scan:
            self.tasks = scan_pages(BASE_DIR)

        self.current_index = 0
        # label de progresso (colocado após criação dos widgets)
        self.progress_var = tk.StringVar()
        self.progress_label = ttk.Label(self.diff_col, textvariable=self.progress_var)
        self.progress_label.pack()
        # botão recusar e navegação
        ttk.Button(self.btn_frame, text="Recusar", command=self.reject_ocr).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(self.btn_frame, text="Anterior", command=self.prev_task).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(self.btn_frame, text="Próxima", command=self.next_task).pack(
            side=tk.LEFT, padx=5
        )
        # se houver tasks, carrega a primeira
        if self.tasks:
            self.load_task(0)

    def create_widgets(self):
        # Layout principal
        self.left_frame = ttk.Frame(self)
        self.left_frame.pack(side=tk.LEFT, fill=tk.Y)

        # Lista de volumes
        ttk.Label(self.left_frame, text="Volumes").pack()
        self.vol_list = tk.Listbox(self.left_frame, width=25)
        self.vol_list.pack(fill=tk.Y, expand=True)
        self.vol_list.bind("<<ListboxSelect>>", self.on_volume_select)

        # Lista de páginas
        ttk.Label(self.left_frame, text="Páginas").pack()
        self.page_list = tk.Listbox(self.left_frame, width=25)
        self.page_list.pack(fill=tk.Y, expand=True)
        self.page_list.bind("<<ListboxSelect>>", self.on_page_select)

        # Frame para visualização lado a lado
        self.viewer_frame = ttk.Frame(self)
        self.viewer_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Cada coluna: Imagem | OCR Antes | OCR Depois | Diff
        self.img_col = ttk.Frame(self.viewer_frame)
        self.ocr_antes_col = ttk.Frame(self.viewer_frame)
        self.ocr_depois_col = ttk.Frame(self.viewer_frame)
        self.diff_col = ttk.Frame(self.viewer_frame)

        # Ajuste de proporções: imagem maior, diff menor
        self.img_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=5, pady=5)
        self.ocr_antes_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.ocr_depois_col.pack(
            side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5
        )
        self.diff_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=5, pady=5)

        # Imagem
        ttk.Label(self.img_col, text="Imagem").pack()
        self.img_label = ttk.Label(self.img_col)
        self.img_label.pack(pady=10)

        # OCR antes
        ttk.Label(self.ocr_antes_col, text="OCR Antes").pack()
        self.ocr_antes = tk.Text(self.ocr_antes_col, height=35, width=45)
        self.ocr_antes.pack(fill=tk.BOTH, expand=True)

        # OCR depois
        ttk.Label(self.ocr_depois_col, text="OCR Depois (editável)").pack()
        self.ocr_depois = tk.Text(self.ocr_depois_col, height=35, width=45)
        self.ocr_depois.pack(fill=tk.BOTH, expand=True)

        # Diff
        ttk.Label(self.diff_col, text="Diff (colorido)").pack()
        self.diff_html = None
        self.diff_frame = None
        # O widget correto será criado dinamicamente em update_diff()

        # Botões
        self.btn_frame = ttk.Frame(self.diff_col)
        self.btn_frame.pack(pady=10)
        ttk.Button(
            self.btn_frame, text="Atualizar Diff", command=self.update_diff
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            self.btn_frame, text="Salvar OCR Corrigido", command=self.save_ocr
        ).pack(side=tk.LEFT, padx=5)

    def populate_volumes(self):
        self.vol_list.delete(0, tk.END)
        self.volumes = get_volumes()
        for v in self.volumes:
            self.vol_list.insert(tk.END, v.name)

    def on_volume_select(self, event):
        sel = self.vol_list.curselection()
        if not sel:
            return
        idx = sel[0]
        self.selected_volume = self.volumes[idx]
        self.page_list.delete(0, tk.END)
        self.pages = get_pages(self.selected_volume)
        for p in self.pages:
            self.page_list.insert(tk.END, p.name)

    def on_page_select(self, event):
        sel = self.page_list.curselection()
        if not sel:
            return
        idx = sel[0]
        self.selected_page = self.pages[idx]
        # Carrega imagem
        img_path = get_image_for_page(self.selected_volume, self.selected_page)
        if img_path and img_path.exists():
            img = Image.open(img_path)
            img.thumbnail((700, 1000))  # aumenta tamanho máximo da imagem
            self.tk_img = ImageTk.PhotoImage(img)
            self.img_label.config(image=self.tk_img)
        else:
            self.img_label.config(image="", text="[Sem imagem]")
        # Carrega OCR antes
        ocr_text = read_file(self.selected_page)
        self.ocr_antes.delete("1.0", tk.END)
        self.ocr_antes.insert(tk.END, ocr_text)
        # Inicialmente OCR depois = antes
        self.ocr_depois.delete("1.0", tk.END)
        self.ocr_depois.insert(tk.END, ocr_text)
        self.update_diff()

    def load_task(self, index: int):
        if not (0 <= index < len(self.tasks)):
            return
        self.current_index = index
        task = self.tasks[index]
        txt_path = Path(task["path"])
        self.selected_page = txt_path
        # set lists selection to that page if possível
        try:
            vol = txt_path.parents[1]
            if hasattr(self, "volumes") and vol in self.volumes:
                vi = self.volumes.index(vol)
                self.vol_list.selection_clear(0, tk.END)
                self.vol_list.selection_set(vi)
                self.on_volume_select(None)
                # procura page index
                for i, p in enumerate(self.pages):
                    if Path(p) == txt_path:
                        self.page_list.selection_clear(0, tk.END)
                        self.page_list.selection_set(i)
                        break
        except Exception:
            pass
        # carrega imagem
        try:
            img_path = get_image_for_page(txt_path.parents[1], txt_path)
        except Exception:
            img_path = None
        if img_path and img_path.exists():
            img = Image.open(img_path)
            img.thumbnail((700, 1000))
            self.tk_img = ImageTk.PhotoImage(img)
            self.img_label.config(image=self.tk_img)
        else:
            self.img_label.config(image="", text="[Sem imagem]")
        # carrega textos
        self.ocr_antes.delete("1.0", tk.END)
        self.ocr_antes.insert(tk.END, task["old"])
        self.ocr_depois.delete("1.0", tk.END)
        self.ocr_depois.insert(tk.END, task.get("new", task["old"]))
        self.update_diff()
        # atualiza progresso
        self.progress_var.set(f"{index+1} de {len(self.tasks)}")

    def prev_task(self):
        if not self.tasks:
            return
        i = max(0, self.current_index - 1)
        self.load_task(i)

    def next_task(self):
        if not self.tasks:
            return
        i = min(len(self.tasks) - 1, self.current_index + 1)
        self.load_task(i)

    def update_diff(self):
        antes = self.ocr_antes.get("1.0", tk.END)
        depois = self.ocr_depois.get("1.0", tk.END)
        html = diff_text(antes, depois)
        # Limpa widgets antigos
        if self.diff_html:
            self.diff_html.destroy()
            self.diff_html = None
        if self.diff_frame:
            self.diff_frame.destroy()
            self.diff_frame = None
        # Renderizar diff colorido de forma confiável usando um Text widget.
        # Observação: muitos renderizadores HTML para Tk não suportam o CSS
        # gerado por difflib.HtmlDiff, por isso geramos um diff por linhas
        # e aplicamos tags de cor diretamente.
        # usar fonte monoespaçada para alinhar colunas e garantir legibilidade
        self.diff_frame = tk.Text(
            self.diff_col, height=35, width=64, bg="#ffffff", font=("TkFixedFont", 10)
        )
        self.diff_frame.pack(fill=tk.BOTH, expand=True)
        # configura tags de cores
        # cores com fundo leve para garantir contraste mesmo em themes variados
        self.diff_frame.tag_configure("add", foreground="#006400", background="#e6ffe6")
        self.diff_frame.tag_configure("del", foreground="#8B0000", background="#ffe6e6")
        self.diff_frame.tag_configure("meta", foreground="#666666", background="#f4f4f4")
        self.diff_frame.tag_configure("ctx", foreground="#000000", background="#ffffff")

        d = difflib.unified_diff(antes.splitlines(), depois.splitlines(), lineterm="")
        # Inserir linha a linha e aplicar tags baseadas no primeiro caracter
        for line in d:
            if line.startswith("+") and not line.startswith("+++"):
                tag = "add"
            elif line.startswith("-") and not line.startswith("---"):
                tag = "del"
            elif line.startswith("@@"):
                tag = "meta"
            else:
                tag = "ctx"
            # captura posição ANTES do insert
            start = self.diff_frame.index("end-1c")
            self.diff_frame.insert(tk.END, line + "\n")
            end = self.diff_frame.index("end-1c")
            self.diff_frame.tag_add(tag, start, end)

    def save_ocr(self):
        if not hasattr(self, "selected_page"):
            messagebox.showerror("Erro", "Nenhuma página selecionada!")
            return
        novo_texto = self.ocr_depois.get("1.0", tk.END)
        write_file(self.selected_page, novo_texto)
        # atualiza task se presente
        for i, t in enumerate(self.tasks):
            if str(self.selected_page) == t["path"]:
                t["new"] = novo_texto
                t["fixes"] = t.get("fixes", []) + ["manual_save"]
                break
        messagebox.showinfo("Salvo", "OCR corrigido salvo com sucesso! " + str(self.selected_page))

    def reject_ocr(self):
        # marca como recusado e avança
        if not hasattr(self, "selected_page"):
            messagebox.showerror("Erro", "Nenhuma página selecionada!")
            return
        # registra em log
        logp = BASE_DIR / "review_log.jsonl"
        entry = {"path": str(self.selected_page), "action": "rejected"}
        try:
            with open(logp, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
        messagebox.showinfo("Recusado", "OCR marcado como recusado e não salvo.")
        # avança
        self.next_task()


def save_fixes(tasks):
    for task in tasks:
        if "new" in task:
            print(f"Salvando correções para {task['path']}")
            write_file(task["path"], task["new"])


def main_cli():
    parser = argparse.ArgumentParser(description="OCR review GUI / scanner")
    parser.add_argument(
        "--scan", action="store_true", help="Scan all texts"
    )
    parser.add_argument(
        "--scan-save", action="store_true", help="Scan all texts and save fixes"
    )
    parser.add_argument(
        "--base", type=str, default=str(BASE_DIR), help="Base directory for volumes"
    )
    args = parser.parse_args()

    if args.scan_save:
        tasks = evaluate_all(Path(args.base))
        save_fixes(tasks)
        return
    # if args.scan:
    #     tasks = evaluate_all(Path(args.base))
    #     print(json.dumps(tasks, ensure_ascii=False, indent=2))
    #     return

    app = OCRGui(should_scan=args.scan)
    app.mainloop()


if __name__ == "__main__":
    main_cli()
