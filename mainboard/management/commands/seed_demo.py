"""
Очистка домена и посев демо-данных всех видов для тестирования.

Удаляет проекты/ресурсы/услуги/расчёты (НЕ трогает пользователей/вход),
затем создаёт услуги всех категорий и демо-проект с ресурсами всех типов
и вариаций тарификации.

Запуск:
    python manage.py seed_demo            # очистить и засеять
    python manage.py seed_demo --keep     # только засеять, без очистки
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from mainboard.models import (
    Project, Employee, Contractor, Subcontractor, Equipment,
    Service, ProjectResourceOverride, EmployeeSchedule, ProjectCost,
)


class Command(BaseCommand):
    help = "Очистить домен (кроме пользователей) и засеять демо-данные всех видов"

    def add_arguments(self, parser):
        parser.add_argument('--keep', action='store_true', help='Не удалять существующие данные')

    @transaction.atomic
    def handle(self, *args, **opts):
        if not opts['keep']:
            self.stdout.write('Очистка домена (пользователи сохраняются)...')
            for model in (ProjectResourceOverride, EmployeeSchedule, ProjectCost,
                          Employee, Contractor, Subcontractor, Equipment,
                          Service, Project):
                n, _ = model.objects.all().delete()
                self.stdout.write(f'  − {model.__name__}: удалено {n}')

        # --- Услуги всех категорий ---
        services = [
            ('Разработка ПО', 'employee', 'Проектирование и кодирование'),
            ('Аналитика и требования', 'employee', 'Сбор и анализ требований'),
            ('Тестирование QA', 'contractor', 'Ручное и авто-тестирование'),
            ('UX/UI дизайн', 'contractor', 'Интерфейсы и прототипы'),
            ('Внешняя интеграция', 'subcontractor', 'Подряд на интеграции'),
            ('Аренда сервера', 'equipment', 'Хостинг/облако'),
            ('Лицензия ПО', 'equipment', 'Подписки и лицензии'),
            ('Управление проектом', 'any', 'PM, общие услуги'),
        ]
        svc = {}
        for name, applies, desc in services:
            svc[name] = Service.objects.create(name=name, applies_to=applies, description=desc, is_active=True)
        # одна скрытая услуга для проверки фильтра
        Service.objects.create(name='Архивная услуга', applies_to='any', is_active=False)
        self.stdout.write(self.style.SUCCESS(f'Услуги: {Service.objects.count()}'))

        # --- Демо-проект ---
        project = Project.objects.create(
            name='Демо-проект (все типы ресурсов)',
            customer='ООО «Тестовый Заказчик»',
            description='Сгенерирован seed_demo для проверки всех вариаций расчёта',
            status='Создан',
            start_date=date(2026, 1, 1),
            end_date=date(2026, 6, 30),
            tax_rate=Decimal('30.00'),   # страховые взносы на ФОТ
            vat_rate=Decimal('22.00'),   # НДС на итог
            margin=Decimal('15.00'),     # наценка
            work_hours_per_day=Decimal('8.00'),
        )

        # Сотрудники: с датами, без дат, с override-маржой
        emp1 = Employee.objects.create(project=project, name='Иванов И.И.', position='Backend-разработчик',
                                       monthly_salary=Decimal('200000'), start_date=date(2026, 1, 1), end_date=date(2026, 4, 30))
        emp2 = Employee.objects.create(project=project, name='Петрова А.С.', position='Аналитик',
                                       monthly_salary=Decimal('150000'))  # без дат → полный месяц
        Employee.objects.create(project=project, name='Сидоров П.П.', position='Frontend-разработчик',
                                monthly_salary=Decimal('180000'), start_date=date(2026, 2, 1), end_date=date(2026, 6, 30))

        # Исполнители: ГПХ и НПД, почасовые
        con1 = Contractor.objects.create(project=project, name='Кузнецов (ГПХ)', contract_type='gph',
                                         hourly_rate=Decimal('1500'), estimated_hours=160)
        Contractor.objects.create(project=project, name='Морозова (НПД)', contract_type='npd',
                                  hourly_rate=Decimal('2000'), estimated_hours=80)

        # Подрядчики: фикс и почасовой, юрлицо/физлицо
        sub1 = Subcontractor.objects.create(project=project, name='ООО «Интегратор»', entity_type='legal',
                                            pricing_type='fixed', contract_amount=Decimal('500000'),
                                            hourly_rate=Decimal('0'), estimated_hours=0, contract_date=date(2026, 1, 15))
        Subcontractor.objects.create(project=project, name='ИП Смирнов', entity_type='individual',
                                     pricing_type='hourly', contract_amount=Decimal('0'),
                                     hourly_rate=Decimal('3000'), estimated_hours=100, contract_date=date(2026, 2, 1))

        # Оборудование: все 4 типа тарификации
        Equipment.objects.create(project=project, name='Сервер Dell R750', model='R750', cost=Decimal('600000'),
                                 pricing_type='month', operational_cost=Decimal('25000'), quantity=1,
                                 purchase_date=date(2026, 3, 15))  # помесячно, куплен в середине проекта
        Equipment.objects.create(project=project, name='Тестовый стенд (аренда)', cost=Decimal('50000'),
                                 pricing_type='day', operational_cost=Decimal('1500'), quantity=2,
                                 purchase_date=date(2026, 2, 1))   # посуточно
        Equipment.objects.create(project=project, name='GPU-кластер', cost=Decimal('900000'),
                                 pricing_type='hour', operational_cost=Decimal('300'), quantity=1,
                                 purchase_date=date(2026, 1, 10))  # почасово
        Equipment.objects.create(project=project, name='API-вызовы (биллинг)', cost=Decimal('10000'),
                                 pricing_type='minute', operational_cost=Decimal('2'), quantity=1,
                                 purchase_date=date(2026, 1, 5))   # поминутно

        # Override-параметры: услуга + маржа на отдельных ресурсах
        ProjectResourceOverride.objects.create(project=project, resource_type='employee', resource_id=emp1.id,
                                               service=svc['Разработка ПО'], resource_margin=Decimal('20'))
        ProjectResourceOverride.objects.create(project=project, resource_type='employee', resource_id=emp2.id,
                                               service=svc['Аналитика и требования'], resource_margin=None)  # маржа проекта
        ProjectResourceOverride.objects.create(project=project, resource_type='contractor', resource_id=con1.id,
                                               service=svc['Тестирование QA'], working_hours=120)
        ProjectResourceOverride.objects.create(project=project, resource_type='subcontractor', resource_id=sub1.id,
                                               service=svc['Внешняя интеграция'])

        # Запись расписания (недоступность сотрудника)
        EmployeeSchedule.objects.create(employee=emp1, start_date=date(2026, 3, 1), end_date=date(2026, 3, 10),
                                        status='dayoff', note='Отпуск')

        self.stdout.write(self.style.SUCCESS(
            f'Проект «{project.name}» (id={project.id}): '
            f'{project.employees.count()} сотр., {project.contractors.count()} исп., '
            f'{project.subcontractors.count()} подр., {project.equipment.count()} обор.'
        ))
        self.stdout.write(self.style.SUCCESS('Готово. Демо-данные засеяны.'))
