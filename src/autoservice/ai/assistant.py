"""Режим A: базовый офлайн-помощник по неисправностям (без языковой модели).

Работает на проверяемых данных: правила диагностики (с источником), библиотека
автодела, история выполненных ремонтов, каталог с указанной применимостью.
Каждый вывод имеет уровень достоверности:
* «Подтверждено» — факт из базы (выполненный ремонт, подтверждённая применимость);
* «Требует проверки» — гипотеза из правил/документов;
* «Недостаточно данных» — сведений для вывода нет.
Ничего не заказывается и не списывается автоматически.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from sqlalchemy import select

from autoservice.application import catalog, inventory
from autoservice.application.context import AppContext, audit
from autoservice.domain.enums import Confidence, Verification
from autoservice.domain.normalize import normalize_text
from autoservice.knowledge import library
from autoservice.persistence.models import AIRecommendation, DiagnosticRule, PartCategory, Vehicle
from autoservice.security.permissions import Perm

DTC_RE = re.compile(r"\b([PBCU][0-3][0-9A-F]{3})\b", re.IGNORECASE)
CRITICAL_WORDS = ("тормоз", "руль", "рулев", "подушк", "airbag", "высоковольт", "гибрид", "электромобил", "запах бензина",
                  "течь топлива", "утечка топлива", "дым", "горит", "возгоран")
CRITICAL_TEXT = ("Неисправность может затрагивать системы, от которых зависит безопасность (тормоза, рулевое управление, "
                 "подушки безопасности, высоковольтные и топливные системы). Требуется профессиональная проверка; "
                 "эксплуатация автомобиля до устранения может быть опасна.")
DISCLAIMER = ("Рекомендации помощника — справочные. Причины ниже — гипотезы для проверки, а не заключение производителя. "
              "Замена деталей — только по результатам диагностики.")


@dataclass
class DiagnosisInput:
    make: str = ""
    model: str = ""
    year: int | None = None
    engine: str = ""
    mileage: int | None = None
    complaint: str = ""
    codes: str = ""
    diagnostics: str = ""
    vehicle_id: int | None = None


def _missing(inp: DiagnosisInput, codes: list[str]) -> list[str]:
    need = []
    if not inp.make:
        need.append("Марка автомобиля")
    if not inp.model:
        need.append("Модель автомобиля")
    if not inp.year:
        need.append("Год выпуска")
    if not inp.engine:
        need.append("Двигатель (код или объём и тип топлива)")
    if not inp.mileage:
        need.append("Пробег")
    if not codes:
        need.append("Коды ошибок (считать сканером, если горит индикатор неисправности)")
    if not inp.diagnostics.strip():
        need.append("Результаты выполненной диагностики (замеры, осмотр)")
    if len(inp.complaint.strip()) < 10:
        need.append("Подробное описание жалобы: когда проявляется, на холодную/горячую, на ходу/на месте")
    return need


def _rule_applies(rule: DiagnosticRule, inp: DiagnosisInput) -> bool:
    if rule.make and normalize_text(rule.make) != normalize_text(inp.make):
        return False
    if rule.model and normalize_text(rule.model) != normalize_text(inp.model):
        return False
    return True


def analyze(ctx: AppContext, inp: DiagnosisInput, log_result: bool = True) -> dict:
    ctx.require(Perm.ASSISTANT_USE)
    if inp.vehicle_id:
        with ctx.db.read() as s:
            v = s.get(Vehicle, inp.vehicle_id)
            if v:
                inp.make = inp.make or v.make
                inp.model = inp.model or v.model
                inp.year = inp.year or v.year
                inp.engine = inp.engine or " ".join(x for x in (v.engine_code, str(v.engine_volume or ""), v.fuel) if x)
                inp.mileage = inp.mileage or v.mileage
    codes = sorted({c.upper() for c in DTC_RE.findall(f"{inp.codes} {inp.complaint} {inp.diagnostics}")})
    text_all = normalize_text(f"{inp.complaint} {inp.diagnostics}")
    result: dict = {"input": asdict(inp), "codes": codes, "missing_info": _missing(inp, codes), "hypotheses": [],
                    "history": [], "library": [], "parts": [], "warnings": [], "disclaimer": DISCLAIMER}
    with ctx.db.read() as s:
        from autoservice.application.settings_service import get_setting

        use_builtin = get_setting(s, "assistant.use_builtin_rules")
        rules = s.execute(select(DiagnosticRule).where(DiagnosticRule.is_active.is_(True))).scalars().all()
        categories = {c.code: c for c in s.execute(select(PartCategory)).scalars()}
        matched: list[DiagnosticRule] = []
        for r in rules:
            if r.is_builtin and not use_builtin:
                continue
            if not _rule_applies(r, inp):
                continue
            hit = False
            if r.dtc_code and r.dtc_code.upper() in codes:
                hit = True
            elif r.keywords:
                for kw in [normalize_text(k) for k in r.keywords.split(",") if k.strip()]:
                    if kw and kw in text_all:
                        hit = True
                        break
            if hit:
                matched.append(r)
        for r in matched:
            result["hypotheses"].append({
                "rule_id": r.id, "title": r.title, "dtc_code": r.dtc_code, "causes": r.possible_causes or [],
                "checks": r.checks or [], "required_info": r.required_info or [],
                "categories": [categories[c].name for c in (r.part_category_codes or []) if c in categories],
                "category_codes": r.part_category_codes or [], "source": r.source, "builtin": r.is_builtin,
                # Причины из правил — всегда гипотезы для конкретного автомобиля.
                "level": Confidence.NEEDS_CHECK, "level_title": Confidence.TITLES[Confidence.NEEDS_CHECK],
                "source_status": Verification.TITLES.get(r.verification_status, ""), "critical": r.is_critical,
            })
            for info in r.required_info or []:
                if info not in result["missing_info"]:
                    result["missing_info"].append(info)

        query = " ".join(codes + [inp.complaint, inp.diagnostics]).strip()
        if query:
            history = library.repair_history_s(s, query, inp.make, inp.model, limit=10) if inp.make else []
            for h in history:
                h["level"] = Confidence.CONFIRMED
                h["level_title"] = "Подтверждено (выполненный ремонт)"
            result["history"] = history

        crit_codes = {c for h in result["hypotheses"] for c in h["category_codes"] if c in categories and categories[c].is_critical}
        if any(h["critical"] for h in result["hypotheses"]) or crit_codes & {"brakes", "steering"} or any(w in text_all for w in CRITICAL_WORDS):
            result["warnings"].append(CRITICAL_TEXT)

        wanted = sorted({c for h in result["hypotheses"] for c in h["category_codes"]})
        if inp.make and wanted:
            summary = inventory.stock_summary(s)
            for part, compat in catalog.compatible_parts(s, inp.make, inp.model, inp.year, "", wanted):
                st = summary.get(part.id, {"available": 0})
                result["parts"].append({
                    "part_id": part.id, "code": part.code, "name": part.name, "article": part.oem_number, "brand": part.brand,
                    "category": part.category.name if part.category else "", "available": st["available"], "unit": part.unit,
                    "compat_source": compat.source, "level": Confidence.CONFIRMED if compat.verification_status == Verification.CONFIRMED else Confidence.NEEDS_CHECK,
                    "level_title": "Применимость подтверждена" if compat.verification_status == Verification.CONFIRMED else "Применимость требует проверки",
                })
            seen = set()
            result["parts"] = [p for p in result["parts"] if not (p["part_id"] in seen or seen.add(p["part_id"]))]
            covered = {p["category"] for p in result["parts"]}
            for code in wanted:
                if code in categories and categories[code].name not in covered:
                    result["warnings"].append(
                        f"В каталоге нет запчастей категории «{categories[code].name}» с указанной применимостью к "
                        f"{inp.make} {inp.model}. Подбор — по каталогу производителя или по VIN.")
    if query:
        result["library"] = library.search(ctx, query, inp.make, inp.model, limit=8)
    if result["history"] or any(p["level"] == Confidence.CONFIRMED for p in result["parts"]):
        overall = Confidence.CONFIRMED if not result["hypotheses"] else Confidence.NEEDS_CHECK
    elif result["hypotheses"] or result["library"]:
        overall = Confidence.NEEDS_CHECK
    else:
        overall = Confidence.INSUFFICIENT
        result["warnings"].append("В локальной базе нет правил, документов и истории ремонтов, подходящих к описанию. "
                                  "Уточните жалобу, коды ошибок и результаты диагностики или добавьте материалы в библиотеку.")
    result["overall"] = overall
    result["overall_title"] = Confidence.TITLES[overall]
    if log_result:
        with ctx.db.transaction() as s:
            s.add(AIRecommendation(user_id=ctx.user_id, kind="diagnosis", mode="basic", vehicle_id=inp.vehicle_id,
                                   input=asdict(inp), output={"overall": overall, "codes": codes,
                                                              "hypotheses": [h["title"] for h in result["hypotheses"]],
                                                              "parts": [p["part_id"] for p in result["parts"]]}))
            audit(s, ctx, "assistant.analyze", "ai", None, codes=codes, overall=overall)
    return result
