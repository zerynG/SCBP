from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from decimal import Decimal

class Project(models.Model):
    """Модель проекта"""
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
    # tax_rate = страховые взносы на ФОТ (%). Начисляются ТОЛЬКО на оклад
    # штатных сотрудников и входят в себестоимость. По умолчанию 0 —
    # начисляются, только если задано (настраивается на проекте).
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), verbose_name="Страховые взносы на ФОТ (%)")
    # НДС (%) на отпускную цену (output). Начисляется ОДИН раз на итоговую цену
    # (после наценки), не входит в прибыль. По умолчанию 0 (напр. УСН без НДС).
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), verbose_name="НДС (%)")
    # margin = наценка/рентабельность (%): надбавка к себестоимости ресурса.
    margin = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), verbose_name="Наценка / рентабельность (%)")
    # --- Параметры расчёта (настраиваемые) ---
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

class Employee(models.Model):
    """Модель сотрудника с месячной зарплатой"""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='employees', verbose_name="Проект")
    name = models.CharField(max_length=100, verbose_name="ФИО")
    position = models.CharField(max_length=100, verbose_name="Должность")
    monthly_salary = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Месячная зарплата"
    )
    start_date = models.DateField(null=True, blank=True, verbose_name="Дата начала работы")
    end_date = models.DateField(null=True, blank=True, verbose_name="Дата окончания работы")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    
    class Meta:
        verbose_name = "Сотрудник"
        verbose_name_plural = "Сотрудники"
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} - {self.position}"

class Contractor(models.Model):
    """Модель исполнителя (ГПХ/НПД)"""
    CONTRACT_TYPE_CHOICES = [
        ('gph', 'ГПХ (Гражданско-правовой договор)'),
        ('npd', 'НПД (Налог на профессиональный доход)'),
    ]
    
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='contractors', verbose_name="Проект")
    name = models.CharField(max_length=100, verbose_name="ФИО")
    contract_type = models.CharField(max_length=3, choices=CONTRACT_TYPE_CHOICES, verbose_name="Тип договора")
    hourly_rate = models.DecimalField(
        max_digits=8, 
        decimal_places=2, 
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Ставка за час"
    )
    estimated_hours = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Планируемые часы"
    )
    actual_hours = models.PositiveIntegerField(
        default=0, 
        verbose_name="Фактические часы"
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    
    class Meta:
        verbose_name = "Исполнитель"
        verbose_name_plural = "Исполнители"
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} ({self.get_contract_type_display()})"
    
    @property
    def estimated_cost(self):
        if self.hourly_rate and self.estimated_hours:
            return self.hourly_rate * self.estimated_hours
        return Decimal('0')
    
    @property
    def actual_cost(self):
        if self.hourly_rate:
            return self.hourly_rate * self.actual_hours
        return Decimal('0')

class Subcontractor(models.Model):
    """Модель подрядчика"""
    ENTITY_TYPE_CHOICES = [
        ('legal', 'Юридическое лицо'),
        ('individual', 'Физическое лицо'),
    ]
    
    PRICING_TYPE_CHOICES = [
        ('fixed', 'Фиксированная стоимость'),
        ('hourly', 'Почасовая оплата'),
    ]
    
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='subcontractors', verbose_name="Проект")
    name = models.CharField(max_length=200, verbose_name="Название/ФИО")
    entity_type = models.CharField(max_length=10, choices=ENTITY_TYPE_CHOICES, verbose_name="Тип лица")
    inn = models.CharField(max_length=12, blank=True, verbose_name="ИНН")
    contact_person = models.CharField(max_length=100, blank=True, verbose_name="Контактное лицо")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Телефон")
    email = models.EmailField(blank=True, verbose_name="Email")
    pricing_type = models.CharField(
        max_length=10, 
        choices=PRICING_TYPE_CHOICES, 
        default='fixed',
        verbose_name="Тип оплаты"
    )
    contract_amount = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Сумма договора"
    )
    hourly_rate = models.DecimalField(
        max_digits=8, 
        decimal_places=2, 
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Ставка за час"
    )
    estimated_hours = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Планируемые часы"
    )
    actual_hours = models.PositiveIntegerField(default=0, verbose_name="Фактические часы")
    work_description = models.TextField(verbose_name="Описание работ")
    contract_date = models.DateField(verbose_name="Дата договора")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    
    class Meta:
        verbose_name = "Подрядчик"
        verbose_name_plural = "Подрядчики"
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} ({self.get_entity_type_display()})"
    
    @property
    def estimated_cost(self):
        if self.pricing_type == 'hourly' and self.hourly_rate and self.estimated_hours:
            return self.hourly_rate * self.estimated_hours
        return self.contract_amount
    
    @property
    def actual_cost(self):
        if self.pricing_type == 'hourly' and self.hourly_rate:
            return self.hourly_rate * self.actual_hours
        return self.contract_amount

class Equipment(models.Model):
    """Модель оборудования"""
    PRICING_TYPE_CHOICES = [
        ('month', 'За месяц'),
        ('day', 'За день'),
        ('hour', 'За час'),
        ('minute', 'За минуту'),
    ]
    
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='equipment', verbose_name="Проект")
    name = models.CharField(max_length=200, verbose_name="Название оборудования")
    model = models.CharField(max_length=100, blank=True, verbose_name="Модель")
    manufacturer = models.CharField(max_length=100, blank=True, verbose_name="Производитель")
    cost = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Стоимость"
    )
    pricing_type = models.CharField(
        max_length=10, 
        choices=PRICING_TYPE_CHOICES, 
        default='month',
        verbose_name="Тип оплаты"
    )
    operational_cost = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0.00'),
        verbose_name="Операционная стоимость (ставка)"
    )
    monthly_cost = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        null=True, 
        blank=True,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Ежемесячная стоимость (для аренды/лизинга)"
    )
    quantity = models.PositiveIntegerField(default=1, verbose_name="Количество")
    purchase_date = models.DateField(
        null=True,  # <-- ИЗМЕНЕНО: добавлено null=True
        blank=True,  # <-- ИЗМЕНЕНО: добавлено blank=True
        verbose_name="Дата приобретения"
    )
    warranty_period = models.PositiveIntegerField(null=True, blank=True, verbose_name="Гарантийный период (месяцы)")
    is_active = models.BooleanField(default=True, verbose_name="Активно")
    
    class Meta:
        verbose_name = "Оборудование"
        verbose_name_plural = "Оборудование"
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} ({self.get_pricing_type_display()})"
    
    @property
    def total_cost(self):
        return self.cost * self.quantity
    
    @property
    def total_monthly_cost(self):
        if self.monthly_cost:
            return self.monthly_cost * self.quantity
        return None

class Service(models.Model):
    """Глобальный реестр услуг"""
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


class ProjectCost(models.Model):
    """Модель для хранения расчетов стоимости проекта"""
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='cost_calculation', verbose_name="Проект")
    total_employee_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Общая стоимость сотрудников")
    total_contractor_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Общая стоимость исполнителей")
    total_subcontractor_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Общая стоимость подрядчиков")
    total_equipment_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Общая стоимость оборудования")
    total_project_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Общая стоимость проекта")
    calculated_at = models.DateTimeField(auto_now=True, verbose_name="Дата расчета")
    
    class Meta:
        verbose_name = "Расчет стоимости проекта"
        verbose_name_plural = "Расчеты стоимости проектов"
    
    def __str__(self):
        return f"Расчет для {self.project.name}"
    
    def calculate_total_cost(self):
        """Метод для пересчета общей стоимости проекта"""
        self.total_project_cost = (
            self.total_employee_cost +
            self.total_contractor_cost +
            self.total_subcontractor_cost +
            self.total_equipment_cost
        )
        self.save()
        return self.total_project_cost

# --- Переопределения полей ресурса для сводной таблицы ---
class ProjectResourceOverride(models.Model):
    RESOURCE_TYPE_CHOICES = [
        ('employee', 'Сотрудник'),
        ('contractor', 'Исполнитель'),
        ('subcontractor', 'Подрядчик'),
        ('equipment', 'Оборудование'),
    ]
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='resource_overrides', verbose_name='Проект')
    resource_type = models.CharField('Тип ресурса', max_length=20, choices=RESOURCE_TYPE_CHOICES)
    resource_id = models.PositiveIntegerField('ID ресурса')
    # Вводимые поля
    resource_margin = models.DecimalField('Маржинальность ресурса (%)', max_digits=5, decimal_places=2, null=True, blank=True, default=None)
    service = models.ForeignKey(Service, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Услуга')
    work_start_date = models.DateField('Дата начала работы', null=True, blank=True)
    work_end_date = models.DateField('Дата окончания работы', null=True, blank=True)
    working_hours = models.PositiveIntegerField('Количество рабочих часов', null=True, blank=True, default=None)

    class Meta:
        verbose_name = 'Параметры ресурса (сводная таблица)'
        verbose_name_plural = 'Параметры ресурсов (сводная таблица)'
        unique_together = ['project', 'resource_type', 'resource_id']

    def __str__(self):
        return f"{self.get_resource_type_display()} #{self.resource_id} для {self.project.name}"


class EmployeeSchedule(models.Model):
    """Расписание сотрудника — в какие дни работает, отдыхает или занят другим проектом"""
    STATUS_CHOICES = [
        ('work', 'Работает'),
        ('dayoff', 'Выходной / Отпуск'),
        ('other_project', 'Занят другим проектом'),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='schedule_entries', verbose_name='Сотрудник')
    start_date = models.DateField(verbose_name='Дата начала')
    end_date = models.DateField(verbose_name='Дата окончания')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='work', verbose_name='Статус')
    note = models.CharField(max_length=200, blank=True, verbose_name='Примечание')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Расписание сотрудника'
        verbose_name_plural = 'Расписания сотрудников'
        ordering = ['start_date']

    def __str__(self):
        return f"{self.employee.name}: {self.get_status_display()} ({self.start_date} — {self.end_date})"