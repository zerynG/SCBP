"""
Единый калькулятор стоимости проекта.

Источник правды для всех формул SCBP. Используется:
- `project_manage` view (HTML + AJAX)
- `recalculate_project` API
- `_build_project_data` (документы: PDF/Excel/Word)
- unit-тесты в `mainboard/tests.py`

JS-зеркало: `static/js/calculator.js` (формулы 1:1).

Экономическая модель (РФ):
  - Страховые взносы (project.tax_rate, %) — начисляются ТОЛЬКО на ФОТ
    (оклад штатных сотрудников) и входят в себестоимость. На исполнителей,
    подрядчиков и оборудование взносы НЕ начисляются.
  - Наценка/рентабельность (margin, %) — надбавка к себестоимости ресурса.
  - НДС (project.vat_rate, %) — начисляется ОДИН раз на итоговую цену
    проекта (output), не входит в прибыль. По умолчанию 0.
  Все три параметра настраиваются на проекте; по умолчанию 0.

Контракт формул (на ресурс):
    base_no_tax = f(тип, ставка, период, override, календарь)
    cost        = base_no_tax * (1 + insurance/100)   # insurance=tax_rate только для employee, иначе 0
    final_cost  = cost * (1 + margin/100)
        где margin = override.resource_margin if задано иначе project.margin

Итоги проекта:
    base_cost      = Σ cost                       # себестоимость (с взносами)
    final_price    = Σ final_cost                 # цена без НДС
    net_profit     = final_price - base_cost      # прибыль (без НДС)
    vat_amount     = final_price * vat_rate/100   # НДС
    price_with_vat = final_price + vat_amount      # цена для клиента
"""
from __future__ import annotations

import json
import os
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

WORKING_DAYS_FALLBACK = Decimal("22")
"""Fallback количества рабочих дней в месяце, когда JSON-календарь недоступен."""

QUANT = Decimal("0.01")


def _D(value: Any) -> Decimal:
    """Безопасное преобразование к Decimal (None/'' → 0)."""
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def quantize_money(value: Decimal) -> Decimal:
    """Округлить до копеек по правилам бухгалтерского округления."""
    return _D(value).quantize(QUANT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Календарь рабочих дней
# ---------------------------------------------------------------------------

# Тип словаря календарей: {year: {"month": {"day": "work"|"weekend"|"holiday"}}}
CalendarMap = Dict[int, Dict[str, Dict[str, str]]]


def load_calendars(calendar_dir: str) -> CalendarMap:
    """Прочитать все calendar_YYYY.json из директории в единый словарь.

    Возвращает пустой dict, если директория не существует или нет файлов.
    """
    result: CalendarMap = {}
    if not calendar_dir or not os.path.isdir(calendar_dir):
        return result
    for name in os.listdir(calendar_dir):
        if not (name.startswith("calendar_") and name.endswith(".json")):
            continue
        year_str = name[len("calendar_"):-len(".json")]
        if not year_str.isdigit():
            continue
        try:
            with open(os.path.join(calendar_dir, name), "r", encoding="utf-8") as f:
                data = json.load(f)
                cal = data.get("calendar")
                if isinstance(cal, dict):
                    result[int(year_str)] = cal
        except Exception:
            continue
    return result


def is_work_day(day: date, calendars: CalendarMap) -> bool:
    """Является ли день рабочим по JSON-календарю.

    Если для года календарь недоступен или статус не задан — fallback: будний день (Пн-Пт).
    """
    cal = calendars.get(day.year) if calendars else None
    if cal:
        month_map = cal.get(str(day.month), {})
        status = month_map.get(str(day.day))
        if status is None:
            return day.weekday() < 5
        return status == "work"
    return day.weekday() < 5


def count_business_days(start: Optional[date], end: Optional[date], calendars: CalendarMap) -> int:
    """Количество рабочих дней в периоде [start, end] включительно."""
    if not start or not end or end < start:
        return 0
    count = 0
    d = start
    while d <= end:
        if is_work_day(d, calendars):
            count += 1
        d += timedelta(days=1)
    return count


def working_days_in_month(ref_date: date, calendars: CalendarMap, fallback: Any = WORKING_DAYS_FALLBACK) -> Decimal:
    """Рабочих дней в месяце ref_date. Если календарь дал 0 — fallback."""
    first = date(ref_date.year, ref_date.month, 1)
    last = date(ref_date.year, ref_date.month, monthrange(ref_date.year, ref_date.month)[1])
    days = count_business_days(first, last, calendars)
    return Decimal(days) if days > 0 else _D(fallback)


def count_calendar_days(start: Optional[date], end: Optional[date]) -> int:
    """Календарные дни в периоде [start, end] включительно."""
    if not start or not end or end < start:
        return 0
    return (end - start).days + 1


def count_unavailable_business_days(
    schedule_entries: Iterable[Any],
    period_start: date,
    period_end: date,
    calendars: CalendarMap,
) -> int:
    """Сколько РАБОЧИХ дней (по JSON-календарю) сотрудник был недоступен.

    schedule_entries — итератор объектов с полями start_date, end_date.
    Обычно передаются записи со статусом dayoff / other_project.
    """
    if not period_start or not period_end or period_end < period_start:
        return 0
    busy_dates: set = set()
    for entry in schedule_entries:
        s = max(entry.start_date, period_start)
        e = min(entry.end_date, period_end)
        if e < s:
            continue
        d = s
        while d <= e:
            busy_dates.add(d)
            d += timedelta(days=1)
    return sum(1 for d in busy_dates if is_work_day(d, calendars))


# ---------------------------------------------------------------------------
# Расчёт месяцев оборудования (изолированная формула)
# ---------------------------------------------------------------------------

def equipment_months(start: date, end: date) -> int:
    """Округлённое вверх количество календарных месяцев.

    Правило: разница в месяцах + 1, если end.day >= start.day.
    Минимум 1 месяц. Совпадает с текущим поведением системы.

    Изолированно — чтобы при необходимости заменить на другую формулу.
    """
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day >= start.day:
        months += 1
    return max(1, months)


# ---------------------------------------------------------------------------
# Структуры результата
# ---------------------------------------------------------------------------

@dataclass
class ResourceCost:
    """Результат расчёта одной строки ресурса."""
    resource_type: str
    resource_id: int
    name: str = ""
    type_display: str = ""
    service: str = ""
    service_id: Optional[int] = None

    margin_percent: Decimal = Decimal("0")
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    work_days: Optional[int] = None
    work_hours: Optional[int] = None

    monthly_cost: Optional[Decimal] = None
    base_no_tax: Decimal = Decimal("0")
    cost: Decimal = Decimal("0")
    final_cost: Decimal = Decimal("0")

    # Дополнительные поля для UI / шаблона
    base_monthly_cost: Optional[Decimal] = None
    base_estimated_cost: Optional[Decimal] = None
    base_estimated_hours: Optional[int] = None
    base_contract_amount: Optional[Decimal] = None
    base_total_cost: Optional[Decimal] = None
    hourly_rate: Optional[Decimal] = None
    pricing_type: Optional[str] = None
    rate: Optional[Decimal] = None
    quantity: Optional[int] = None
    months_count: Optional[int] = None
    has_monthly_cost: bool = False
    delete_name: str = ""
    delete_id: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        d = {
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "name": self.name,
            "type": self.type_display,
            "service": self.service,
            "service_id": self.service_id or "",
            "margin_percent": self.margin_percent,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "work_days": self.work_days,
            "work_hours": self.work_hours,
            "monthly_cost": self.monthly_cost,
            "base_no_tax": self.base_no_tax,
            "cost": self.cost,
            "final_cost": self.final_cost,
            "base_monthly_cost": self.base_monthly_cost,
            "base_estimated_cost": self.base_estimated_cost,
            "base_estimated_hours": self.base_estimated_hours,
            "base_contract_amount": self.base_contract_amount,
            "base_total_cost": self.base_total_cost,
            "hourly_rate": self.hourly_rate,
            "pricing_type": self.pricing_type,
            "rate": self.rate,
            "quantity": self.quantity,
            "months_count": self.months_count,
            "has_monthly_cost": self.has_monthly_cost,
            "delete_name": self.delete_name,
            "delete_id": self.delete_id,
        }
        return d


@dataclass
class ProjectTotals:
    base_cost: Decimal = Decimal("0")      # себестоимость (включая взносы на ФОТ)
    final_price: Decimal = Decimal("0")    # цена без НДС (= Σ final_cost)
    net_profit: Decimal = Decimal("0")     # прибыль (без НДС) = final_price - base_cost

    vat_rate: Decimal = Decimal("0")       # ставка НДС, %
    vat_amount: Decimal = Decimal("0")     # сумма НДС
    price_with_vat: Decimal = Decimal("0") # цена с НДС (для клиента)

    total_employee_cost: Decimal = Decimal("0")
    total_contractor_cost: Decimal = Decimal("0")
    total_subcontractor_cost: Decimal = Decimal("0")
    total_equipment_cost: Decimal = Decimal("0")


# ---------------------------------------------------------------------------
# Применение налога и маржи (единое правило)
# ---------------------------------------------------------------------------

def tax_multiplier(tax_rate_percent: Any) -> Decimal:
    return Decimal("1") + _D(tax_rate_percent) / Decimal("100")


def margin_multiplier(margin_percent: Any) -> Decimal:
    return Decimal("1") + _D(margin_percent) / Decimal("100")


def resolve_margin(override_margin: Any, project_margin: Any) -> Decimal:
    """Какой процент маржи применять.

    Семантика:
      - override_margin = None  → берём project_margin (override не задан)
      - override_margin = 0     → строго 0% (явно задано пользователем)
      - иначе                   → override_margin
    """
    if override_margin is None:
        return _D(project_margin)
    return _D(override_margin)


def apply_tax_and_margin(
    base_no_tax: Decimal,
    tax_rate_percent: Any,
    margin_percent: Any,
) -> Tuple[Decimal, Decimal]:
    """Вернуть (cost, final_cost) по единой формуле."""
    cost = _D(base_no_tax) * tax_multiplier(tax_rate_percent)
    final_cost = cost * margin_multiplier(margin_percent)
    return cost, final_cost


# ---------------------------------------------------------------------------
# Калькуляция базовой себестоимости (без налога) — по типам ресурса
# ---------------------------------------------------------------------------

def calc_employee_base(
    monthly_salary: Any,
    start: Optional[date],
    end: Optional[date],
    calendars: CalendarMap,
    unavailable_days: int = 0,
    fallback_days: Any = WORKING_DAYS_FALLBACK,
) -> Dict[str, Any]:
    """Базовая стоимость сотрудника (до налога).

    - С датами: salary × (рабочие_дни − недоступные) / рабочие_дни_в_месяце
    - Без дат: salary (полный месяц)
    """
    salary = _D(monthly_salary)
    if start and end and end >= start:
        work_days = count_business_days(start, end, calendars)
        work_days = max(0, work_days - max(0, unavailable_days))
        total_days_in_month = working_days_in_month(start, calendars, fallback=fallback_days)
        if work_days > 0 and total_days_in_month > 0:
            months = Decimal(work_days) / total_days_in_month
        else:
            months = Decimal("0")
        base = salary * months
        return {
            "base_no_tax": base,
            "work_days": work_days,
            "months": months,
            "total_days_in_month": int(total_days_in_month) if total_days_in_month else None,
        }
    return {
        "base_no_tax": salary,
        "work_days": None,
        "months": None,
        "total_days_in_month": None,
    }


def calc_contractor_base(
    hourly_rate: Any,
    working_hours: Optional[int],
    estimated_hours: Optional[int],
    estimated_cost: Any = None,
) -> Dict[str, Any]:
    """Базовая стоимость исполнителя (ГПХ/НПД).

    Правила:
    - working_hours передан явно (включая 0) → rate × working_hours
    - working_hours == None и hourly_rate задан → rate × estimated_hours
    - hourly_rate не задан → estimated_cost (legacy)
    """
    rate = _D(hourly_rate)
    if working_hours is not None:
        hours = int(working_hours)
        base = rate * Decimal(hours)
        return {"base_no_tax": base, "work_hours": hours}
    if rate > 0:
        hours = int(estimated_hours or 0)
        base = rate * Decimal(hours)
        return {"base_no_tax": base, "work_hours": hours}
    return {"base_no_tax": _D(estimated_cost), "work_hours": None}


def calc_subcontractor_base(
    pricing_type: str,
    contract_amount: Any,
    hourly_rate: Any,
    working_hours: Optional[int],
    estimated_hours: Optional[int],
) -> Dict[str, Any]:
    """Базовая стоимость подрядчика.

    pricing_type == 'hourly' → почасовая (см. правила contractor)
    иначе → фиксированная сумма contract_amount
    """
    if pricing_type == "hourly" and _D(hourly_rate) > 0:
        rate = _D(hourly_rate)
        if working_hours is not None:
            hours = int(working_hours)
        else:
            hours = int(estimated_hours or 0)
        return {"base_no_tax": rate * Decimal(hours), "work_hours": hours}
    return {"base_no_tax": _D(contract_amount), "work_hours": None}


def calc_equipment_base(
    pricing_type: str,
    rate: Any,
    quantity: int,
    start: Optional[date],
    end: Optional[date],
    fallback_total_cost: Any,
    calendars: Optional[CalendarMap] = None,
    hour_basis: str = "calendar",
    work_hours_per_day: Any = 8,
) -> Dict[str, Any]:
    """Базовая стоимость оборудования.

    pricing_type (всё — по календарным дням периода, выходные/праздники не важны):
      - month:  rate × equipment_months(start, end) × qty   (месяцев, округл. вверх)
      - day:    rate × calendar_days × qty
      - hour:   rate × (calendar_days × 24) × qty
      - minute: rate × (calendar_days × 24 × 60) × qty       (= часы × 60)

    hour_basis='working' (по рабочим дням × КЧЧ) поддерживается калькулятором,
    но из UI больше не выбирается (всегда календарный режим).

    Без дат / некорректных дат → fallback_total_cost.
    """
    qty = int(quantity or 1)
    rate_d = _D(rate)
    fallback = _D(fallback_total_cost)
    wh_per_day = _D(work_hours_per_day) if work_hours_per_day is not None else Decimal("8")
    result: Dict[str, Any] = {
        "base_no_tax": fallback,
        "months_count": None,
        "calendar_days": None,
        "calendar_hours": None,
        "calendar_minutes": None,
        "hour_basis": hour_basis,
    }

    if start and end and end >= start:
        days = count_calendar_days(start, end)
        if pricing_type == "month":
            months = equipment_months(start, end)
            result["base_no_tax"] = rate_d * Decimal(months) * qty
            result["months_count"] = months
            result["calendar_days"] = days
        elif pricing_type == "day":
            result["base_no_tax"] = rate_d * Decimal(days) * qty
            result["calendar_days"] = days
        elif pricing_type == "hour":
            if hour_basis == "working":
                bd = count_business_days(start, end, calendars or {})
                hours_d = Decimal(bd) * wh_per_day
                result["base_no_tax"] = rate_d * hours_d * qty
                result["calendar_days"] = days
                result["calendar_hours"] = int(hours_d) if hours_d == hours_d.to_integral_value() else float(hours_d)
            else:
                hours = days * 24
                result["base_no_tax"] = rate_d * Decimal(hours) * qty
                result["calendar_days"] = days
                result["calendar_hours"] = hours
        elif pricing_type == "minute":
            minutes = days * 24 * 60
            result["base_no_tax"] = rate_d * Decimal(minutes) * qty
            result["calendar_days"] = days
            result["calendar_minutes"] = minutes
            result["calendar_hours"] = days * 24  # для UI
        else:
            result["base_no_tax"] = fallback
    return result


# ---------------------------------------------------------------------------
# Высокоуровневая функция: посчитать строки + итоги проекта
# ---------------------------------------------------------------------------

def calc_project(
    *,
    project: Any,
    employees: Iterable[Any],
    contractors: Iterable[Any],
    subcontractors: Iterable[Any],
    equipment: Iterable[Any],
    overrides_map: Dict[Tuple[str, int], Any],
    schedule_by_employee: Dict[int, List[Any]],
    calendars: CalendarMap,
) -> Tuple[List[ResourceCost], ProjectTotals]:
    """Полный расчёт проекта.

    Возвращает (resources, totals).

    project: должен иметь .tax_rate, .margin
    employees/contractors/subcontractors/equipment: queryset/итераторы Django-моделей
    overrides_map: {(resource_type, resource_id): ProjectResourceOverride}
    schedule_by_employee: {employee_id: [EmployeeSchedule, ...]} — только dayoff/other_project
    """
    # tax_rate = страховые взносы на ФОТ (только сотрудники); vat_rate = НДС на итог
    insurance_rate = _D(getattr(project, "tax_rate", 0))
    vat_rate = _D(getattr(project, "vat_rate", 0))
    project_margin = _D(getattr(project, "margin", 0))
    wh_per_day = _D(getattr(project, "work_hours_per_day", 8)) or Decimal("8")
    eq_hour_basis = getattr(project, "equipment_hour_basis", "calendar") or "calendar"
    wd_fallback = _D(getattr(project, "working_days_per_month_fallback", WORKING_DAYS_FALLBACK)) or WORKING_DAYS_FALLBACK
    rows: List[ResourceCost] = []
    totals = ProjectTotals()

    # ----------------------------- Сотрудники
    for e in employees:
        o = overrides_map.get(("employee", e.id))
        margin_percent = resolve_margin(
            getattr(o, "resource_margin", None) if o else None,
            project_margin,
        )
        start = (o.work_start_date if o and o.work_start_date else e.start_date)
        end = (o.work_end_date if o and o.work_end_date else e.end_date)

        # Недоступные дни
        unavailable = 0
        if start and end:
            entries = schedule_by_employee.get(e.id, [])
            if entries:
                unavailable = count_unavailable_business_days(entries, start, end, calendars)

        emp_base = calc_employee_base(e.monthly_salary, start, end, calendars, unavailable, fallback_days=wd_fallback)
        base_no_tax = emp_base["base_no_tax"]
        # взносы на ФОТ — только для штатных сотрудников
        cost, final_cost = apply_tax_and_margin(base_no_tax, insurance_rate, margin_percent)
        totals.total_employee_cost += cost

        # Часы для отображения: рабочие_дни × КЧЧ
        work_hours_display = None
        if emp_base["work_days"] is not None:
            wh = Decimal(emp_base["work_days"]) * wh_per_day
            work_hours_display = int(wh) if wh == wh.to_integral_value() else float(wh)

        rows.append(ResourceCost(
            resource_type="employee",
            resource_id=e.id,
            name=e.name,
            type_display="Сотрудник",
            service=(o.service.name if o and o.service else ""),
            service_id=(o.service_id if o and getattr(o, "service_id", None) else None),
            margin_percent=margin_percent,
            start_date=start,
            end_date=end,
            work_days=emp_base["work_days"],
            work_hours=work_hours_display,
            monthly_cost=e.monthly_salary,
            base_no_tax=base_no_tax,
            cost=cost,
            final_cost=final_cost,
            base_monthly_cost=e.monthly_salary,
            has_monthly_cost=False,
            delete_name="delete_employee",
            delete_id=e.id,
        ))

    # ----------------------------- Исполнители (Contractor)
    for c in contractors:
        o = overrides_map.get(("contractor", c.id))
        margin_percent = resolve_margin(
            getattr(o, "resource_margin", None) if o else None,
            project_margin,
        )
        wh = o.working_hours if o and o.working_hours is not None else None
        cb = calc_contractor_base(c.hourly_rate, wh, c.estimated_hours, c.estimated_cost)
        base_no_tax = cb["base_no_tax"]
        # взносы на исполнителей не начисляются (ГПХ/НПД/ИП платят сами)
        cost, final_cost = apply_tax_and_margin(base_no_tax, Decimal("0"), margin_percent)
        totals.total_contractor_cost += cost

        start = o.work_start_date if o and o.work_start_date else None
        end = o.work_end_date if o and o.work_end_date else None
        work_days_disp = count_business_days(start, end, calendars) if start and end and end >= start else None
        work_hours_disp = wh if wh is not None else c.estimated_hours

        rows.append(ResourceCost(
            resource_type="contractor",
            resource_id=c.id,
            name=c.name,
            type_display="Исполнитель",
            service=(o.service.name if o and o.service else ""),
            service_id=(o.service_id if o and getattr(o, "service_id", None) else None),
            margin_percent=margin_percent,
            start_date=start,
            end_date=end,
            work_days=work_days_disp,
            work_hours=work_hours_disp,
            monthly_cost=c.hourly_rate,
            base_no_tax=base_no_tax,
            cost=cost,
            final_cost=final_cost,
            base_estimated_cost=base_no_tax,
            base_estimated_hours=c.estimated_hours,
            hourly_rate=c.hourly_rate,
            pricing_type="hourly",
            has_monthly_cost=False,
            delete_name="delete_contractor",
            delete_id=c.id,
        ))

    # ----------------------------- Подрядчики (Subcontractor)
    for s in subcontractors:
        o = overrides_map.get(("subcontractor", s.id))
        margin_percent = resolve_margin(
            getattr(o, "resource_margin", None) if o else None,
            project_margin,
        )
        wh = o.working_hours if o and o.working_hours is not None else None
        sb = calc_subcontractor_base(
            s.pricing_type,
            s.contract_amount,
            s.hourly_rate,
            wh,
            s.estimated_hours,
        )
        base_no_tax = sb["base_no_tax"]
        # взносы на подрядчиков не начисляются (юрлицо/договор)
        cost, final_cost = apply_tax_and_margin(base_no_tax, Decimal("0"), margin_percent)
        totals.total_subcontractor_cost += cost

        start = o.work_start_date if o and o.work_start_date else None
        end = o.work_end_date if o and o.work_end_date else None
        work_days_disp = count_business_days(start, end, calendars) if start and end and end >= start else None

        is_hourly = s.pricing_type == "hourly" and _D(s.hourly_rate) > 0
        hourly_rate = s.hourly_rate if is_hourly else None
        monthly_cost = (s.hourly_rate if is_hourly else s.contract_amount)

        rows.append(ResourceCost(
            resource_type="subcontractor",
            resource_id=s.id,
            name=s.name,
            type_display="Подрядчик",
            service=(o.service.name if o and o.service else ""),
            service_id=(o.service_id if o and getattr(o, "service_id", None) else None),
            margin_percent=margin_percent,
            start_date=start,
            end_date=end,
            work_days=work_days_disp,
            work_hours=sb["work_hours"],
            monthly_cost=monthly_cost,
            base_no_tax=base_no_tax,
            cost=cost,
            final_cost=final_cost,
            base_estimated_cost=base_no_tax,
            base_estimated_hours=s.estimated_hours if is_hourly else None,
            base_contract_amount=s.contract_amount,
            hourly_rate=hourly_rate,
            pricing_type=s.pricing_type,
            has_monthly_cost=False,
            delete_name="delete_subcontractor",
            delete_id=s.id,
        ))

    # ----------------------------- Оборудование
    for eq in equipment:
        o = overrides_map.get(("equipment", eq.id))
        margin_percent = resolve_margin(
            getattr(o, "resource_margin", None) if o else None,
            project_margin,
        )
        # Период оборудования: даты из сводной таблицы (override) → дата покупки
        # для старта и конец проекта для финиша. Это позволяет помесячной аренде
        # считаться от приобретения до конца проекта (с округлением вверх),
        # а не падать на фиксированный total_cost при пустом «Окончании».
        proj_start = getattr(project, "start_date", None)
        proj_end = getattr(project, "end_date", None)
        start = (o.work_start_date if o and o.work_start_date else None) or eq.purchase_date or proj_start
        end = (o.work_end_date if o and o.work_end_date else None) or proj_end

        # Ставка: operational_cost, затем monthly_cost, затем cost (legacy)
        rate = _D(eq.operational_cost)
        if rate == 0 and getattr(eq, "monthly_cost", None):
            rate = _D(eq.monthly_cost)
        if rate == 0 and _D(getattr(eq, "cost", 0)) > 0:
            rate = _D(eq.cost)

        pricing_type = getattr(eq, "pricing_type", "month") or "month"
        if pricing_type not in ("month", "day", "hour", "minute"):
            pricing_type = "month"

        eb = calc_equipment_base(
            pricing_type=pricing_type,
            rate=rate,
            quantity=eq.quantity,
            start=start,
            end=end,
            fallback_total_cost=eq.total_cost,
            calendars=calendars,
            hour_basis=eq_hour_basis,
            work_hours_per_day=wh_per_day,
        )
        base_no_tax = eb["base_no_tax"]
        # взносы на оборудование не начисляются
        cost, final_cost = apply_tax_and_margin(base_no_tax, Decimal("0"), margin_percent)
        totals.total_equipment_cost += cost

        rows.append(ResourceCost(
            resource_type="equipment",
            resource_id=eq.id,
            name=eq.name,
            type_display="Оборудование",
            service=(o.service.name if o and o.service else ""),
            service_id=(o.service_id if o and getattr(o, "service_id", None) else None),
            margin_percent=margin_percent,
            start_date=start,
            end_date=end,
            work_days=eb["calendar_days"],
            work_hours=eb["calendar_hours"],
            monthly_cost=rate,
            base_no_tax=base_no_tax,
            cost=cost,
            final_cost=final_cost,
            base_total_cost=eq.total_cost,
            pricing_type=pricing_type,
            rate=rate,
            quantity=eq.quantity,
            months_count=eb["months_count"],
            has_monthly_cost=True,
            delete_name="delete_equipment",
            delete_id=eq.id,
        ))

    # ----------------------------- Итоги
    totals.base_cost = (
        totals.total_employee_cost
        + totals.total_contractor_cost
        + totals.total_subcontractor_cost
        + totals.total_equipment_cost
    )
    totals.final_price = sum((r.final_cost for r in rows), Decimal("0"))
    totals.net_profit = totals.final_price - totals.base_cost
    # НДС — один раз на итоговую цену (output), не входит в прибыль
    totals.vat_rate = vat_rate
    totals.vat_amount = totals.final_price * vat_rate / Decimal("100")
    totals.price_with_vat = totals.final_price + totals.vat_amount
    return rows, totals


def serialize_totals(totals: ProjectTotals) -> Dict[str, str]:
    return {
        "base_cost": str(quantize_money(totals.base_cost)),
        "final_price": str(quantize_money(totals.final_price)),
        "net_profit": str(quantize_money(totals.net_profit)),
        "vat_rate": str(totals.vat_rate),
        "vat_amount": str(quantize_money(totals.vat_amount)),
        "price_with_vat": str(quantize_money(totals.price_with_vat)),
        "total_employee_cost": str(quantize_money(totals.total_employee_cost)),
        "total_contractor_cost": str(quantize_money(totals.total_contractor_cost)),
        "total_subcontractor_cost": str(quantize_money(totals.total_subcontractor_cost)),
        "total_equipment_cost": str(quantize_money(totals.total_equipment_cost)),
    }


def serialize_row(r: ResourceCost) -> Dict[str, Any]:
    """Сериализация строки ресурса для JSON-ответа (для JS)."""
    return {
        "resource_type": r.resource_type,
        "resource_id": r.resource_id,
        "name": r.name,
        "type": r.type_display,
        "service": r.service,
        "service_id": r.service_id,
        "margin_percent": str(quantize_money(r.margin_percent)) if r.margin_percent is not None else None,
        "start_date": r.start_date.isoformat() if r.start_date else None,
        "end_date": r.end_date.isoformat() if r.end_date else None,
        "work_days": r.work_days,
        "work_hours": r.work_hours,
        "monthly_cost": str(quantize_money(r.monthly_cost)) if r.monthly_cost is not None else None,
        "base_no_tax": str(quantize_money(r.base_no_tax)),
        "cost": str(quantize_money(r.cost)),
        "final_cost": str(quantize_money(r.final_cost)),
        "pricing_type": r.pricing_type,
        "months_count": r.months_count,
    }
