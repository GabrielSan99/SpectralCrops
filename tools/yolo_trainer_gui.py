"""SpectralCrops -- Treinador YOLO (GUI)
==========================================

Ferramenta standalone que NAO faz parte do app Django e NAO roda no
Raspberry Pi (o Pi nao tem GPU, treinar aqui seria bem mais lento). Roda no
seu computador e treina um modelo YOLO de segmentacao ou deteccao a partir
do dataset .zip baixado em Annotations (botao "Exportar" -> Segmentacao ou
Deteccao) ou em Machine Learning.

Uso:
    1. pip install ultralytics
       (isso ja instala o PyTorch tambem; se voce tiver GPU NVIDIA, instale
       o PyTorch com suporte a CUDA primeiro pra treinar bem mais rapido --
       ver https://pytorch.org/get-started/locally/ -- e so depois
       "pip install ultralytics")
    2. python yolo_trainer_gui.py
    3. Extraia o .zip baixado do SpectralCrops numa pasta
    4. Clique em "Escolher data.yaml..." e selecione o data.yaml dessa pasta
    5. Escolha a tarefa (Segmentacao/Deteccao) -- tem que ser a MESMA do
       dataset que voce exportou -- ajuste os parametros se quiser (os
       padroes funcionam bem pra comecar) e clique em "Iniciar treinamento"
    6. Acompanhe o progresso na caixa de log. Ao final, clique em "Abrir
       pasta do modelo treinado" pra achar o best.pt e suba ele de volta em
       Machine Learning, no SpectralCrops.
"""
import os
import re
import sys
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

try:
    import torch
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

try:
    from ultralytics import YOLO
    HAS_ULTRALYTICS = True
except ImportError:
    HAS_ULTRALYTICS = False


MODEL_SIZES = {"Nano (mais rápido)": "n", "Small": "s", "Medium (mais preciso, mais lento)": "m"}
IMGSZ_OPTIONS = [320, 480, 640, 960]

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")  # cores/cursor que o tqdm do ultralytics manda pro terminal


class _QueueWriter:
    """Arquivo-like que junta o que o ultralytics escreve em stdout/stderr
    (incluindo as barras de progresso, que usam \\r sem \\n) e manda pra uma
    Queue como linhas completas -- ("line", texto) linha normal (fica no
    log), ("progress", texto) atualizacao de barra de progresso (sobrescreve
    a ultima linha em vez de acumular, senao o log vira um poema)."""

    def __init__(self, q):
        self.q = q
        self._buf = ""

    def write(self, s):
        if not s:
            return
        self._buf += _ANSI_RE.sub("", s)
        while True:
            idx_n = self._buf.find("\n")
            idx_r = self._buf.find("\r")
            if idx_n == -1 and idx_r == -1:
                break
            if idx_n != -1 and (idx_r == -1 or idx_n < idx_r):
                line, self._buf = self._buf[:idx_n], self._buf[idx_n + 1:]
                self.q.put(("line", line))
            else:
                line, self._buf = self._buf[:idx_r], self._buf[idx_r + 1:]
                self.q.put(("progress", line))

    def flush(self):
        pass


class _RedirectOutput:
    """Context manager que redireciona stdout/stderr pra uma Queue -- ver
    _QueueWriter. Usado so durante o treino, num thread separado, pra GUI
    mostrar o log em tempo real sem travar a interface principal."""

    def __init__(self, q):
        self.writer = _QueueWriter(q)
        self._old_stdout = None
        self._old_stderr = None

    def __enter__(self):
        self._old_stdout, self._old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = self.writer, self.writer
        return self

    def __exit__(self, exc_type, exc, tb):
        sys.stdout, sys.stderr = self._old_stdout, self._old_stderr
        return False


class TrainerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SpectralCrops — Treinador YOLO")
        self.geometry("640x640")
        self.minsize(580, 560)

        self.data_yaml = tk.StringVar()
        self.task = tk.StringVar(value="seg")
        self.model_size = tk.StringVar(value="Nano (mais rápido)")
        self.epochs = tk.IntVar(value=100)
        self.imgsz = tk.IntVar(value=640)
        self.batch = tk.IntVar(value=-1)
        self.force_cpu = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="Selecione o data.yaml pra começar.")

        self._log_queue = queue.Queue()
        self._training_thread = None
        self._last_save_dir = None
        self._last_line_was_progress = False

        self._build_ui()
        self._check_deps()
        self.after(150, self._poll_log_queue)

    # ── UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}

        frm_data = ttk.LabelFrame(self, text="1. Dataset")
        frm_data.pack(fill="x", **pad)
        ttk.Entry(frm_data, textvariable=self.data_yaml, state="readonly").pack(
            side="left", fill="x", expand=True, padx=(8, 4), pady=8)
        ttk.Button(frm_data, text="Escolher data.yaml…", command=self._pick_data_yaml).pack(
            side="left", padx=(0, 8), pady=8)

        frm_task = ttk.LabelFrame(self, text="2. Tarefa (tem que ser a mesma do dataset exportado)")
        frm_task.pack(fill="x", **pad)
        ttk.Radiobutton(frm_task, text="Segmentação (contorno)", variable=self.task, value="seg").pack(
            side="left", padx=8, pady=6)
        ttk.Radiobutton(frm_task, text="Detecção (caixa)", variable=self.task, value="det").pack(
            side="left", padx=8, pady=6)

        frm_params = ttk.LabelFrame(self, text="3. Parâmetros de treino")
        frm_params.pack(fill="x", **pad)
        self._param_row(frm_params, "Tamanho do modelo:",
                         ttk.Combobox(frm_params, textvariable=self.model_size,
                                      values=list(MODEL_SIZES.keys()), state="readonly", width=28))
        self._param_row(frm_params, "Épocas:",
                         ttk.Spinbox(frm_params, from_=1, to=1000, textvariable=self.epochs, width=10))
        self._param_row(frm_params, "Tamanho da imagem:",
                         ttk.Combobox(frm_params, textvariable=self.imgsz,
                                      values=IMGSZ_OPTIONS, state="readonly", width=10))
        self._param_row(frm_params, "Batch size (-1 = automático):",
                         ttk.Spinbox(frm_params, from_=-1, to=128, textvariable=self.batch, width=10))

        self.device_label = ttk.Label(frm_params, text="Verificando GPU…")
        self.device_label.pack(anchor="w", padx=8, pady=(4, 2))
        ttk.Checkbutton(frm_params, text="Forçar treino na CPU (mais lento)",
                        variable=self.force_cpu).pack(anchor="w", padx=8, pady=(0, 8))

        frm_run = ttk.Frame(self)
        frm_run.pack(fill="x", **pad)
        self.btn_train = ttk.Button(frm_run, text="🚀 Iniciar treinamento", command=self._start_training)
        self.btn_train.pack(side="left")
        ttk.Label(frm_run, textvariable=self.status_text).pack(side="left", padx=12)

        frm_log = ttk.LabelFrame(self, text="Progresso")
        frm_log.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(frm_log, height=14, wrap="none", state="disabled",
                                 bg="#0f1117", fg="#d1d5db", insertbackground="#d1d5db")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

        self.btn_open_folder = ttk.Button(self, text="📂 Abrir pasta do modelo treinado",
                                           command=self._open_result_folder, state="disabled")
        self.btn_open_folder.pack(pady=(0, 10))

    def _param_row(self, parent, label, widget):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=3)
        ttk.Label(row, text=label, width=26).pack(side="left")
        widget.pack(side="left")

    # ── Checagens ───────────────────────────────────────────────────────
    def _check_deps(self):
        if not HAS_ULTRALYTICS:
            messagebox.showerror(
                "Dependência faltando",
                "O pacote 'ultralytics' não está instalado nesse Python.\n\n"
                "Abra um terminal e rode:\n    pip install ultralytics\n\n"
                "e reabra esse programa.")
            self.btn_train.config(state="disabled")
            self.device_label.config(text="ultralytics não instalado.")
            return
        if HAS_TORCH and torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            self.device_label.config(text=f"✅ GPU detectada: {name}")
        else:
            self.device_label.config(text="⚠ Nenhuma GPU CUDA detectada — vai treinar na CPU (bem mais lento).")

    # ── Ações ───────────────────────────────────────────────────────────
    def _pick_data_yaml(self):
        path = filedialog.askopenfilename(
            title="Selecione o data.yaml do dataset exportado",
            filetypes=[("data.yaml", "data.yaml"), ("YAML", "*.yaml;*.yml"), ("Todos os arquivos", "*.*")])
        if path:
            self.data_yaml.set(path)
            self.status_text.set("Dataset selecionado. Ajuste os parâmetros e inicie o treino.")

    def _start_training(self):
        if not self.data_yaml.get():
            messagebox.showwarning("Falta o dataset", "Selecione o data.yaml primeiro.")
            return
        if self._training_thread and self._training_thread.is_alive():
            return
        self.btn_train.config(state="disabled")
        self.btn_open_folder.config(state="disabled")
        self.status_text.set("Treinando…")
        self._clear_log()
        self._training_thread = threading.Thread(target=self._run_training, daemon=True)
        self._training_thread.start()

    def _run_training(self):
        try:
            size = MODEL_SIZES[self.model_size.get()]
            suffix = "-seg" if self.task.get() == "seg" else ""
            checkpoint = f"yolo11{size}{suffix}.pt"
            use_gpu = HAS_TORCH and torch.cuda.is_available() and not self.force_cpu.get()
            device = 0 if use_gpu else "cpu"

            with _RedirectOutput(self._log_queue):
                model = YOLO(checkpoint)
                results = model.train(
                    data=self.data_yaml.get(),
                    epochs=int(self.epochs.get()),
                    imgsz=int(self.imgsz.get()),
                    batch=int(self.batch.get()),
                    device=device,
                    project=str(Path(self.data_yaml.get()).parent / "runs"),
                    name="spectralcrops",
                )

            save_dir = (getattr(results, "save_dir", None)
                        or getattr(getattr(model, "trainer", None), "save_dir", None)
                        or (Path(self.data_yaml.get()).parent / "runs" / "spectralcrops"))
            save_dir = Path(save_dir)
            best = save_dir / "weights" / "best.pt"
            self._last_save_dir = best.parent if best.exists() else save_dir
            self._log_queue.put(("line", f"\n✅ Treino concluído. Modelo salvo em: {best}"))
            self._log_queue.put(("done_ok", str(best)))
        except Exception as e:
            self._log_queue.put(("line", f"\n❌ Erro durante o treino: {e}"))
            self._log_queue.put(("done_err", str(e)))

    # ── Log / fila ──────────────────────────────────────────────────────
    def _poll_log_queue(self):
        try:
            while True:
                kind, payload = self._log_queue.get_nowait()
                if kind == "done_ok":
                    self._on_training_done(ok=True)
                elif kind == "done_err":
                    self._on_training_done(ok=False)
                else:
                    self._append_line(payload, progress=(kind == "progress"))
        except queue.Empty:
            pass
        self.after(150, self._poll_log_queue)

    def _on_training_done(self, ok):
        self.btn_train.config(state="normal")
        if ok:
            self.status_text.set("Treino concluído ✅")
            self.btn_open_folder.config(state="normal")
        else:
            self.status_text.set("Erro no treino ❌")

    def _append_line(self, text, progress=False):
        self.log_text.config(state="normal")
        if progress and self._last_line_was_progress:
            self.log_text.delete("end-2l", "end-1l")  # troca a ultima linha em vez de empilhar
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")
        self._last_line_was_progress = progress

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")
        self._last_line_was_progress = False

    def _open_result_folder(self):
        if not self._last_save_dir:
            return
        path = str(self._last_save_dir)
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: on nao-Windows isso nem existe, so entra aqui no Windows
        elif sys.platform == "darwin":
            subprocess.run(["open", path])
        else:
            subprocess.run(["xdg-open", path])


if __name__ == "__main__":
    app = TrainerApp()
    app.mainloop()
