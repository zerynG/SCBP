from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from decimal import Decimal


# ============================================================================
# Рабочие области
# ============================================================================

class Workspace(models.Model):
    """Рабочая область — контейнер верхнего уровня для проектов и ресурсов."""
    name = models.CharField(max_length=120, verbose_name="Название")
    subdomain = models.SlugField(
        max_length=40, unique=True,
        verbose_name="Поддомен",
        help_text="Только латиница, цифры и дефис. Например: marketing-team"
    )
    description = models.TextField(blank=True, verbose_name="Описание")
    admin_name = models.CharField(max_length=150, verbose_name="ФИО администратора")
    admin_email = models.EmailField(verbose_name="Email администратора")
    admin_phone = models.CharField(max_length=20, blank=True, verbose_name="Телефон администратора")
    admin_role = models.CharField(max_length=100, blank=True, verbose_name="Должность администратора")
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Рабочая область"
        verbose_name_plural = "Рабочие области"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.subdomain})"


# ============================================================================
# Проект
# ============================================================================

class Project(models.Model):
    """Модель проекта."""
    workspace = models.ForeignKey(
        Workspace, on_delete=models.SET_NULL,
        related_name='projects', null=True, blank=True,
        verbose_name="Рабочая область",
    )
    name = models.CharField(max_length=200, verbose_name="Название проекта")
    description = models.TextField(blank=True, verbose_name="Описание")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    technical_task_url = models.URLField(blank=True, verbose_name="Техническое задание (ссылка)")
    status = models.CharField(max_length=50, default='Создан', verbose_name="Статус")
    customer = models.CharField(max_length=200, blank=True, verbose_name="Заказчик")
    start_date = models.DateField(null=True, blank=True, verbose_name="Дата начала")
    end_date = models.DateField(null=True, blank=True, verbose_name="Дата окончания")

    # Страховые взносы на ФОТ (%). Начисляются ТОЛЬКО на оклад штатных сотрудников.
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.00'),
        verbose_name="Страховые взносы на ФОТ (%)",
    )
    # НДС (%) на отпускную цену.
    vat_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.00'),
        verbose_name="НДС (%)",
    )
    # Наценка/рентабельность (%).
    margin = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.00'),
        verbose_name="Наценка / рентабельность (%)",
    )

    # Параметры расчёта
    work_hours_per_day = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal('8.00'),
        verbose_name="Рабочих часов в дне (КЧЧ)",
    )
    EQUIPMENT_HOUR_BASIS_CHOICES = [
        ('calendar', 'Календарные часы (дней × 24)'),
        ('working', 'Рабочие часы (рабочих_дней × КЧЧ)'),
    ]
    equipment_hour_basis = models.CharField(
        max_length=10, choices=EQUIPMENT_HOUR_BASIS_CHOICES, default='calendar',
        verbose_name="База часов для оборудования (pricing='hour')",
    )
    working_days_per_month_fallback = models.PositiveSmallIntegerField(
        default=22,
        verbose_name="Fallback рабочих дней в месяце (если нет календаря)",
    )

    class Meta:
        verbose_name = "Проект"
        verbose_name_plural = "Проекты"
        ordering = ['-created_at']

    def __str__(self):
        return self.name


# ============================================================================
# Ресурсы каталога (в рамках Workspace)
# ============================================================================

class Employee(models.Model):
    """Сотрудник — глобальный каталог в рамках рабочей области."""
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE,
        related_name='employees', verbose_name="Рабочая область",
    )
    name = models.CharField(max_length=150, verbose_name="ФИО")
    position = models.CharField(max_length=100, verbose_name="Должность")
    monthly_salary = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Месячная зарплата",
    )
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('13.00'),
        verbose_name="Налоговая ставка (%)",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = "Сотрудник"
        verbose_name_plural = "Сотрудники"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} — {self.position}"


class Contractor(models.Model):
    """Исполнитель (ГПХ/НПД) — глобальный каталог."""
    CONTRACT_TYPE_CHOICES = [
        ('gph', 'ГПХ'),
        ('npd', 'НПД'),
    ]
    UNIT_CHOICES = [
        ('hours', 'Часы'),
        ('days', 'Дни'),
        ('fixed', 'Полная стоимость'),
    ]
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE,
        related_name='contractors', verbose_name="Рабочая область",
    )
    name = models.CharField(max_length=150, verbose_name="ФИО")
    contract_type = models.CharField(
        max_length=3, choices=CONTRACT_TYPE_CHOICES, default='gph',
        verbose_name="Тип договора",
    )
    unit = models.CharField(
        max_length=10, choices=UNIT_CHOICES, default='hours',
        verbose_name="Единица измерения",
    )
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('13.00'),
        verbose_name="Налоговая ставка (%)",
    )
    hourly_cost = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        verbose_name="Стоимость за час",
    )
    daily_cost = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        verbose_name="Стоимость за день",
    )
    fixed_cost = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        verbose_name="Полная стоимость",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = "Исполнитель"
        verbose_name_plural = "Исполнители"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_contract_type_display()})"


class Subcontractor(models.Model):
    """Субподрядчик — глобальный каталог."""
    ENTITY_TYPE_CHOICES = [
        ('legal', 'Юридическое лицо'),
        ('ip', 'Индивидуальный предприниматель'),
    ]
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE,
        related_name='subcontractors', verbose_name="Рабочая область",
    )
    name = models.CharField(max_length=200, verbose_name="Наименование")
    entity_type = models.CharField(
        max_length=10, choices=ENTITY_TYPE_CHOICES, default='legal',
        verbose_name="Тип контрагента",
    )
    inn = models.CharField(max_length=12, blank=True, verbose_name="ИНН")
    kpp = models.CharField(max_length=9, blank=True, verbose_name="КПП")
    ogrn = models.CharField(max_length=15, blank=True, verbose_name="ОГРН/ОГРНИП")
    legal_address = models.CharField(max_length=300, blank=True, verbose_name="Юридический адрес")
    actual_address = models.CharField(max_length=300, blank=True, verbose_name="Фактический адрес")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Телефон")
    email = models.EmailField(blank=True, verbose_name="Email")
    director = models.CharField(max_length=150, blank=True, verbose_name="ФИО руководителя")
    bank_name = models.CharField(max_length=200, blank=True, verbose_name="Наименование банка")
    bik = models.CharField(max_length=9, blank=True, verbose_name="БИК")
    account = models.CharField(max_length=20, blank=True, verbose_name="Расчётный счёт")
    corr_account = models.CharField(max_length=20, blank=True, verbose_name="Корреспондентский счёт")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = "Субподрядчик"
        verbose_name_plural = "Субподрядчики"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_entity_type_display()})"


class Customer(models.Model):
    """Заказчик — глобальный каталог."""
    CUSTOMER_TYPE_CHOICES = [
        ('individual', 'Физическое лицо'),
        ('ip', 'Индивидуальный предприниматель'),
        ('legal', 'Юридическое лицо'),
    ]
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE,
        related_name='customers', verbose_name="Рабочая область",
    )
    name = models.CharField(max_length=150, verbose_name="ФИО")
    customer_type = models.CharField(
        max_length=10, choices=CUSTOMER_TYPE_CHOICES, default='individual',
        verbose_name="Тип заказчика",
    )
    inn = models.CharField(max_length=12, blank=True, verbose_name="ИНН")
    organization = models.CharField(max_length=200, blank=True, verbose_name="Название организации")
    email = models.EmailField(blank=True, verbose_name="Email")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Телефон")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = "Заказчик"
        verbose_name_plural = "Заказчики"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_customer_type_display()})"


class Equipment(models.Model):
    """Оборудование — глобальный каталог."""
    ACQUISITION_TYPE_CHOICES = [
        ('owned', 'Собственное'),
        ('rented', 'В аренде'),
    ]
    UNIT_CHOICES = [
        ('hours', 'Часы'),
        ('days', 'Дни'),
    ]
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE,
        related_name='equipment', verbose_name="Рабочая область",
    )
    name = models.CharField(max_length=200, verbose_name="Название")
    acquisition_type = models.CharField(
        max_length=10, choices=ACQUISITION_TYPE_CHOICES, default='owned',
        verbose_name="Тип приобретения",
    )
    unit = models.CharField(
        max_length=10, choices=UNIT_CHOICES, default='hours',
        verbose_name="Единица измерения",
    )
    cost = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Стоимость",
    )
    operational_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        verbose_name="Эксплуатационная стоимость",
    )
    description = models.TextField(blank=True, verbose_name="Описание")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = "Оборудование"
        verbose_name_plural = "Оборудование"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_acquisition_type_display()})"


# ============================================================================
# Услуги
# ============================================================================

class Service(models.Model):
    """Глобальный реестр услуг."""
    APPLIES_TO_CHOICES = [
        ('any', 'Любой ресурс'),
        ('employee', 'Сотрудники'),
        ('contractor', 'Исполнители'),
        ('subcontractor', 'Подрядчики'),
        ('equipment', 'Оборудование'),
    ]
    name = models.CharField(max_length=255, unique=True, verbose_name="Название услуги")
    description = models.TextField(blank=True, verbose_name="Описание")
    applies_to = models.CharField(
        max_length=20, choices=APPLIES_TO_CHOICES, default='any',
        verbose_name="Применяется к",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активна")

    class Meta:
        verbose_name = "Услуга"
        verbose_name_plural = "Услуги"
        ordering = ['applies_to', 'name']

    def __str__(self):
        return self.name


# ============================================================================
# Расчёт стоимости проекта
# ============================================================================

class ProjectCost(models.Model):
    """Модель для хранения расчетов стоимости проекта."""
    project = models.OneToOneField(
        Project, on_delete=models.CASCADE,
        related_name='cost_calculation', verbose_name="Проект",
    )
    total_employee_cost = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        verbose_name="Общая стоимость сотрудников",
    )
    total_contractor_cost = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        verbose_name="Общая стоимость исполнителей",
    )
    total_subcontractor_cost = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        verbose_name="Общая стоимость подрядчиков",
    )
    total_equipment_cost = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        verbose_name="Общая стоимость оборудования",
    )
    total_project_cost = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        verbose_name="Общая стоимость проекта",
    )
    calculated_at = models.DateTimeField(auto_now=True, verbose_name="Дата расчета")

    class Meta:
        verbose_name = "Расчет стоимости проекта"
        verbose_name_plural = "Расчеты стоимости проектов"

    def __str__(self):
        return f"Расчет для {self.project.name}"

    def calculate_total_cost(self):
        """Метод для пересчета общей стоимости проекта."""
        self.total_project_cost = (
            self.total_employee_cost +
            self.total_contractor_cost +
            self.total_subcontractor_cost +
            self.total_equipment_cost
        )
        self.save()
        return self.total_project_cost


# ============================================================================
# Переопределения полей ресурса для сводной таблицы
# ============================================================================

class ProjectResourceOverride(models.Model):
    """Параметры ресурса внутри конкретного проекта (маржа, услуга, сроки)."""
    RESOURCE_TYPE_CHOICES = [
        ('employee', 'Сотрудник'),
        ('contractor', 'Исполнитель'),
        ('subcontractor', 'Подрядчик'),
        ('equipment', 'Оборудование'),
    ]
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE,
        related_name='resource_overrides', verbose_name='Проект',
    )
    resource_type = models.CharField(
        'Тип ресурса', max_length=20, choices=RESOURCE_TYPE_CHOICES,
    )
    resource_id = models.PositiveIntegerField('ID ресурса')
    resource_margin = models.DecimalField(
        'Маржинальность ресурса (%)', max_digits=5, decimal_places=2,
        null=True, blank=True, default=None,
    )
    service = models.ForeignKey(
        Service, on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='Услуга',
    )
    work_start_date = models.DateField('Дата начала работы', null=True, blank=True)
    work_end_date = models.DateField('Дата окончания работы', null=True, blank=True)
    working_hours = models.PositiveIntegerField(
        'Количество рабочих часов', null=True, blank=True, default=None,
    )

    class Meta:
        verbose_name = 'Параметры ресурса (сводная таблица)'
        verbose_name_plural = 'Параметры ресурсов (сводная таблица)'
        unique_together = ['project', 'resource_type', 'resource_id']

    def __str__(self):
        return f"{self.get_resource_type_display()} #{self.resource_id} для {self.project.name}"


# ============================================================================
# Расписание сотрудников
# ============================================================================

class EmployeeSchedule(models.Model):
    """Расписание сотрудника — в какие дни работает, отдыхает или занят другим проектом."""
    STATUS_CHOICES = [
        ('work', 'Работает'),
        ('dayoff', 'Выходной / Отпуск'),
        ('other_project', 'Занят другим проектом'),
    ]

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE,
        related_name='schedule_entries', verbose_name='Сотрудник',
    )
    start_date = models.DateField(verbose_name='Дата начала')
    end_date = models.DateField(verbose_name='Дата окончания')
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='work',
        verbose_name='Статус',
    )
    note = models.CharField(max_length=200, blank=True, verbose_name='Примечание')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Расписание сотрудника'
        verbose_name_plural = 'Расписания сотрудников'
        ordering = ['start_date']

    def __str__(self):
        return f"{self.employee.name}: {self.get_status_display()} ({self.start_date} — {self.end_date})"