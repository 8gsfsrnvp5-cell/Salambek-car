"""ИИ-помощник: базовый офлайн-режим (A), локальная модель (B), рекомендации по закупке."""

from __future__ import annotations

import html

from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from autoservice.ai import llm, recommendations
from autoservice.ai.assistant import DiagnosisInput, analyze
from autoservice.application import vehicles
from autoservice.domain.enums import Confidence
from autoservice.ui.common import FAILED, DataTable, button, confirm, fmt, info, label, page_header, run
from autoservice.ui.pages import Page

BADGE = {Confidence.CONFIRMED: "#15803d", Confidence.NEEDS_CHECK: "#b45309", Confidence.INSUFFICIENT: "#6b7280"}


def _badge(level: str, text: str) -> str:
    return f'<span style="background:{BADGE.get(level, "#6b7280")};color:white;padding:1px 6px;border-radius:4px;">{html.escape(text)}</span>'


class AssistantPage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win)
        self.layout_.addWidget(page_header("ИИ-помощник", "Режим A работает без языковой модели: правила с источниками, библиотека, "
                                                          "история ремонтов, каталог. Ничего не заказывается и не списывается автоматически."))
        self.tabs = QTabWidget()
        w = QWidget()
        split = QSplitter()
        left = QWidget()
        form = QFormLayout(left)
        self.vehicle_id = None
        self.vehicle_lbl = label("Автомобиль не выбран", "Muted")
        self.make, self.model, self.year, self.engine, self.mileage = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.complaint, self.codes, self.diag = QPlainTextEdit(), QLineEdit(), QPlainTextEdit()
        self.complaint.setFixedHeight(90)
        self.diag.setFixedHeight(70)
        self.codes.setPlaceholderText("P0171, P0300…")
        vrow = QHBoxLayout()
        vrow.addWidget(self.vehicle_lbl, 1)
        vrow.addWidget(button("Выбрать автомобиль", self.pick_vehicle))
        vw = QWidget()
        vw.setLayout(vrow)
        for text, x in (("Автомобиль из базы", vw), ("Марка", self.make), ("Модель", self.model), ("Год", self.year), ("Двигатель", self.engine),
                        ("Пробег, км", self.mileage), ("Описание неисправности", self.complaint), ("Коды ошибок", self.codes),
                        ("Результаты диагностики", self.diag)):
            form.addRow(text, x)
        brow = QHBoxLayout()
        brow.addWidget(button("Проанализировать (режим A)", self.run_basic, primary=True))
        self.b_llm = button("Спросить локальную модель (режим B)", self.run_llm)
        brow.addWidget(self.b_llm)
        bw = QWidget()
        bw.setLayout(brow)
        form.addRow("", bw)
        self.llm_status = label("", "Muted")
        form.addRow("", self.llm_status)
        split.addWidget(left)
        right = QWidget()
        rv = QVBoxLayout(right)
        self.out = QTextBrowser()
        self.out.setOpenExternalLinks(False)
        rv.addWidget(self.out, 3)
        rv.addWidget(label("Запчасти из локального каталога с указанной применимостью:", "Muted"))
        self.parts = DataTable([("name", "Запчасть", "text"), ("article", "Артикул", "text"), ("brand", "Бренд", "text"),
                                ("category", "Категория", "text"), ("available", "Доступно", "qty"), ("level_title", "Достоверность", "text"),
                                ("compat_source", "Источник применимости", "text")], empty_text="Подходящих запчастей с указанной применимостью нет",
                               multi=True)
        rv.addWidget(self.parts, 1)
        rv.addWidget(button("Подготовить черновик закупки выбранных", self.draft_purchase))
        split.addWidget(right)
        split.setStretchFactor(1, 2)
        lay = QVBoxLayout(w)
        lay.addWidget(split)
        self.tabs.addTab(w, "Консультация по неисправности")
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        self.recs = DataTable([("part_name", "Запчасть", "text"), ("article", "Артикул", "text"), ("available", "Доступно", "qty"),
                               ("min_stock", "Минимум", "qty"), ("consumption", "Расход 90 дн.", "qty"), ("wo_shortage", "Не хватает под ремонты", "qty"),
                               ("recommended_qty", "Предлагается купить", "qty"), ("supplier", "Поставщик", "text"),
                               ("last_price", "Последняя цена", "money"), ("last_price_date", "Дата цены", "date"),
                               ("reliability", "Достоверность", "text"), ("basis", "Основание расчёта", "text")],
                              empty_text="Рекомендаций нет: остатки выше минимума, потребностей под ремонты нет.", multi=True)
        l2.addWidget(self.recs, 1)
        row = QHBoxLayout()
        row.addWidget(button("Обновить", self.load_recs))
        row.addWidget(button("Создать черновики закупок для выбранных", self.create_drafts, primary=True))
        row.addStretch()
        l2.addLayout(row)
        l2.addWidget(label("Если истории расхода мало (менее 30 дней или 3 документов) или не указан срок поставки, расчёт "
                           "помечается «Недостаточно данных для прогноза» и опирается только на минимальный остаток и резервы.", "Muted"))
        self.tabs.addTab(w2, "Рекомендации по закупке")
        self.layout_.addWidget(self.tabs, 1)
        self.last_input = None
        self.last_basic = None

    def refresh(self):
        model = run(self, llm.LocalLLM.from_settings, self.ctx)
        if model is FAILED or model is None:
            self.b_llm.setEnabled(False)
            self.llm_status.setText("Режим B: локальная модель выключена в настройках. Работает базовый режим.")
        else:
            st = model.status()
            self.b_llm.setEnabled(st.available)
            self.llm_status.setText(f"Режим B: {st.text}")
        if self.tabs.currentIndex() == 1:
            self.load_recs()

    def pick_vehicle(self):
        from autoservice.ui.pages.repairs import VehiclePicker

        vid = VehiclePicker.pick(self.ctx, self)
        if vid:
            self.set_vehicle(vid)

    def set_vehicle(self, vid):
        v = vehicles.get_vehicle(self.ctx, vid)
        self.vehicle_id = vid
        self.vehicle_lbl.setText(f"{v['title']} {v['plate']}")
        self.make.setText(v["make"])
        self.model.setText(v["model"])
        self.year.setText(str(v["year"] or ""))
        self.engine.setText(" ".join(x for x in (v["engine_code"], fmt(v["engine_volume"], "qty") if v["engine_volume"] else "", v["fuel"]) if x))
        self.mileage.setText(str(v["mileage"] or ""))

    def _input(self) -> DiagnosisInput:
        def as_int(t):
            t = t.strip().replace(" ", "")
            return int(t) if t.isdigit() else None

        return DiagnosisInput(make=self.make.text().strip(), model=self.model.text().strip(), year=as_int(self.year.text()),
                              engine=self.engine.text().strip(), mileage=as_int(self.mileage.text()), complaint=self.complaint.toPlainText(),
                              codes=self.codes.text(), diagnostics=self.diag.toPlainText(), vehicle_id=self.vehicle_id)

    def run_basic(self):
        inp = self._input()
        res = run(self, analyze, self.ctx, inp)
        if res is FAILED:
            return
        self.last_input, self.last_basic = inp, res
        self.out.setHtml(self.render(res))
        self.parts.set_rows(res["parts"])

    def render(self, r: dict) -> str:
        e = html.escape
        parts = [f"<h3>Итог: {_badge(r['overall'], r['overall_title'])}</h3>"]
        for w in r["warnings"]:
            parts.append(f'<p style="color:#b91c1c;"><b>⚠ {e(w)}</b></p>')
        for n in r.get("notes", []):
            parts.append(f'<p style="color:#374151;">ℹ {e(n)}</p>')
        if r["codes"]:
            parts.append(f"<p><b>Коды ошибок:</b> {e(', '.join(r['codes']))}</p>")
        if r["hypotheses"]:
            parts.append("<h4>Возможные причины и проверки (гипотезы)</h4>")
            for i, h in enumerate(r["hypotheses"], 1):
                parts.append(f"<p><b>{i}. {e(h['title'])}</b> {_badge(h['level'], h['level_title'])}<br>"
                             f"<i>Источник: {e(h['source'])}</i></p><ul>")
                parts += [f"<li>{e(c)}</li>" for c in h["causes"]]
                parts.append("</ul><p>Порядок проверки:</p><ol>")
                parts += [f"<li>{e(c)}</li>" for c in h["checks"]]
                parts.append("</ol>")
                if h["categories"]:
                    parts.append(f"<p>Возможные категории запчастей: {e(', '.join(h['categories']))}</p>")
        if r["history"]:
            parts.append("<h4>Подтверждённые ремонты в этом автосервисе</h4><ul>")
            for h in r["history"][:8]:
                parts.append(f"<li>{_badge(h['level'], 'Подтверждено')} № {e(h['number'])} {e(h['vehicle'])}: «{e(h['complaint'])}» → "
                             f"{e(h['diagnosis'] or '—')}; работы: {e('; '.join(h['services']) or '—')}</li>")
            parts.append("</ul>")
        if r["library"]:
            parts.append("<h4>Материалы библиотеки</h4><ul>")
            for d in r["library"]:
                parts.append(f"<li><b>{e(d['title'])}</b> ({e(d['source'])}, {e(d['status_title'])}): {e(d['snippet'])}</li>")
            parts.append("</ul>")
        if r["missing_info"]:
            parts.append("<h4>Какие сведения ещё нужны</h4><ul>" + "".join(f"<li>{e(x)}</li>" for x in r["missing_info"]) + "</ul>")
        parts.append(f"<p style='color:#6b7280'>{e(r['disclaimer'])}</p>")
        return "".join(parts)

    def run_llm(self):
        if self.last_basic is None:
            self.run_basic()
        if self.last_basic is None:
            return
        from dataclasses import asdict

        res = run(self, llm.ask_llm, self.ctx, asdict(self.last_input), self.last_basic)
        if res is FAILED:
            return
        e = html.escape
        block = (f"<hr><h3>Ответ локальной модели «{e(res['model'])}» {_badge(Confidence.NEEDS_CHECK, 'Требует проверки')}</h3>"
                 f"<p style='white-space:pre-wrap'>{e(res['text'])}</p><p><b>Источники контекста:</b></p><ol>"
                 + "".join(f"<li>{e(s)}</li>" for s in res["sources"]) + "</ol>")
        self.out.setHtml(self.render(self.last_basic) + block)

    def draft_purchase(self):
        rows = self.parts.selected_rows()
        if not rows:
            return info(self, "Выберите запчасти в таблице")
        recs = {r["part_id"]: r for r in recommendations.purchase_recommendations(self.ctx)}
        items = [recs.get(r["part_id"]) or {"part_id": r["part_id"], "recommended_qty": "1", "supplier_id": None, "last_price": None}
                 for r in rows]
        if confirm(self, f"Подготовить черновик закупки ({len(items)} поз.)? Закупка не будет проведена автоматически."):
            ids = run(self, recommendations.create_purchase_drafts, self.ctx, items)
            if ids is not FAILED:
                info(self, "Созданы черновики закупок. Проверьте количество, цены и поставщика в разделе «Закупки».")

    def load_recs(self):
        self.recs.set_rows(recommendations.purchase_recommendations(self.ctx))

    def create_drafts(self):
        rows = self.recs.selected_rows()
        if not rows:
            return info(self, "Выберите строки (Ctrl/Shift для нескольких)")
        if confirm(self, f"Создать черновики закупок по {len(rows)} позициям? Проведение — отдельно, после проверки."):
            ids = run(self, recommendations.create_purchase_drafts, self.ctx, rows)
            if ids is not FAILED:
                info(self, f"Создано черновиков: {len(ids)}. Откройте раздел «Закупки» для проверки и проведения.")

    def handle(self, action, **kw):
        if action == "vehicle":
            self.tabs.setCurrentIndex(0)
            self.set_vehicle(kw["vehicle_id"])
        elif action == "work_order":
            wo = kw["wo"]
            self.tabs.setCurrentIndex(0)
            self.set_vehicle(wo["vehicle_id"])
            self.complaint.setPlainText(wo["complaint"])
            self.diag.setPlainText(wo["diagnosis"])
            if wo["mileage"]:
                self.mileage.setText(str(wo["mileage"]))
        elif action == "recommendations":
            self.tabs.setCurrentIndex(1)
            self.load_recs()


__all__ = ["AssistantPage", "QVBoxLayout"]
