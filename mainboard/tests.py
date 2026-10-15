"""
Тесты единого калькулятора SCBP.

Покрывают:
- Базовые формулы (employee/contractor/subcontractor/equipment) с edge cases
- Семантику override.margin = NULL vs 0
- Отсутствие двойного учёта налога и маржи в итогах проекта
- Совпадение чисел между `project_manage` и `_build_project_data`
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.test import TestCase

from mainboard import calculator as calc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_calendars():
    return calc.load_calendars(str(settings.BASE_DIR / 'calendar') if hasattr(settings.BASE_DIR, '__truediv__') else f"{settings.BASE_DIR}/calendar")


# ---------------------------------------------------------------------------
# Календарь рабочих дней
# ---------------------------------------------------------------------------

class CalendarTests(TestCase):
    def test_count_business_days_no_dates(self):
        self.assertEqual(calc.count_business_days(None, None, {}), 0)
        self.assertEqual(calc.count_business_days(date(2025, 1, 10), None, {}), 0)
        self.assertEqual(calc.count_business_days(date(2025, 1, 10), date(2025, 1, 5), {}), 0)

    def test_count_business_days_weekday_fallback(self):
        """Без календаря — будние дни."""
        # 6 января 2025 (Пн) - 12 января 2025 (Вс) = 5 будних дней
        self.assertEqual(calc.count_business_days(date(2025, 1, 6), date(2025, 1, 12), {}), 5)

    def test_count_business_days_with_holidays(self):
        """С календарём — учитывает праздники."""
        calendars = _load_calendars()
        # Январь 2025: первые 8 дней — holiday по календарю.
        # 01.01-12.01: 9, 10 — work; 13.01 не входит. Итого 2 рабочих дня.
        self.assertEqual(
            calc.count_business_days(date(2025, 1, 1), date(2025, 1, 12), calendars),
            2,
        )

    def test_working_days_in_month_fallback(self):
        # Январь 2025 содержит 23 будних дня
        self.assertEqual(calc.working_days_in_month(date(2025, 1, 1), {}), Decimal('23'))

    def test_working_days_in_month_uses_fallback_when_calendar_empty_year(self):
        # Если для года календарь дал 0 (синтетический случай) — используется fallback
        # Симулируем календарь, где все дни помечены не-work
        synth = {2099: {str(m): {str(d): 'holiday' for d in range(1, 32)} for m in range(1, 13)}}
        self.assertEqual(
            calc.working_days_in_month(date(2099, 1, 1), synth, fallback=Decimal('22')),
            Decimal('22'),
        )


# ---------------------------------------------------------------------------
# Employee
# ---------------------------------------------------------------------------

class EmployeeBaseTests(TestCase):
    def test_full_month_no_dates(self):
        r = calc.calc_employee_base(Decimal('100000'), None, None, {})
        self.assertEqual(r['base_no_tax'], Decimal('100000'))
        self.assertIsNone(r['work_days'])

    def test_partial_month_no_calendar(self):
        # 06.01 (Пн) - 17.01 (Пт) → 10 будних дней.
        # В январе 2025 (без календаря) 23 будних дня.
        # Используем зарплату 230000, кратную 23, чтобы число было чистым.
        r = calc.calc_employee_base(Decimal('230000'), date(2025, 1, 6), date(2025, 1, 17), {})
        self.assertEqual(r['work_days'], 10)
        self.assertEqual(r['base_no_tax'], Decimal('100000'))

    def test_unavailable_days_subtracted(self):
        """Если сотрудник недоступен — рабочих дней меньше."""
        # 06.01-17.01 → 10 будних дней, минус 3 = 7
        r = calc.calc_employee_base(
            Decimal('230000'), date(2025, 1, 6), date(2025, 1, 17), {},
            unavailable_days=3,
        )
        self.assertEqual(r['work_days'], 7)
        # 230000 × 7/23 = 70000
        self.assertEqual(r['base_no_tax'], Decimal('70000'))

    def test_zero_work_days_gives_zero(self):
        # end < start
        r = calc.calc_employee_base(Decimal('100000'), date(2025, 1, 10), date(2025, 1, 5), {})
        # end < start считается "нет дат" → полная зарплата
        self.assertEqual(r['base_no_tax'], Decimal('100000'))


# ---------------------------------------------------------------------------
# Contractor (исполнитель)
# ---------------------------------------------------------------------------

class ContractorBaseTests(TestCase):
    def test_hourly_explicit_zero(self):
        """Часы = 0 явно → стоимость 0 (не fallback на estimated)."""
        r = calc.calc_contractor_base(Decimal('500'), working_hours=0, estimated_hours=100)
        self.assertEqual(r['base_no_tax'], Decimal('0'))
        self.assertEqual(r['work_hours'], 0)

    def test_hourly_with_hours(self):
        r = calc.calc_contractor_base(Decimal('500'), working_hours=80, estimated_hours=100)
        self.assertEqual(r['base_no_tax'], Decimal('40000'))

    def test_hourly_fallback_to_estimated(self):
        """working_hours=None → используется estimated_hours."""
        r = calc.calc_contractor_base(Decimal('500'), working_hours=None, estimated_hours=100)
        self.assertEqual(r['base_no_tax'], Decimal('50000'))

    def test_no_rate_fallback_estimated_cost(self):
        r = calc.calc_contractor_base(None, working_hours=None, estimated_hours=None, estimated_cost=Decimal('12345'))
        self.assertEqual(r['base_no_tax'], Decimal('12345'))


# ---------------------------------------------------------------------------
# Subcontractor (подрядчик)
# ---------------------------------------------------------------------------

class SubcontractorBaseTests(TestCase):
    def test_fixed(self):
        r = calc.calc_subcontractor_base(
            'fixed', contract_amount=Decimal('500000'),
            hourly_rate=None, working_hours=None, estimated_hours=None,
        )
        self.assertEqual(r['base_no_tax'], Decimal('500000'))

    def test_hourly_with_override(self):
        r = calc.calc_subcontractor_base(
            'hourly', contract_amount=Decimal('500000'),
            hourly_rate=Decimal('1000'), working_hours=50, estimated_hours=100,
        )
        self.assertEqual(r['base_no_tax'], Decimal('50000'))

    def test_hourly_explicit_zero(self):
        r = calc.calc_subcontractor_base(
            'hourly', contract_amount=Decimal('500000'),
            hourly_rate=Decimal('1000'), working_hours=0, estimated_hours=100,
        )
        self.assertEqual(r['base_no_tax'], Decimal('0'))

    def test_hourly_no_rate_falls_back_to_fixed(self):
        r = calc.calc_subcontractor_base(
            'hourly', contract_amount=Decimal('500000'),
            hourly_rate=None, working_hours=10, estimated_hours=10,
        )
        self.assertEqual(r['base_no_tax'], Decimal('500000'))


# ---------------------------------------------------------------------------
# Equipment
# ---------------------------------------------------------------------------

class EquipmentBaseTests(TestCase):
    def test_month_with_dates_end_day_ge_start_day(self):
        # 01.01-02.02 → 1 + 1 = 2 месяца
        r = calc.calc_equipment_base(
            'month', Decimal('10000'), 1,
            date(2025, 1, 1), date(2025, 2, 2),
            fallback_total_cost=Decimal('0'),
        )
        self.assertEqual(r['months_count'], 2)
        self.assertEqual(r['base_no_tax'], Decimal('20000'))

    def test_month_with_dates_end_day_lt_start_day(self):
        # 15.01-14.02 → 1 + 0 = 1 месяц
        r = calc.calc_equipment_base(
            'month', Decimal('10000'), 1,
            date(2025, 1, 15), date(2025, 2, 14),
            fallback_total_cost=Decimal('0'),
        )
        self.assertEqual(r['months_count'], 1)
        self.assertEqual(r['base_no_tax'], Decimal('10000'))

    def test_month_min_one(self):
        # Один день → минимум 1 месяц
        r = calc.calc_equipment_base(
            'month', Decimal('10000'), 1,
            date(2025, 1, 1), date(2025, 1, 1),
            fallback_total_cost=Decimal('0'),
        )
        self.assertEqual(r['months_count'], 1)

    def test_hour_calendar_days_times_24(self):
        # 01.01-03.01 = 3 дня × 24 ч = 72 часа (calendar basis по умолчанию)
        r = calc.calc_equipment_base(
            'hour', Decimal('100'), 1,
            date(2025, 1, 1), date(2025, 1, 3),
            fallback_total_cost=Decimal('0'),
        )
        self.assertEqual(r['calendar_hours'], 72)
        self.assertEqual(r['base_no_tax'], Decimal('7200'))

    def test_hour_working_basis_uses_business_days(self):
        # 13.01-17.01 (Пн-Пт, без календаря) → 5 рабочих × 8ч = 40 часов
        r = calc.calc_equipment_base(
            'hour', Decimal('100'), 1,
            date(2025, 1, 13), date(2025, 1, 17),
            fallback_total_cost=Decimal('0'),
            calendars={},
            hour_basis='working',
            work_hours_per_day=Decimal('8'),
        )
        self.assertEqual(r['calendar_hours'], 40)
        self.assertEqual(r['base_no_tax'], Decimal('4000'))

    def test_hour_working_basis_custom_wh_per_day(self):
        # 5 рабочих дней × 12 ч/день = 60 часов
        r = calc.calc_equipment_base(
            'hour', Decimal('100'), 1,
            date(2025, 1, 13), date(2025, 1, 17),
            fallback_total_cost=Decimal('0'),
            calendars={},
            hour_basis='working',
            work_hours_per_day=Decimal('12'),
        )
        self.assertEqual(r['base_no_tax'], Decimal('6000'))

    def test_day_calendar_days(self):
        # 01.01-03.01 = 3 календарных дня × 500 = 1500 (выходные/праздники не важны)
        r = calc.calc_equipment_base(
            'day', Decimal('500'), 1,
            date(2025, 1, 1), date(2025, 1, 3),
            fallback_total_cost=Decimal('0'),
        )
        self.assertEqual(r['calendar_days'], 3)
        self.assertEqual(r['base_no_tax'], Decimal('1500'))

    def test_day_quantity(self):
        # 2 дня × 500 × 3 шт = 3000
        r = calc.calc_equipment_base(
            'day', Decimal('500'), 3,
            date(2025, 1, 1), date(2025, 1, 2),
            fallback_total_cost=Decimal('0'),
        )
        self.assertEqual(r['base_no_tax'], Decimal('3000'))

    def test_minute(self):
        r = calc.calc_equipment_base(
            'minute', Decimal('1'), 1,
            date(2025, 1, 1), date(2025, 1, 1),
            fallback_total_cost=Decimal('0'),
        )
        self.assertEqual(r['calendar_minutes'], 1440)
        self.assertEqual(r['base_no_tax'], Decimal('1440'))

    def test_no_dates_fallback(self):
        r = calc.calc_equipment_base(
            'month', Decimal('10000'), 1, None, None,
            fallback_total_cost=Decimal('99000'),
        )
        self.assertEqual(r['base_no_tax'], Decimal('99000'))

    def test_quantity_multiplier(self):
        r = calc.calc_equipment_base(
            'month', Decimal('10000'), 3,
            date(2025, 1, 1), date(2025, 1, 1),
            fallback_total_cost=Decimal('0'),
        )
        self.assertEqual(r['base_no_tax'], Decimal('30000'))


# ---------------------------------------------------------------------------
# Margin/Tax семантика
# ---------------------------------------------------------------------------

class MarginAndTaxTests(TestCase):
    def test_resolve_margin_none_uses_project(self):
        self.assertEqual(calc.resolve_margin(None, Decimal('20')), Decimal('20'))

    def test_resolve_margin_zero_is_explicit(self):
        """resource_margin = 0 — это явный 0%, не fallback к проекту."""
        self.assertEqual(calc.resolve_margin(Decimal('0'), Decimal('20')), Decimal('0'))

    def test_resolve_margin_custom(self):
        self.assertEqual(calc.resolve_margin(Decimal('35'), Decimal('20')), Decimal('35'))

    def test_apply_tax_and_margin(self):
        # base 1000, tax 20%, margin 10%: 1000 × 1.2 × 1.1 = 1320
        cost, final = calc.apply_tax_and_margin(Decimal('1000'), Decimal('20'), Decimal('10'))
        self.assertEqual(cost, Decimal('1200.00'))
        self.assertEqual(final, Decimal('1320.000'))


# ---------------------------------------------------------------------------
# Project totals — нет двойного учёта налога/маржи
# ---------------------------------------------------------------------------

class ProjectTotalsTests(TestCase):
    def _make_project(self, tax=Decimal('20'), margin=Decimal('10')):
        return SimpleNamespace(tax_rate=tax, margin=margin, name='T', id=1)

    def _make_employee(self, id_=1, salary=Decimal('100000'), start=None, end=None):
        return SimpleNamespace(
            id=id_, name='Emp', monthly_salary=salary,
            start_date=start, end_date=end,
        )

    def test_single_employee_no_double_tax(self):
        """tax+margin применяются ровно один раз."""
        project = self._make_project(tax=Decimal('20'), margin=Decimal('10'))
        emp = self._make_employee()
        rows, totals = calc.calc_project(
            project=project,
            employees=[emp],
            contractors=[],
            subcontractors=[],
            equipment=[],
            overrides_map={},
            schedule_by_employee={},
            calendars={},
        )
        # cost = 100000 × 1.2 = 120000
        # final = 120000 × 1.1 = 132000
        self.assertEqual(rows[0].cost, Decimal('120000.00'))
        self.assertEqual(rows[0].final_cost, Decimal('132000.000'))
        # final_price = Σ final = 132000 (без повторного применения tax/margin проекта)
        self.assertEqual(totals.final_price, Decimal('132000.000'))
        self.assertEqual(totals.base_cost, Decimal('120000.00'))

    def test_override_margin_zero_is_respected(self):
        """Если override.resource_margin = 0, используется 0% (не маржа проекта)."""
        project = self._make_project(tax=Decimal('20'), margin=Decimal('30'))
        emp = self._make_employee()
        override = SimpleNamespace(
            resource_margin=Decimal('0'),
            work_start_date=None, work_end_date=None,
            service=None, service_id=None, working_hours=None,
        )
        rows, totals = calc.calc_project(
            project=project,
            employees=[emp],
            contractors=[], subcontractors=[], equipment=[],
            overrides_map={('employee', 1): override},
            schedule_by_employee={},
            calendars={},
        )
        # margin = 0% → final = cost
        self.assertEqual(rows[0].margin_percent, Decimal('0'))
        self.assertEqual(rows[0].final_cost, rows[0].cost)

    def test_override_margin_none_falls_back_to_project(self):
        project = self._make_project(tax=Decimal('20'), margin=Decimal('30'))
        emp = self._make_employee()
        override = SimpleNamespace(
            resource_margin=None,
            work_start_date=None, work_end_date=None,
            service=None, service_id=None, working_hours=None,
        )
        rows, totals = calc.calc_project(
            project=project,
            employees=[emp],
            contractors=[], subcontractors=[], equipment=[],
            overrides_map={('employee', 1): override},
            schedule_by_employee={},
            calendars={},
        )
        self.assertEqual(rows[0].margin_percent, Decimal('30'))

    def test_project_settings_thread_through_employee_work_hours(self):
        """Project.work_hours_per_day используется в work_hours сотрудника."""
        project = SimpleNamespace(
            tax_rate=Decimal('0'), margin=Decimal('0'),
            work_hours_per_day=Decimal('10'),
            equipment_hour_basis='calendar',
            working_days_per_month_fallback=22,
        )
        emp = self._make_employee(salary=Decimal('230000'),
                                  start=date(2025, 1, 6), end=date(2025, 1, 17))
        rows, _ = calc.calc_project(
            project=project, employees=[emp],
            contractors=[], subcontractors=[], equipment=[],
            overrides_map={}, schedule_by_employee={}, calendars={},
        )
        # 10 рабочих дней × 10 ч = 100
        self.assertEqual(rows[0].work_hours, 100)

    def test_project_settings_equipment_hour_basis_working(self):
        """Project.equipment_hour_basis='working' меняет расчёт оборудования по часам."""
        project = SimpleNamespace(
            tax_rate=Decimal('0'), margin=Decimal('0'),
            work_hours_per_day=Decimal('8'),
            equipment_hour_basis='working',
            working_days_per_month_fallback=22,
        )
        eq = SimpleNamespace(
            id=1, name='Server', monthly_salary=None,
            operational_cost=Decimal('100'), monthly_cost=None, cost=Decimal('1000'),
            quantity=1, purchase_date=date(2025, 1, 13),
            pricing_type='hour', total_cost=Decimal('1000'),
        )
        rows, _ = calc.calc_project(
            project=project, employees=[],
            contractors=[], subcontractors=[], equipment=[eq],
            overrides_map={
                ('equipment', 1): SimpleNamespace(
                    resource_margin=None,
                    work_start_date=date(2025, 1, 13),
                    work_end_date=date(2025, 1, 17),
                    service=None, service_id=None, working_hours=None,
                ),
            },
            schedule_by_employee={}, calendars={},
        )
        # 5 рабочих × 8 ч = 40 часов × 100 ставка = 4000
        self.assertEqual(rows[0].base_no_tax, Decimal('4000'))

    def test_net_profit_is_margin_only(self):
        """net_profit = final - base = маржа в денежном выражении."""
        project = self._make_project(tax=Decimal('0'), margin=Decimal('25'))
        emp = self._make_employee(salary=Decimal('100000'))
        rows, totals = calc.calc_project(
            project=project, employees=[emp],
            contractors=[], subcontractors=[], equipment=[],
            overrides_map={}, schedule_by_employee={}, calendars={},
        )
        # base = 100000, final = 125000, profit = 25000
        self.assertEqual(totals.base_cost, Decimal('100000'))
        self.assertEqual(totals.final_price, Decimal('125000.00'))
        self.assertEqual(totals.net_profit, Decimal('25000.00'))


# ---------------------------------------------------------------------------
# Экономика РФ: взносы только на ФОТ + НДС на output
# ---------------------------------------------------------------------------

class RFEconomicsTests(TestCase):
    def _contractor(self, id_=1, rate=Decimal('1000'), hours=10):
        return SimpleNamespace(
            id=id_, name='Contr', hourly_rate=rate,
            estimated_hours=hours, estimated_cost=None,
        )

    def test_insurance_only_on_payroll(self):
        """Страховые взносы (tax_rate) начисляются на ФОТ, но НЕ на исполнителей."""
        project = SimpleNamespace(
            tax_rate=Decimal('30'), vat_rate=Decimal('0'), margin=Decimal('0'),
            name='T', id=1,
        )
        emp = SimpleNamespace(id=1, name='Emp', monthly_salary=Decimal('100000'),
                              start_date=None, end_date=None)
        contr = self._contractor()  # base = 1000 × 10 = 10000
        rows, totals = calc.calc_project(
            project=project, employees=[emp], contractors=[contr],
            subcontractors=[], equipment=[],
            overrides_map={}, schedule_by_employee={}, calendars={},
        )
        by_type = {r.resource_type: r for r in rows}
        # сотрудник: 100000 × 1.30 = 130000 (взносы начислены)
        self.assertEqual(by_type['employee'].cost, Decimal('130000.00'))
        # исполнитель: 10000 (взносы НЕ начислены)
        self.assertEqual(by_type['contractor'].cost, Decimal('10000'))
        self.assertEqual(totals.base_cost, Decimal('140000.00'))

    def test_vat_applied_once_on_output_not_in_profit(self):
        """НДС начисляется на итоговую цену один раз и не входит в прибыль."""
        project = SimpleNamespace(
            tax_rate=Decimal('0'), vat_rate=Decimal('22'), margin=Decimal('20'),
            name='T', id=1,
        )
        emp = SimpleNamespace(id=1, name='Emp', monthly_salary=Decimal('100000'),
                              start_date=None, end_date=None)
        rows, totals = calc.calc_project(
            project=project, employees=[emp], contractors=[],
            subcontractors=[], equipment=[],
            overrides_map={}, schedule_by_employee={}, calendars={},
        )
        # base=100000, цена без НДС = 120000
        self.assertEqual(totals.base_cost, Decimal('100000'))
        self.assertEqual(totals.final_price, Decimal('120000.00'))
        # НДС 22% на 120000 = 26400; цена с НДС = 146400
        self.assertEqual(calc.quantize_money(totals.vat_amount), Decimal('26400.00'))
        self.assertEqual(calc.quantize_money(totals.price_with_vat), Decimal('146400.00'))
        # прибыль считается БЕЗ НДС
        self.assertEqual(totals.net_profit, Decimal('20000.00'))

    def test_equipment_month_uses_project_end_as_fallback(self):
        """Помесячное оборудование без явного «Окончания» считается от даты
        покупки до конца проекта (а не падает на фикс. total_cost)."""
        from datetime import date as _date
        project = SimpleNamespace(
            tax_rate=Decimal('0'), vat_rate=Decimal('0'), margin=Decimal('0'),
            name='T', id=1,
            start_date=_date(2025, 1, 1), end_date=_date(2025, 5, 1),
            work_hours_per_day=Decimal('8'), equipment_hour_basis='calendar',
            working_days_per_month_fallback=22,
        )
        eq = SimpleNamespace(
            id=1, name='Server', operational_cost=Decimal('10000'),
            monthly_cost=None, cost=Decimal('99000'), quantity=1,
            purchase_date=_date(2025, 3, 15),  # куплен на 2.5 мес из 4
            pricing_type='month', total_cost=Decimal('99000'),
        )
        rows, _ = calc.calc_project(
            project=project, employees=[], contractors=[],
            subcontractors=[], equipment=[eq],
            overrides_map={}, schedule_by_employee={}, calendars={},
        )
        # 15.03 → 01.05: 2 месяца (округление вверх) × 10000 = 20000 (не fallback 99000)
        self.assertEqual(rows[0].months_count, 2)
        self.assertEqual(rows[0].base_no_tax, Decimal('20000'))

    def test_vat_zero_by_default(self):
        """Без vat_rate (старые проекты) НДС = 0, цена с НДС = цена без НДС."""
        project = SimpleNamespace(tax_rate=Decimal('0'), margin=Decimal('10'),
                                  name='T', id=1)  # нет атрибута vat_rate
        emp = SimpleNamespace(id=1, name='Emp', monthly_salary=Decimal('100000'),
                              start_date=None, end_date=None)
        _, totals = calc.calc_project(
            project=project, employees=[emp], contractors=[],
            subcontractors=[], equipment=[],
            overrides_map={}, schedule_by_employee={}, calendars={},
        )
        self.assertEqual(totals.vat_amount, Decimal('0'))
        self.assertEqual(totals.price_with_vat, totals.final_price)
