import tkinter as tk
from tkinter import ttk, messagebox
import serial
import serial.tools.list_ports
import time
import threading
import json
import re
import os
from datetime import datetime, timedelta

class AlmoxarifadoCNCApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Almoxarifado CNC - ESP32 (WMS System)")
        self.root.geometry("1280x850")
        self.root.configure(bg="#222222")

        # Configurações de Estilo (Dark Mode)
        self.style = ttk.Style()
        self.style.theme_use('default')
        self.style.configure("TNotebook", background="#222222", borderwidth=0)
        self.style.configure("TNotebook.Tab", background="#333333", foreground="white", padding=[12, 6])
        self.style.map("TNotebook.Tab", background=[("selected", "#2196f3")])
        self.style.configure("Treeview", background="#333333", foreground="white", fieldbackground="#333333", rowheight=25)
        self.style.map('Treeview', background=[('selected', '#2196f3')])

        self.ser = None
        self.serial_thread = None
        self.ler_serial_flag = False
        self.serial_wait_event = threading.Event()
        
        self.automacao_rodando = False
        self.caixa_selecionada = None

        self.arquivo_dados = "cnc_almoxarifado.json"
        self.base_coords = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        self.caixas = {}
        self.logs = [] # Lista de histórico de logs
        
        self.gerar_caixas_padrao()
        self.carregar_dados()
        self.setup_ui()

    def gerar_caixas_padrao(self):
        linhas = ['A', 'B', 'C', 'D']
        for l in linhas:
            for c in range(1, 5):
                pos = f"{l}{c}"
                self.caixas[pos] = {"status": "guardada", "x": 0.0, "z": 0.0, "itens": []}

    def checar_status_validade(self, data_str):
        if not data_str or data_str.upper() == "N/A":
            return "OK", "normal"
        try:
            dt_val = datetime.strptime(data_str, "%d/%m/%Y").date()
            hoje = datetime.now().date()
            if dt_val < hoje:
                return "VENCIDO", "vencido"
            elif dt_val <= hoje + timedelta(days=30):
                return "A VENCER (<30d)", "alerta"
            else:
                return "OK", "normal"
        except ValueError:
            return "DATA INVÁLIDA", "normal"

    def carregar_dados(self):
        if os.path.exists(self.arquivo_dados):
            try:
                with open(self.arquivo_dados, 'r') as f:
                    dados = json.load(f)
                    self.base_coords = dados.get("base", self.base_coords)
                    caixas_salvas = dados.get("caixas", {})
                    for k, v in caixas_salvas.items():
                        if k in self.caixas: self.caixas[k] = v
                    self.logs = dados.get("logs", [])
                    self.seq_retirada = dados.get("seq_retirada", "G0 X{X} Z{Z+15}\nG0 Z{Z}\nTOGGLE_D22\nG0 Z{Z+15}\nG0 X{BX} Z{BZ}\nG0 Y{BY-10}")
                    self.seq_devolucao = dados.get("seq_devolucao", "G0 Y{BY}\nG0 X{X} Z{Z+15}\nG0 Z{Z}\nTOGGLE_D22\nG0 Z{Z+15}")
            except Exception as e: print(f"Erro ao carregar JSON: {e}")
        else:
            self.seq_retirada = "G0 X{X} Z{Z+15}\nG0 Z{Z}\nTOGGLE_D22\nG0 Z{Z+15}\nG0 X{BX} Z{BZ}\nG0 Y{BY-10}"
            self.seq_devolucao = "G0 Y{BY}\nG0 X{X} Z{Z+15}\nG0 Z{Z}\nTOGGLE_D22\nG0 Z{Z+15}"

    def salvar_dados(self):
        dados = {
            "base": self.base_coords,
            "caixas": self.caixas,
            "logs": self.logs,
            "seq_retirada": self.txt_seq_retirada.get("1.0", tk.END).strip(),
            "seq_devolucao": self.txt_seq_devolucao.get("1.0", tk.END).strip()
        }
        with open(self.arquivo_dados, 'w') as f:
            json.dump(dados, f, indent=4)

    def registrar_log(self, tipo, caixa, item, qtd):
        data_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        registro = {
            "data_hora": data_hora,
            "tipo": tipo,
            "caixa": caixa,
            "item": item,
            "qtd": qtd
        }
        self.logs.insert(0, registro) # Registros mais recentes ficam no topo
        if hasattr(self, 'tree_logs'):
            self.atualizar_tabela_logs()

    def setup_ui(self):
        top_bar = tk.Frame(self.root, bg="#111111", height=40)
        top_bar.pack(fill="x", side="top")
        
        self.lbl_status = tk.Label(top_bar, text="Status: PRONTO", bg="#111111", fg="#4caf50", font=("Arial", 11, "bold"))
        self.lbl_status.pack(side="right", padx=20, pady=8)

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)

        self.tab_matriz = tk.Frame(notebook, bg="#222222")
        self.tab_pesquisa = tk.Frame(notebook, bg="#222222")
        self.tab_itens = tk.Frame(notebook, bg="#222222")
        self.tab_logs = tk.Frame(notebook, bg="#222222")
        self.tab_config = tk.Frame(notebook, bg="#222222")

        notebook.add(self.tab_matriz, text=" Matriz do Almoxarifado ")
        notebook.add(self.tab_pesquisa, text=" Pesquisa de Estoque ")
        notebook.add(self.tab_itens, text=" Cadastrar / Gerenciar Itens ")
        notebook.add(self.tab_logs, text=" Histórico de Logs ")
        notebook.add(self.tab_config, text=" Configurações & Serial ")

        self.setup_tab_matriz()
        self.setup_tab_pesquisa()
        self.setup_tab_itens()
        self.setup_tab_logs()
        self.setup_tab_config()

    # ========================== LÓGICA DE + e - ==========================
    def alterar_qtd(self, pos, idx, delta, callback_atualizar_tela):
        if pos not in self.caixas or idx >= len(self.caixas[pos]["itens"]): return
        
        item = self.caixas[pos]["itens"][idx]
        qtd_atual_caixa = sum(int(i['qtd']) for i in self.caixas[pos]["itens"])
        
        if delta > 0 and qtd_atual_caixa >= 8:
            messagebox.showwarning("Aviso", "A caixa já atingiu o limite máximo de 8 itens.")
            return

        nome_item = item['nome']
        tipo_mov = "Entrada" if delta > 0 else "Saída"
        qtd_mov = abs(delta)

        item['qtd'] += delta
        
        # Regra: Se a quantidade chegar a 0, exclui o item
        if item['qtd'] <= 0:
            del self.caixas[pos]["itens"][idx]
            
        self.registrar_log(tipo_mov, pos, nome_item, qtd_mov)
        self.salvar_dados()
        self.atualizar_pesquisa_estoque()
        self.atualizar_ui_matriz()
        callback_atualizar_tela()

    # ========================== ABA 1: MATRIZ ==========================
    def setup_tab_matriz(self):
        frame_grid = tk.Frame(self.tab_matriz, bg="#222222")
        frame_grid.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        
        self.frames_caixas = {}
        linhas = ['A', 'B', 'C', 'D']
        for row_idx, l in enumerate(linhas):
            for col_idx in range(1, 5):
                pos = f"{l}{col_idx}"
                cf = tk.Frame(frame_grid, bg="#3a3a3a", highlightbackground="#555555", highlightthickness=1)
                cf.grid(row=row_idx, column=col_idx-1, padx=3, pady=3, sticky="nsew")
                frame_grid.grid_rowconfigure(row_idx, weight=1)
                frame_grid.grid_columnconfigure(col_idx-1, weight=1)
                
                cf.bind("<Button-1>", lambda e, p=pos: self.selecionar_caixa(p))
                
                lbl_title = tk.Label(cf, text=pos, bg="#3a3a3a", fg="white", font=("Arial", 12, "bold"))
                lbl_title.place(relx=0.5, rely=0.35, anchor="center")
                lbl_title.bind("<Button-1>", lambda e, p=pos: self.selecionar_caixa(p))

                lbl_status = tk.Label(cf, text="[ VAZIA ]", bg="#3a3a3a", fg="#aaaaaa", font=("Arial", 9))
                lbl_status.place(relx=0.5, rely=0.6, anchor="center")
                lbl_status.bind("<Button-1>", lambda e, p=pos: self.selecionar_caixa(p))

                canvas_dot = tk.Canvas(cf, width=16, height=16, bg="#3a3a3a", highlightthickness=0)
                canvas_dot.place(x=8, rely=0.75)
                dot = canvas_dot.create_oval(2, 2, 14, 14, fill="#4caf50", outline="")
                canvas_dot.bind("<Button-1>", lambda e, p=pos: self.alternar_status_manual(p))

                self.frames_caixas[pos] = {"frame": cf, "lbl_status": lbl_status, "canvas": canvas_dot, "dot": dot}

        sidebar = tk.Frame(self.tab_matriz, bg="#2a2a2a", width=220)
        sidebar.pack(side="right", fill="y")
        
        tk.Label(sidebar, text="Controle de Caixa", bg="#2a2a2a", fg="white", font=("Arial", 11, "bold")).pack(pady=10)
        self.lbl_caixa_sel = tk.Label(sidebar, text="Caixa: Nenhuma", bg="#2a2a2a", fg="#00bcd4", font=("Arial", 14, "bold"))
        self.lbl_caixa_sel.pack(pady=10)

        tk.Button(sidebar, text="EXECUTAR HOMING (G28)", bg="#2196f3", fg="white", font=("Arial", 10, "bold"), command=self.enviar_homing).pack(fill="x", padx=10, pady=10, ipady=8)
        tk.Button(sidebar, text="RETIRAR CAIXA", bg="#ff9800", fg="black", font=("Arial", 10, "bold"), command=lambda: self.iniciar_movimento("retirada")).pack(fill="x", padx=10, pady=8, ipady=8)
        tk.Button(sidebar, text="GUARDAR CAIXA", bg="#4caf50", fg="black", font=("Arial", 10, "bold"), command=lambda: self.iniciar_movimento("devolucao")).pack(fill="x", padx=10, pady=8, ipady=8)
        tk.Button(sidebar, text="GERENCIAR ITENS", bg="#607d8b", fg="white", font=("Arial", 10, "bold"), command=self.abrir_popup_itens).pack(fill="x", padx=10, pady=8, ipady=8)

        self.atualizar_ui_matriz()

    def alternar_status_manual(self, pos):
        atual = self.caixas[pos]["status"]
        self.caixas[pos]["status"] = "retirada" if atual == "guardada" else "guardada"
        self.atualizar_ui_matriz()
        self.salvar_dados()

    def selecionar_caixa(self, pos):
        self.caixa_selecionada = pos
        self.lbl_caixa_sel.config(text=f"Caixa: {pos}")
        for p, d in self.frames_caixas.items():
            d["frame"].config(highlightbackground="#555555")
        self.frames_caixas[pos]["frame"].config(highlightbackground="#00bcd4")
        
        self.combo_pos.set(pos)
        self.atualizar_lista_itens()

    def atualizar_ui_matriz(self):
        for pos, dados in self.caixas.items():
            qtd_total = sum(int(item.get('qtd', 0)) for item in dados["itens"])
            txt = "[ VAZIA ]" if qtd_total == 0 else f"[ {qtd_total}/8 ITENS ]"
            
            self.frames_caixas[pos]["lbl_status"].config(text=txt)
            cor_dot = "#4caf50" if dados["status"] == "guardada" else "#f44336"
            self.frames_caixas[pos]["canvas"].itemconfig(self.frames_caixas[pos]["dot"], fill=cor_dot)

    # ========================== ABA 2: PESQUISA DE ESTOQUE ==========================
    def setup_tab_pesquisa(self):
        frame_top = tk.Frame(self.tab_pesquisa, bg="#222222")
        frame_top.pack(fill="x", padx=10, pady=10)

        tk.Label(frame_top, text="🔍 Buscar Item:", bg="#222222", fg="white", font=("Arial", 11)).pack(side="left", padx=5)
        self.ent_busca = tk.Entry(frame_top, width=30, font=("Arial", 11))
        self.ent_busca.pack(side="left", padx=5)
        self.ent_busca.bind("<KeyRelease>", lambda e: self.atualizar_pesquisa_estoque())

        tk.Button(frame_top, text="▶ BUSCAR CAIXA DO ITEM", bg="#ff9800", fg="black", font=("Arial", 10, "bold"), command=self.retirar_item_via_pesquisa).pack(side="right", padx=10)

        cols = ("Caixa", "Item", "Qtd", "Validade", "Situação Validade")
        self.tree_pesquisa = ttk.Treeview(self.tab_pesquisa, columns=cols, show="headings")
        
        for c in cols:
            self.tree_pesquisa.heading(c, text=c)
            self.tree_pesquisa.column(c, anchor="center")
        self.tree_pesquisa.column("Item", anchor="w", width=250)

        self.tree_pesquisa.tag_configure("vencido", foreground="#ff6b6b", font=("Arial", 10, "bold"))
        self.tree_pesquisa.tag_configure("alerta", foreground="#ffd166", font=("Arial", 10, "bold"))
        self.tree_pesquisa.tag_configure("normal", foreground="white")

        self.tree_pesquisa.pack(fill="both", expand=True, padx=10, pady=10)
        self.atualizar_pesquisa_estoque()

    def atualizar_pesquisa_estoque(self):
        for i in self.tree_pesquisa.get_children(): 
            self.tree_pesquisa.delete(i)
            
        termo = self.ent_busca.get().strip().lower()
        
        for pos, dados in self.caixas.items():
            for item in dados["itens"]:
                nome = item["nome"]
                if termo in nome.lower() or termo in pos.lower():
                    sit, tag = self.checar_status_validade(item.get("validade", "N/A"))
                    self.tree_pesquisa.insert("", "end", values=(pos, nome, item["qtd"], item.get("validade", "N/A"), sit), tags=(tag,))

    def retirar_item_via_pesquisa(self):
        sel = self.tree_pesquisa.selection()
        if not sel: return messagebox.showwarning("Aviso", "Selecione um item na tabela de pesquisa.")
        
        pos_caixa = self.tree_pesquisa.item(sel[0], 'values')[0]
        self.selecionar_caixa(pos_caixa)
        
        if self.caixas[pos_caixa]["status"] == "retirada":
            messagebox.showinfo("Aviso", f"A caixa {pos_caixa} já está na base de entrega!")
            self.abrir_popup_itens()
            return
            
        self.iniciar_movimento("retirada")

    # ========================== ABA 3: GERENCIAR ITENS ==========================
    def setup_tab_itens(self):
        frame_top = tk.Frame(self.tab_itens, bg="#222222")
        frame_top.pack(fill="x", padx=10, pady=10)

        tk.Label(frame_top, text="Posição:", bg="#222222", fg="white").pack(side="left")
        self.combo_pos = ttk.Combobox(frame_top, values=list(self.caixas.keys()), width=5, state="readonly")
        self.combo_pos.pack(side="left", padx=5)
        if self.caixas: self.combo_pos.current(0)
        self.combo_pos.bind("<<ComboboxSelected>>", lambda e: self.atualizar_lista_itens())

        tk.Label(frame_top, text="Item:", bg="#222222", fg="white").pack(side="left", padx=5)
        self.ent_item = tk.Entry(frame_top, width=18)
        self.ent_item.pack(side="left")

        tk.Label(frame_top, text="Qtd:", bg="#222222", fg="white").pack(side="left", padx=5)
        self.spin_qtd = ttk.Spinbox(frame_top, from_=1, to=8, width=4)
        self.spin_qtd.set(1)
        self.spin_qtd.pack(side="left")

        tk.Label(frame_top, text="Validade (DD/MM/AAAA):", bg="#222222", fg="white").pack(side="left", padx=5)
        self.ent_val = tk.Entry(frame_top, width=12)
        self.ent_val.pack(side="left")

        tk.Button(frame_top, text="Cadastrar Item", bg="#2196f3", fg="white", command=self.add_item).pack(side="left", padx=10)

        # Configuração da Lista de Itens Customizada
        self.container_itens = tk.Frame(self.tab_itens, bg="#222222")
        self.container_itens.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.canvas_itens = tk.Canvas(self.container_itens, bg="#333333", highlightthickness=0)
        self.scroll_y = tk.Scrollbar(self.container_itens, orient="vertical", command=self.canvas_itens.yview)
        
        self.frame_lista = tk.Frame(self.canvas_itens, bg="#333333")
        
        self.frame_lista.bind("<Configure>", lambda e: self.canvas_itens.configure(scrollregion=self.canvas_itens.bbox("all")))
        self.canvas_window = self.canvas_itens.create_window((0, 0), window=self.frame_lista, anchor="nw")
        self.canvas_itens.bind("<Configure>", lambda e: self.canvas_itens.itemconfig(self.canvas_window, width=e.width))
        
        self.canvas_itens.pack(side="left", fill="both", expand=True)
        self.scroll_y.pack(side="right", fill="y")
        self.canvas_itens.configure(yscrollcommand=self.scroll_y.set)

        self.atualizar_lista_itens()

    def add_item(self):
        pos = self.combo_pos.get()
        nome = self.ent_item.get().strip()
        try: qtd = int(self.spin_qtd.get())
        except: return messagebox.showerror("Erro", "Quantidade inválida.")
        val = self.ent_val.get().strip() or "N/A"

        if not nome: return messagebox.showerror("Erro", "Nome do item é obrigatório.")

        if val != "N/A":
            try: datetime.strptime(val, "%d/%m/%Y")
            except ValueError: return messagebox.showerror("Erro", "Formato de data inválido! Use DD/MM/AAAA.")
        
        qtd_atual = sum(int(i['qtd']) for i in self.caixas[pos]["itens"])
        if qtd_atual + qtd > 8:
            return messagebox.showerror("Erro", f"Limite excedido! A caixa possui {qtd_atual} itens (Máx 8).")

        self.caixas[pos]["itens"].append({"nome": nome, "qtd": qtd, "validade": val})
        self.registrar_log("Entrada", pos, nome, qtd)
        self.salvar_dados()
        self.atualizar_lista_itens()
        self.atualizar_pesquisa_estoque()
        self.atualizar_ui_matriz()
        self.ent_item.delete(0, tk.END)
        self.ent_val.delete(0, tk.END)

    def atualizar_lista_itens(self):
        for widget in self.frame_lista.winfo_children():
            widget.destroy()
            
        pos = self.combo_pos.get()
        if pos in self.caixas:
            for idx, item in enumerate(self.caixas[pos]["itens"]):
                row = tk.Frame(self.frame_lista, bg="#444444", pady=8, padx=10)
                row.pack(fill="x", pady=3, padx=5)
                
                val = item.get("validade", "N/A")
                sit, tag = self.checar_status_validade(val)
                cor_val = "#ff6b6b" if tag == "vencido" else "#ffd166" if tag == "alerta" else "#aaaaaa"
                
                tk.Label(row, text=item["nome"], bg="#444444", fg="white", font=("Arial", 12, "bold"), width=30, anchor="w").pack(side="left")
                tk.Label(row, text=f"Validade: {val} ({sit})", bg="#444444", fg=cor_val, width=30, anchor="w", font=("Arial", 10, "bold")).pack(side="left")
                
                tk.Button(row, text="  -  ", bg="#f44336", fg="white", font=("Arial", 12, "bold"), relief="flat", command=lambda i=idx: self.alterar_qtd(pos, i, -1, self.atualizar_lista_itens)).pack(side="left", padx=10)
                tk.Label(row, text=f"{item['qtd']}", bg="#444444", fg="white", font=("Arial", 14, "bold"), width=4).pack(side="left")
                tk.Button(row, text="  +  ", bg="#4caf50", fg="black", font=("Arial", 12, "bold"), relief="flat", command=lambda i=idx: self.alterar_qtd(pos, i, 1, self.atualizar_lista_itens)).pack(side="left", padx=10)

    # ========================== ABA 4: HISTÓRICO DE LOGS ==========================
    def setup_tab_logs(self):
        frame_top = tk.Frame(self.tab_logs, bg="#222222")
        frame_top.pack(fill="x", padx=10, pady=10)

        tk.Label(frame_top, text="🔍 Filtrar Log:", bg="#222222", fg="white", font=("Arial", 11)).pack(side="left", padx=5)
        self.ent_busca_log = tk.Entry(frame_top, width=25, font=("Arial", 11))
        self.ent_busca_log.pack(side="left", padx=5)
        self.ent_busca_log.bind("<KeyRelease>", lambda e: self.atualizar_tabela_logs())

        tk.Label(frame_top, text="Tipo:", bg="#222222", fg="white", font=("Arial", 11)).pack(side="left", padx=(15, 5))
        self.combo_tipo_log = ttk.Combobox(frame_top, values=["Todos", "Entrada", "Saída"], width=10, state="readonly")
        self.combo_tipo_log.set("Todos")
        self.combo_tipo_log.pack(side="left", padx=5)
        self.combo_tipo_log.bind("<<ComboboxSelected>>", lambda e: self.atualizar_tabela_logs())

        cols = ("Data e Hora", "Tipo", "Caixa", "Item", "Quantidade")
        self.tree_logs = ttk.Treeview(self.tab_logs, columns=cols, show="headings")
        
        for c in cols:
            self.tree_logs.heading(c, text=c)
            self.tree_logs.column(c, anchor="center")
        self.tree_logs.column("Item", anchor="w", width=250)
        self.tree_logs.column("Data e Hora", width=180)

        self.tree_logs.tag_configure("Entrada", foreground="#4caf50", font=("Arial", 10, "bold"))
        self.tree_logs.tag_configure("Saída", foreground="#ff6b6b", font=("Arial", 10, "bold"))

        scroll_logs = ttk.Scrollbar(self.tab_logs, orient="vertical", command=self.tree_logs.yview)
        self.tree_logs.configure(yscroll=scroll_logs.set)

        self.tree_logs.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        scroll_logs.pack(side="right", fill="y", padx=(0, 10), pady=10)

        self.atualizar_tabela_logs()

    def atualizar_tabela_logs(self):
        for i in self.tree_logs.get_children():
            self.tree_logs.delete(i)
            
        termo = self.ent_busca_log.get().strip().lower()
        tipo_filtro = self.combo_tipo_log.get()

        for reg in self.logs:
            data_hora = reg.get("data_hora", "")
            tipo = reg.get("tipo", "")
            caixa = reg.get("caixa", "")
            item = reg.get("item", "")
            qtd = reg.get("qtd", 1)

            # Filtro por tipo
            if tipo_filtro != "Todos" and tipo != tipo_filtro:
                continue

            # Filtro por texto
            if termo and (termo not in item.lower() and termo not in caixa.lower() and termo not in data_hora.lower()):
                continue

            self.tree_logs.insert("", "end", values=(data_hora, tipo, caixa, item, qtd), tags=(tipo,))

    # ========================== POPUP DE ITENS ==========================
    def abrir_popup_itens(self):
        if not self.caixa_selecionada:
            return messagebox.showwarning("Aviso", "Selecione uma caixa na matriz primeiro.")
            
        pop = tk.Toplevel(self.root)
        pop.title(f"Gerenciar Itens - {self.caixa_selecionada}")
        pop.geometry("750x450")
        pop.configure(bg="#222222")
        pop.transient(self.root)
        pop.grab_set()

        tk.Label(pop, text=f"Caixa: {self.caixa_selecionada}", bg="#222222", fg="#00bcd4", font=("Arial", 14, "bold")).pack(pady=10)

        frame_top = tk.Frame(pop, bg="#222222")
        frame_top.pack(fill="x", padx=10)
        
        tk.Label(frame_top, text="Item:", bg="#222222", fg="white").pack(side="left")
        ent_i = tk.Entry(frame_top, width=20); ent_i.pack(side="left", padx=5)
        
        tk.Label(frame_top, text="Qtd:", bg="#222222", fg="white").pack(side="left")
        spin_q = ttk.Spinbox(frame_top, from_=1, to=8, width=4); spin_q.set(1); spin_q.pack(side="left", padx=5)
        
        tk.Label(frame_top, text="Val (DD/MM/AAAA):", bg="#222222", fg="white").pack(side="left")
        ent_v = tk.Entry(frame_top, width=12); ent_v.pack(side="left", padx=5)
        
        # Container rolável do popup
        container_pop = tk.Frame(pop, bg="#222222")
        container_pop.pack(fill="both", expand=True, padx=10, pady=10)
        
        canvas_pop = tk.Canvas(container_pop, bg="#333333", highlightthickness=0)
        scroll_pop = tk.Scrollbar(container_pop, orient="vertical", command=canvas_pop.yview)
        frame_list_pop = tk.Frame(canvas_pop, bg="#333333")
        
        frame_list_pop.bind("<Configure>", lambda e: canvas_pop.configure(scrollregion=canvas_pop.bbox("all")))
        canvas_pop.create_window((0, 0), window=frame_list_pop, anchor="nw", width=canvas_pop.winfo_width())
        canvas_pop.bind("<Configure>", lambda e: canvas_pop.itemconfig(canvas_pop.find_withtag("all")[0], width=e.width))
        
        canvas_pop.pack(side="left", fill="both", expand=True)
        scroll_pop.pack(side="right", fill="y")
        canvas_pop.configure(yscrollcommand=scroll_pop.set)

        def att_lista():
            for widget in frame_list_pop.winfo_children(): widget.destroy()
            for idx, item in enumerate(self.caixas[self.caixa_selecionada]["itens"]):
                row = tk.Frame(frame_list_pop, bg="#444444", pady=8, padx=10)
                row.pack(fill="x", pady=3, padx=5)
                
                val = item.get("validade", "N/A")
                sit, tag = self.checar_status_validade(val)
                cor_val = "#ff6b6b" if tag == "vencido" else "#ffd166" if tag == "alerta" else "#aaaaaa"
                
                tk.Label(row, text=item["nome"], bg="#444444", fg="white", font=("Arial", 11, "bold"), width=25, anchor="w").pack(side="left")
                tk.Label(row, text=f"Val: {val} ({sit})", bg="#444444", fg=cor_val, width=25, anchor="w", font=("Arial", 9, "bold")).pack(side="left")
                
                tk.Button(row, text=" - ", bg="#f44336", fg="white", font=("Arial", 11, "bold"), relief="flat", command=lambda i=idx: self.alterar_qtd(self.caixa_selecionada, i, -1, att_lista_e_principal)).pack(side="left", padx=5)
                tk.Label(row, text=f"{item['qtd']}", bg="#444444", fg="white", font=("Arial", 12, "bold"), width=3).pack(side="left")
                tk.Button(row, text=" + ", bg="#4caf50", fg="black", font=("Arial", 11, "bold"), relief="flat", command=lambda i=idx: self.alterar_qtd(self.caixa_selecionada, i, 1, att_lista_e_principal)).pack(side="left", padx=5)

        def att_lista_e_principal():
            att_lista()
            self.atualizar_lista_itens()

        def add():
            nome, qtd, val = ent_i.get().strip(), spin_q.get(), ent_v.get().strip() or "N/A"
            if not nome: return
            try: qtd = int(qtd)
            except: return
            if val != "N/A":
                try: datetime.strptime(val, "%d/%m/%Y")
                except ValueError: return messagebox.showerror("Erro", "Data inválida (DD/MM/AAAA).")
            qtd_atual = sum(int(i['qtd']) for i in self.caixas[self.caixa_selecionada]["itens"])
            if qtd_atual + qtd > 8: return messagebox.showerror("Erro", "Máx 8 itens excedido.")
            
            self.caixas[self.caixa_selecionada]["itens"].append({"nome": nome, "qtd": qtd, "validade": val})
            self.registrar_log("Entrada", self.caixa_selecionada, nome, qtd)
            self.salvar_dados()
            self.atualizar_ui_matriz()
            self.atualizar_pesquisa_estoque()
            att_lista_e_principal()
            ent_i.delete(0, tk.END); ent_v.delete(0, tk.END)

        tk.Button(frame_top, text="Adicionar Item", bg="#2196f3", fg="white", command=add).pack(side="left", padx=10)
        
        att_lista()

    # ========================== ABA 5: CONFIG & SERIAL ==========================
    def setup_tab_config(self):
        f_conn = tk.Frame(self.tab_config, bg="#222222")
        f_conn.pack(fill="x", pady=10)
        tk.Label(f_conn, text="Porta Serial:", bg="#222222", fg="white").pack(side="left")
        self.combo_ports = ttk.Combobox(f_conn, width=15)
        self.combo_ports.pack(side="left", padx=5)
        tk.Button(f_conn, text="🔄", command=self.atualizar_portas).pack(side="left")
        self.btn_connect = tk.Button(f_conn, text="Conectar", bg="#4caf50", fg="white", command=self.toggle_conexao)
        self.btn_connect.pack(side="left", padx=10)
        self.atualizar_portas()

        f_coords = tk.Frame(self.tab_config, bg="#222222")
        f_coords.pack(fill="x", pady=10)
        
        tk.Label(f_coords, text="Base X:", bg="#222222", fg="white").grid(row=0, column=0)
        self.ent_bx = tk.Entry(f_coords, width=6); self.ent_bx.grid(row=0, column=1); self.ent_bx.insert(0, str(self.base_coords['x']))
        tk.Label(f_coords, text="Base Y:", bg="#222222", fg="white").grid(row=0, column=2)
        self.ent_by = tk.Entry(f_coords, width=6); self.ent_by.grid(row=0, column=3); self.ent_by.insert(0, str(self.base_coords['y']))
        tk.Label(f_coords, text="Base Z:", bg="#222222", fg="white").grid(row=0, column=4)
        self.ent_bz = tk.Entry(f_coords, width=6); self.ent_bz.grid(row=0, column=5); self.ent_bz.insert(0, str(self.base_coords['z']))
        
        tk.Label(f_coords, text=" | Posição:", bg="#222222", fg="white").grid(row=0, column=6)
        self.combo_conf_cx = ttk.Combobox(f_coords, values=list(self.caixas.keys()), width=5, state="readonly")
        self.combo_conf_cx.grid(row=0, column=7)
        tk.Label(f_coords, text="Caixa X:", bg="#222222", fg="white").grid(row=0, column=8)
        self.ent_cx = tk.Entry(f_coords, width=6); self.ent_cx.grid(row=0, column=9)
        tk.Label(f_coords, text="Caixa Z:", bg="#222222", fg="white").grid(row=0, column=10)
        self.ent_cz = tk.Entry(f_coords, width=6); self.ent_cz.grid(row=0, column=11)
        
        tk.Button(f_coords, text="Salvar Coordenadas", bg="#2196f3", fg="white", command=self.salvar_coords).grid(row=0, column=12, padx=10)
        self.combo_conf_cx.bind("<<ComboboxSelected>>", self.carregar_coords_caixa)

        f_seq = tk.Frame(self.tab_config, bg="#222222")
        f_seq.pack(fill="both", expand=True, pady=10)
        tk.Label(f_seq, text="Seq. Retirada:", bg="#222222", fg="white").grid(row=0, column=0, sticky="w")
        self.txt_seq_retirada = tk.Text(f_seq, height=8, width=40)
        self.txt_seq_retirada.grid(row=1, column=0, padx=5); self.txt_seq_retirada.insert("1.0", self.seq_retirada)
        
        tk.Label(f_seq, text="Seq. Devolução:", bg="#222222", fg="white").grid(row=0, column=1, sticky="w")
        self.txt_seq_devolucao = tk.Text(f_seq, height=8, width=40)
        self.txt_seq_devolucao.grid(row=1, column=1, padx=5); self.txt_seq_devolucao.insert("1.0", self.seq_devolucao)
        
        tk.Button(f_seq, text="Salvar Sequências e Dados", bg="#2196f3", fg="white", command=self.salvar_dados).grid(row=2, column=0, columnspan=2, pady=10)

        self.txt_log = tk.Text(self.tab_config, height=10, bg="#111111", fg="#00ff00")
        self.txt_log.pack(fill="both", expand=True, pady=5)

    def carregar_coords_caixa(self, event=None):
        pos = self.combo_conf_cx.get()
        self.ent_cx.delete(0, tk.END); self.ent_cx.insert(0, str(self.caixas[pos]['x']))
        self.ent_cz.delete(0, tk.END); self.ent_cz.insert(0, str(self.caixas[pos]['z']))

    def salvar_coords(self):
        try:
            self.base_coords['x'] = float(self.ent_bx.get())
            self.base_coords['y'] = float(self.ent_by.get())
            self.base_coords['z'] = float(self.ent_bz.get())
            pos = self.combo_conf_cx.get()
            if pos:
                self.caixas[pos]['x'] = float(self.ent_cx.get())
                self.caixas[pos]['z'] = float(self.ent_cz.get())
            self.salvar_dados()
            self.log("Coordenadas atualizadas.")
        except: messagebox.showerror("Erro", "Valores numéricos inválidos.")

    # ========================== COMUNICAÇÃO SERIAL E AUTOMAÇÃO ==========================
    def atualizar_portas(self):
        self.combo_ports["values"] = [p.device for p in serial.tools.list_ports.comports()]
        if self.combo_ports["values"]: self.combo_ports.current(0)

    def toggle_conexao(self):
        if self.ser and self.ser.is_open:
            self.ler_serial_flag = False
            self.ser.close()
            self.ser = None
            self.btn_connect.config(text="Conectar", bg="#4caf50")
            self.log("Desconectado.")
        else:
            p = self.combo_ports.get()
            if not p: return
            try:
                self.ser = serial.Serial(p, 115200, timeout=1)
                self.btn_connect.config(text="Desconectar", bg="#f44336")
                self.ler_serial_flag = True
                self.serial_thread = threading.Thread(target=self.thread_leitura_serial, daemon=True)
                self.serial_thread.start()
                self.log(f"Conectado em {p}")
            except Exception as e: messagebox.showerror("Erro", str(e))

    def log(self, txt):
        self.txt_log.insert("end", txt + "\n")
        self.txt_log.see("end")

    def thread_leitura_serial(self):
        while self.ler_serial_flag and self.ser and self.ser.is_open:
            try:
                if self.ser.in_waiting > 0:
                    linha = self.ser.readline().decode('utf-8').strip()
                    if linha:
                        self.root.after(0, self.log, f"RX: {linha}")
                        if linha == "OK":
                            self.serial_wait_event.set()
                        elif "ALARM" in linha or "ERRO" in linha:
                            self.automacao_rodando = False
                            self.serial_wait_event.set()
            except: pass
            time.sleep(0.01)

    def enviar_gcode_sync(self, cmd):
        if not self.ser or not self.ser.is_open: return False
        self.root.after(0, self.log, f"TX: {cmd}")
        self.serial_wait_event.clear()
        self.ser.write(f"{cmd}\n".encode("utf-8"))
        sucesso = self.serial_wait_event.wait(60.0)
        return sucesso and self.automacao_rodando

    def enviar_homing(self):
        if not self.ser or not self.ser.is_open: return messagebox.showwarning("Aviso", "Conecte a máquina.")
        threading.Thread(target=self._executar_homing, daemon=True).start()

    def _executar_homing(self):
        self.automacao_rodando = True
        self.root.after(0, lambda: self.lbl_status.config(text="Status: HOMING...", fg="#ff9800"))
        self.enviar_gcode_sync("G28")
        self.automacao_rodando = False
        self.root.after(0, lambda: self.lbl_status.config(text="Status: PRONTO", fg="#4caf50"))

    def parse_vars(self, linha, cx_nome):
        c = self.caixas[cx_nome]
        v_dict = {'X': c['x'], 'Z': c['z'], 'BX': self.base_coords['x'], 'BY': self.base_coords['y'], 'BZ': self.base_coords['z']}
        def rep(m):
            var, op = m.group(1).upper(), m.group(2)
            if var in v_dict:
                val = v_dict[var]
                if op:
                    try: val += float(op)
                    except: pass
                return str(round(val, 2))
            return m.group(0)
        return re.sub(r'\{([a-zA-Z]+)([\+\-][0-9\.]+)?\}', rep, linha)

    def iniciar_movimento(self, tipo):
        if not self.caixa_selecionada: return messagebox.showwarning("Aviso", "Selecione uma caixa.")
        if not self.ser or not self.ser.is_open: return messagebox.showwarning("Aviso", "Conecte a máquina.")
        
        pos = self.caixa_selecionada
        status_atual = self.caixas[pos]["status"]

        if tipo == "retirada" and status_atual == "retirada":
            return messagebox.showwarning("Aviso", "Esta caixa JÁ FOI RETIRADA!")
        if tipo == "devolucao" and status_atual == "guardada":
            return messagebox.showwarning("Aviso", "Esta caixa JÁ ESTÁ GUARDADA!")

        if tipo == "retirada":
            para_fora = [k for k, v in self.caixas.items() if v["status"] == "retirada"]
            if len(para_fora) > 0:
                return messagebox.showwarning("Aviso", f"A caixa {para_fora[0]} já está na base. Devolva-a primeiro.")

        script = self.txt_seq_retirada.get("1.0", tk.END) if tipo == "retirada" else self.txt_seq_devolucao.get("1.0", tk.END)
        threading.Thread(target=self.executar_sequencia, args=(script, pos, tipo), daemon=True).start()

    def executar_sequencia(self, script, pos, tipo):
        self.automacao_rodando = True
        self.root.after(0, lambda: self.lbl_status.config(text=f"Status: MOVENDO {pos}...", fg="#ff9800"))
        
        linhas = script.split('\n')
        for linha in linhas:
            linha = linha.strip()
            if not linha or linha.startswith(';'): continue
            if not self.automacao_rodando: break

            cmd = self.parse_vars(linha, pos)
            if not self.enviar_gcode_sync(cmd):
                self.root.after(0, lambda: messagebox.showerror("Erro", "Sequência abortada ou Erro na máquina."))
                break
                
        if self.automacao_rodando:
            novo_status = "retirada" if tipo == "retirada" else "guardada"
            self.caixas[pos]["status"] = novo_status
            self.root.after(0, self.atualizar_ui_matriz)
            self.root.after(0, self.atualizar_pesquisa_estoque)
            self.root.after(0, self.salvar_dados)
            self.root.after(0, lambda: self.lbl_status.config(text="Status: PRONTO", fg="#4caf50"))
            
            if tipo == "retirada":
                itens_vencidos = [
                    i["nome"] for i in self.caixas[pos]["itens"] 
                    if self.checar_status_validade(i.get("validade", ""))[0] == "VENCIDO"
                ]
                if itens_vencidos:
                    self.root.after(200, lambda: messagebox.showwarning(
                        "ALERTA DE SEGURANÇA", 
                        f"⚠️ ATENÇÃO: A caixa {pos} possui o(s) seguinte(s) item(ns) VENCIDO(S):\n- " + "\n- ".join(itens_vencidos)
                    ))
                self.root.after(500, self.abrir_popup_itens)
        else:
            self.root.after(0, lambda: self.lbl_status.config(text="Status: ERRO/ABORTADO", fg="#f44336"))
        
        self.automacao_rodando = False

if __name__ == "__main__":
    root = tk.Tk()
    app = AlmoxarifadoCNCApp(root)
    root.mainloop()