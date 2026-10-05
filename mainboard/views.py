import io
import json
import os
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from calendar import monthrange

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

# Импорты для генерации документов
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image as ReportLabImage
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
except ImportError:
    print("Предупреждение: reportlab не установлен. PDF генерация недоступна.")

try:
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as OpenpyxlImage
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
except ImportError:
    print("Предупреждение: openpyxl не установлен. Excel генерация недоступна.")

try:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import RGBColor, Pt
except ImportError:
    print("Предупреждение: python-docx не установлен. Word генерация недоступна.")

from .models import Contractor, Employee, EmployeeSchedule, Equipment, Project, ProjectCost, ProjectResourceOverride, Service, Subcontractor
from . import calculator as calc


# Генерация документов вынесена в отдельный модуль (КП/НМА, PDF/Excel/Word).
from .documents import DocumentGenerator


@login_required
def index(request):
    """Главная страница"""
    projects = Project.objects.filter(is_active=True).order_by('-created_at')[:5]
    return render(request, 'index.html', {'projects': projects})

@login_required
def profile(request):
    """Профиль пользователя"""
    # Последние активные проекты
    recent_projects = Project.objects.filter(is_active=True).order_by('-created_at')[:5]

    # Статистика по проектам и ресурсам
    stats = {
        'projects_total': Project.objects.count(),
        'projects_active': Project.objects.filter(is_active=True).count(),
        'employees_total': Employee.objects.count(),
        'contractors_total': Contractor.objects.count(),
        'subcontractors_total': Subcontractor.objects.count(),
        'equipment_total': Equipment.objects.count(),
    }

    return render(request, 'profile.html', {
        'user': request.user,
        'recent_projects': recent_projects,
        'stats': stats,
    })

def logout_view(request):
    """Выход из системы"""
    logout(request)
    messages.success(request, 'Вы успешно вышли из системы.')
    return redirect('login')

def login_view(request):
    """Вход в систему"""
    if request.user.is_authenticated:
        return redirect('index')
    
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        if email and password:
            user = authenticate(request, username=email, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f'Добро пожаловать, {user.first_name or user.username}!')
                return redirect('index')
            else:
                messages.error(request, 'Неверный email или пароль.')
        else:
            messages.error(request, 'Пожалуйста, заполните все поля.')
    
    # Передаем значение email обратно в шаблон при ошибке
    email_value = request.POST.get('email', '') if request.method == 'POST' else ''
    
    return render(request, 'login.html', {'email_value': email_value})

# Представления для работы с проектами

@login_required
def create_project(request):
    """Страница создания проекта"""
    if request.method == 'POST':
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        try:
            with transaction.atomic():
                name = request.POST.get('project_name') or request.POST.get('name')
                description = request.POST.get('project_description') or request.POST.get('description', '')
                if not name:
                    if is_ajax:
                        return JsonResponse({'success': False, 'error': 'Название проекта обязательно'})
                    messages.error(request, 'Пожалуйста, укажите название проекта.')
                    return render(request, 'create_project.html', {'services': Service.objects.filter(is_active=True)})
                
                # Создаем проект
                project = Project.objects.create(
                    name=name,
                    description=description
                )
                
                # Создаем запись для расчета стоимости
                ProjectCost.objects.create(project=project)
                
                # Обрабатываем данные ресурсов из localStorage (если переданы)
                resources_data = request.POST.get('resources_data')
                if resources_data:
                    try:
                        resources = json.loads(resources_data)
                        
                        # Создаем подрядчиков (contractors)
                        contractors_data = resources.get('contractors', [])
                        for contractor_data in contractors_data:
                            # Определяем тип подрядчика - если есть estimatedHours/actualHours, то это Contractor, иначе Subcontractor
                            if contractor_data.get('estimatedHours') is not None or contractor_data.get('actualHours') is not None:
                                # Создаем Contractor (исполнитель)
                                hourly_rate = None
                                estimated_hours = None
                                actual_hours = 0
                                
                                if contractor_data.get('pricingType') == 'hourly':
                                    hourly_cost = contractor_data.get('hourlyCost')
                                    if hourly_cost:
                                        hourly_rate = Decimal(str(hourly_cost).replace(',', '.'))
                                    
                                    est_hours = contractor_data.get('estimatedHours')
                                    if est_hours:
                                        estimated_hours = int(est_hours)
                                    
                                    act_hours = contractor_data.get('actualHours', 0)
                                    if act_hours:
                                        actual_hours = int(act_hours)
                                
                                Contractor.objects.create(
                                    project=project,
                                    name=contractor_data.get('name', ''),
                                    contract_type=contractor_data.get('entityType', 'individual'),
                                    hourly_rate=hourly_rate,
                                    estimated_hours=estimated_hours,
                                    actual_hours=actual_hours
                                )
                            else:
                                # Создаем Subcontractor (подрядчик)
                                pricing_type = contractor_data.get('pricingType', 'fixed')
                                contract_amount = Decimal('0')
                                hourly_rate = None
                                estimated_hours = None
                                actual_hours = 0
                                
                                if pricing_type == 'hourly':
                                    hourly_cost = contractor_data.get('hourlyCost')
                                    if hourly_cost:
                                        hourly_rate = Decimal(str(hourly_cost).replace(',', '.'))
                                    
                                    est_hours = contractor_data.get('estimatedHours')
                                    if est_hours:
                                        estimated_hours = int(est_hours)
                                        if hourly_rate:
                                            contract_amount = hourly_rate * estimated_hours
                                    
                                    act_hours = contractor_data.get('actualHours', 0)
                                    if act_hours:
                                        actual_hours = int(act_hours)
                                else:
                                    fixed_cost = contractor_data.get('fixedCost')
                                    if fixed_cost:
                                        contract_amount = Decimal(str(fixed_cost).replace(',', '.'))
                                
                                Subcontractor.objects.create(
                                    project=project,
                                    name=contractor_data.get('name', ''),
                                    entity_type=contractor_data.get('entityType', 'individual'),
                                    pricing_type=pricing_type,
                                    contract_amount=contract_amount,
                                    hourly_rate=hourly_rate,
                                    estimated_hours=estimated_hours,
                                    actual_hours=actual_hours,
                                    work_description='Создано из каталога'
                                )
                        
                        # Создаем сотрудников (employees)
                        employees_data = resources.get('employees', [])
                        for employee_data in employees_data:
                            monthly_salary = Decimal('0')
                            salary = employee_data.get('salary')
                            if salary:
                                monthly_salary = Decimal(str(salary).replace(',', '.'))
                            
                            Employee.objects.create(
                                project=project,
                                name=employee_data.get('name', ''),
                                position=employee_data.get('position', ''),
                                monthly_salary=monthly_salary
                            )
                        
                        # Создаем исполнителей (executors -> Contractor)
                        executors_data = resources.get('executors', [])
                        for executor_data in executors_data:
                            hourly_rate = None
                            estimated_hours = None
                            actual_hours = 0
                            
                            if executor_data.get('pricingType') == 'hourly':
                                hourly_cost = executor_data.get('hourlyCost')
                                if hourly_cost:
                                    hourly_rate = Decimal(str(hourly_cost).replace(',', '.'))
                            
                            Contractor.objects.create(
                                project=project,
                                name=executor_data.get('name', ''),
                                contract_type=executor_data.get('contractType', 'individual'),
                                hourly_rate=hourly_rate,
                                estimated_hours=estimated_hours,
                                actual_hours=actual_hours
                            )
                        
                        # Создаем оборудование (equipment)
                        equipment_data = resources.get('equipment', [])
                        for equip_data in equipment_data:
                            operational_cost = Decimal('0')
                            op_cost = equip_data.get('operationalCost')
                            if op_cost:
                                operational_cost = Decimal(str(op_cost).replace(',', '.'))
                            
                            cost = Decimal('0')
                            c = equip_data.get('cost')
                            if c:
                                cost = Decimal(str(c).replace(',', '.'))
                                
                            monthly_cost = None
                            mc = equip_data.get('monthlyCost')
                            if mc:
                                monthly_cost = Decimal(str(mc).replace(',', '.'))

                            Equipment.objects.create(
                                project=project,
                                name=equip_data.get('name', ''),
                                pricing_type=equip_data.get('pricingType', 'month'),
                                operational_cost=operational_cost,
                                cost=cost,
                                monthly_cost=monthly_cost,
                                quantity=int(equip_data.get('quantity', 1))
                            )
                            
                    except (json.JSONDecodeError, ValueError, TypeError) as e:
                        # Если ошибка в данных ресурсов, не прерываем создание проекта
                        pass
                
                messages.success(request, f'Проект "{project.name}" успешно создан!')
                
                if is_ajax:
                    return JsonResponse({
                        'success': True,
                        'project': {
                            'id': project.id,
                            'name': project.name,
                            'description': project.description,
                            'created_at': project.created_at.isoformat(),
                        }
                    })
                return redirect('project_manage', project_id=project.id)
                
        except Exception as e:
            if is_ajax:
                return JsonResponse({'success': False, 'error': str(e)})
            messages.error(request, f'Ошибка при создании проекта: {str(e)}')
    
    services = Service.objects.filter(is_active=True)
    return render(request, 'create_project.html', {'services': services})

@login_required
def project_list(request):
    """Список всех проектов"""
    projects = Project.objects.filter(is_active=True).order_by('-created_at')
    return render(request, 'project_list.html', {'projects': projects})

# ---------------------------------------------------------------------------
# Хелперы для project_manage / recalculate_project / документов
# ---------------------------------------------------------------------------

def _load_project_calendars():
    """Прочитать все JSON-календари в формате calculator.CalendarMap."""
    try:
        calendar_dir = os.path.join(settings.BASE_DIR, 'calendar')
        return calc.load_calendars(calendar_dir)
    except Exception:
        return {}


def _load_overrides_map(project):
    """Все переопределения проекта в виде словаря {(type, id): override}."""
    overrides_map = {}
    try:
        for o in ProjectResourceOverride.objects.filter(project=project):
            overrides_map[(o.resource_type, o.resource_id)] = o
    except Exception:
        pass
    return overrides_map


def _load_employee_schedule(project):
    """Словарь {employee_id: [entries]} только для dayoff/other_project."""
    schedule_by_employee = {}
    for entry in EmployeeSchedule.objects.filter(
        employee__project=project,
        employee__is_active=True,
        status__in=['dayoff', 'other_project'],
    ):
        schedule_by_employee.setdefault(entry.employee_id, []).append(entry)
    return schedule_by_employee


def _calculate_project(project):
    """Единая точка расчёта проекта: возвращает (rows_dicts, totals, calendars, schedule_for_js).

    rows_dicts — список словарей для шаблона / JSON-ответа.
    """
    employees_qs = list(project.employees.filter(is_active=True))
    contractors_qs = list(project.contractors.filter(is_active=True))
    subcontractors_qs = list(project.subcontractors.filter(is_active=True))
    equipment_qs = list(project.equipment.filter(is_active=True))

    overrides_map = _load_overrides_map(project)
    calendars = _load_project_calendars()
    schedule_by_employee = _load_employee_schedule(project)

    rows, totals = calc.calc_project(
        project=project,
        employees=employees_qs,
        contractors=contractors_qs,
        subcontractors=subcontractors_qs,
        equipment=equipment_qs,
        overrides_map=overrides_map,
        schedule_by_employee=schedule_by_employee,
        calendars=calendars,
    )

    # Сериализация для шаблона
    rows_dicts = [r.as_dict() for r in rows]

    # Расписание для JS
    schedule_for_js = {
        emp_id: [
            {'start': str(e.start_date), 'end': str(e.end_date), 'status': e.status}
            for e in entries
        ]
        for emp_id, entries in schedule_by_employee.items()
    }

    return {
        'rows': rows_dicts,
        'totals': totals,
        'calendars': calendars,
        'schedule_for_js': schedule_for_js,
        'employees_qs': employees_qs,
        'contractors_qs': contractors_qs,
        'subcontractors_qs': subcontractors_qs,
        'equipment_qs': equipment_qs,
    }


def _persist_project_cost(project, totals):
    """Сохранить итоги в ProjectCost (себестоимость с налогом, без маржи)."""
    cost_calculation, _ = ProjectCost.objects.get_or_create(project=project)
    cost_calculation.total_employee_cost = totals.total_employee_cost
    cost_calculation.total_contractor_cost = totals.total_contractor_cost
    cost_calculation.total_subcontractor_cost = totals.total_subcontractor_cost
    cost_calculation.total_equipment_cost = totals.total_equipment_cost
    cost_calculation.calculate_total_cost()
    return cost_calculation


@login_required
def project_manage(request, project_id):
    """Страница управления проектом"""
    project = get_object_or_404(Project, id=project_id, is_active=True)

    if request.method == 'POST' and request.POST.get('form_type') == 'update_project':
        project.name = request.POST.get('name', project.name)
        project.description = request.POST.get('description', project.description)
        project.technical_task_url = request.POST.get('technical_task_url', project.technical_task_url)
        project.status = request.POST.get('status', project.status)
        project.customer = request.POST.get('customer', project.customer)
        project.start_date = request.POST.get('start_date') or project.start_date
        project.end_date = request.POST.get('end_date') or project.end_date
        tax_rate = request.POST.get('tax_rate')
        margin = request.POST.get('margin')
        vat_rate = request.POST.get('vat_rate')
        if tax_rate is not None:
            try:
                tax_rate_clean = str(tax_rate).replace(',', '.').strip()
                if tax_rate_clean != '':
                    val = Decimal(tax_rate_clean)
                    if val < Decimal('0') or val > Decimal('999.99'):
                        messages.error(request, 'Страховые взносы должны быть от 0 до 999.99')
                    else:
                        project.tax_rate = val
            except Exception:
                messages.error(request, 'Некорректное значение страховых взносов')
        if vat_rate is not None:
            try:
                vat_rate_clean = str(vat_rate).replace(',', '.').strip()
                if vat_rate_clean != '':
                    val = Decimal(vat_rate_clean)
                    if val < Decimal('0') or val > Decimal('999.99'):
                        messages.error(request, 'НДС должен быть от 0 до 999.99')
                    else:
                        project.vat_rate = val
            except Exception:
                messages.error(request, 'Некорректное значение НДС')
        if margin is not None:
            try:
                margin_clean = str(margin).replace(',', '.').strip()
                if margin_clean != '':
                    val = Decimal(margin_clean)
                    if val < Decimal('0') or val > Decimal('999.99'):
                        messages.error(request, 'Наценка должна быть от 0 до 999.99')
                    else:
                        project.margin = val
            except Exception:
                messages.error(request, 'Некорректное значение наценки')

        # Настройки расчёта: КЧЧ, база часов оборудования, fallback рабочих дней
        wh = request.POST.get('work_hours_per_day')
        if wh is not None:
            try:
                wh_clean = str(wh).replace(',', '.').strip()
                if wh_clean != '':
                    val = Decimal(wh_clean)
                    if val <= Decimal('0') or val > Decimal('24'):
                        messages.error(request, 'КЧЧ должно быть от 0.01 до 24')
                    else:
                        project.work_hours_per_day = val
            except Exception:
                messages.error(request, 'Некорректное значение КЧЧ')
        ehb = request.POST.get('equipment_hour_basis')
        if ehb in ('calendar', 'working'):
            project.equipment_hour_basis = ehb
        wdf = request.POST.get('working_days_per_month_fallback')
        if wdf is not None:
            try:
                wdf_clean = str(wdf).strip()
                if wdf_clean != '':
                    n = int(wdf_clean)
                    if 1 <= n <= 31:
                        project.working_days_per_month_fallback = n
            except Exception:
                pass

        project.save()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        messages.success(request, 'Данные проекта обновлены')
        return redirect('project_manage', project_id=project.id)

    result = _calculate_project(project)
    totals = result['totals']
    _persist_project_cost(project, totals)

    def decimal_default(obj):
        if isinstance(obj, Decimal):
            return str(obj)
        raise TypeError

    catalog_db = {
        'employees': list(Employee.objects.values('name', 'position', 'monthly_salary').distinct()),
        'contractors': list(Contractor.objects.values('name', 'contract_type', 'hourly_rate', 'estimated_hours').distinct()),
        'subcontractors': list(Subcontractor.objects.values('name', 'entity_type', 'pricing_type', 'contract_amount', 'hourly_rate', 'estimated_hours').distinct()),
        'equipment': list(Equipment.objects.values('name', 'model', 'cost', 'monthly_cost', 'pricing_type', 'operational_cost').distinct()),
    }
    services = Service.objects.filter(is_active=True)

    # Подготовим словари для json_script (Django сам безопасно сериализует Decimal в строки)
    # catalog_db содержит Decimal — конвертируем через json.loads(json.dumps(..., default=str))
    catalog_db_safe = json.loads(json.dumps(catalog_db, default=decimal_default, ensure_ascii=False))

    context = {
        'project': project,
        'employees': result['employees_qs'],
        'contractors': result['contractors_qs'],
        'subcontractors': result['subcontractors_qs'],
        'equipment': result['equipment_qs'],
        'resources': result['rows'],
        'services': services,
        'base_cost': totals.base_cost,
        'final_price': totals.final_price,
        'net_profit': totals.net_profit,
        'vat_rate': totals.vat_rate,
        'vat_amount': totals.vat_amount,
        'price_with_vat': totals.price_with_vat,
        # dict-объекты для тега {{ ...|json_script:'...' }} в шаблоне
        'calendars_json': result['calendars'],
        'catalog_db_json': catalog_db_safe,
        'employee_schedule_json': result['schedule_for_js'],
    }
    return render(request, 'project_manage.html', context)


@login_required
@require_http_methods(["POST", "GET"])
def recalculate_project(request, project_id):
    """API: пересчитать проект и вернуть JSON с строками и итогами.

    POST/GET одинаково возвращают актуальный снимок. Используется фронтом после
    debounced save в качестве "источника правды" по числам.
    """
    project = get_object_or_404(Project, id=project_id, is_active=True)
    result = _calculate_project(project)
    totals = result['totals']
    _persist_project_cost(project, totals)
    return JsonResponse({
        'success': True,
        'totals': calc.serialize_totals(totals),
        'rows': [calc.serialize_row(_row_obj_for_serialization(r)) for r in result['rows']],
    })


def _row_obj_for_serialization(row_dict):
    """Конвертирует dict в ResourceCost для serialize_row."""
    r = calc.ResourceCost(
        resource_type=row_dict.get('resource_type'),
        resource_id=row_dict.get('resource_id'),
        name=row_dict.get('name') or '',
        type_display=row_dict.get('type') or '',
        service=row_dict.get('service') or '',
        service_id=row_dict.get('service_id') or None,
        margin_percent=row_dict.get('margin_percent') or Decimal('0'),
        start_date=row_dict.get('start_date'),
        end_date=row_dict.get('end_date'),
        work_days=row_dict.get('work_days'),
        work_hours=row_dict.get('work_hours'),
        monthly_cost=row_dict.get('monthly_cost'),
        base_no_tax=row_dict.get('base_no_tax') or Decimal('0'),
        cost=row_dict.get('cost') or Decimal('0'),
        final_cost=row_dict.get('final_cost') or Decimal('0'),
        pricing_type=row_dict.get('pricing_type'),
        months_count=row_dict.get('months_count'),
    )
    return r

# Представления для работы с сотрудниками

def _save_service_override(project, resource_type, resource_id, service_id):
    """Сохраняет услугу в ProjectResourceOverride при добавлении ресурса"""
    if service_id:
        try:
            service = Service.objects.get(id=int(service_id))
        except (Service.DoesNotExist, ValueError, TypeError):
            return
        override, _ = ProjectResourceOverride.objects.get_or_create(
            project=project,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        override.service = service
        override.save()

def add_employee(request, project_id):
    """Добавление сотрудника к проекту"""
    project = get_object_or_404(Project, id=project_id, is_active=True)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    try:
        employee = Employee.objects.create(
            project=project,
            name=request.POST.get('name'),
            position=request.POST.get('position'),
            monthly_salary=request.POST.get('monthly_salary'),
            start_date=request.POST.get('start_date') or None,
            end_date=request.POST.get('end_date') or None
        )
        _save_service_override(project, 'employee', employee.id, request.POST.get('service'))
        if is_ajax:
            return JsonResponse({
                'success': True,
                'employee': {
                    'id': employee.id,
                    'name': employee.name,
                    'position': employee.position,
                    'monthly_salary': str(employee.monthly_salary),
                    'start_date': employee.start_date.isoformat() if employee.start_date else None,
                    'end_date': employee.end_date.isoformat() if employee.end_date else None,
                }
            })
        messages.success(request, f'Сотрудник "{employee.name}" добавлен к проекту.')
    except Exception as e:
        if is_ajax:
            return JsonResponse({'success': False, 'error': str(e)})
        messages.error(request, f'Ошибка при добавлении сотрудника: {str(e)}')
    return redirect('project_manage', project_id=project_id)

@login_required
@require_http_methods(["POST"])
def delete_employee(request, project_id, employee_id):
    """Удаление сотрудника из проекта"""
    employee = get_object_or_404(Employee, id=employee_id, project_id=project_id)
    employee.is_active = False
    employee.save()
    messages.success(request, f'Сотрудник "{employee.name}" удален из проекта.')
    return redirect('project_manage', project_id=project_id)

# Представления для работы с исполнителями

@login_required
@require_http_methods(["POST"])
def add_contractor(request, project_id):
    """Добавление исполнителя к проекту"""
    project = get_object_or_404(Project, id=project_id, is_active=True)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    try:
        # Валидация и парсинг числовых значений
        hr_raw = request.POST.get('hourly_rate')
        eh_raw = request.POST.get('estimated_hours')
        ah_raw = request.POST.get('actual_hours', 0)
        if hr_raw is None or str(hr_raw).strip() == '':
            raise ValueError('Часовая ставка обязательна и должна быть > 0.')
        try:
            hourly_rate = Decimal(str(hr_raw).replace(',', '.'))
        except Exception:
            raise ValueError('Часовая ставка должна быть десятичным числом.')
        if hourly_rate <= Decimal('0'):
            raise ValueError('Часовая ставка должна быть больше нуля.')
        try:
            estimated_hours = int(eh_raw or 0)
        except Exception:
            raise ValueError('Планируемые часы должны быть целым числом.')
        try:
            actual_hours = int(ah_raw or 0)
        except Exception:
            raise ValueError('Фактические часы должны быть целым числом.')

        contractor = Contractor.objects.create(
            project=project,
            name=request.POST.get('name'),
            contract_type=request.POST.get('contract_type'),
            hourly_rate=hourly_rate,
            estimated_hours=estimated_hours,
            actual_hours=actual_hours
        )
        _save_service_override(project, 'contractor', contractor.id, request.POST.get('service'))
        if is_ajax:
            return JsonResponse({
                'success': True,
                'contractor': {
                    'id': contractor.id,
                    'name': contractor.name,
                    'contract_type': contractor.contract_type,
                    'hourly_rate': str(contractor.hourly_rate),
                    'estimated_hours': contractor.estimated_hours,
                    'actual_hours': contractor.actual_hours,
                }
            })
        messages.success(request, f'Исполнитель "{contractor.name}" добавлен к проекту.')
    except Exception as e:
        if is_ajax:
            return JsonResponse({'success': False, 'error': str(e)})
        messages.error(request, f'Ошибка при добавлении исполнителя: {str(e)}')
    return redirect('project_manage', project_id=project_id)

@login_required
@require_http_methods(["POST"])
def delete_contractor(request, project_id, contractor_id):
    """Удаление исполнителя из проекта"""
    contractor = get_object_or_404(Contractor, id=contractor_id, project_id=project_id)
    contractor.is_active = False
    contractor.save()
    messages.success(request, f'Исполнитель "{contractor.name}" удален из проекта.')
    return redirect('project_manage', project_id=project_id)

# Представления для работы с подрядчиками

@login_required
@require_http_methods(["POST"])
def add_subcontractor(request, project_id):
    """Добавление подрядчика к проекту"""
    project = get_object_or_404(Project, id=project_id, is_active=True)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    try:
        # Обработка полей для почасовой оплаты
        pricing_type = request.POST.get('pricing_type', 'fixed')
        hourly_rate = None
        estimated_hours = None
        actual_hours = 0
        
        if pricing_type == 'hourly':
            hr_raw = request.POST.get('hourly_rate')
            eh_raw = request.POST.get('estimated_hours')
            ah_raw = request.POST.get('actual_hours', 0)
            
            if hr_raw:
                try:
                    hourly_rate = Decimal(str(hr_raw).replace(',', '.'))
                except Exception:
                    raise ValueError('Часовая ставка должна быть десятичным числом.')
                if hourly_rate <= Decimal('0'):
                    raise ValueError('Часовая ставка должна быть больше нуля.')
            
            if eh_raw:
                try:
                    estimated_hours = int(eh_raw)
                except Exception:
                    raise ValueError('Планируемые часы должны быть целым числом.')
            
            try:
                actual_hours = int(ah_raw or 0)
            except Exception:
                raise ValueError('Фактические часы должны быть целым числом.')
        
        subcontractor = Subcontractor.objects.create(
            project=project,
            name=request.POST.get('name'),
            entity_type=request.POST.get('entity_type'),
            inn=request.POST.get('inn', ''),
            contact_person=request.POST.get('contact_person', ''),
            phone=request.POST.get('phone', ''),
            email=request.POST.get('email', ''),
            pricing_type=pricing_type,
            contract_amount=request.POST.get('contract_amount'),
            hourly_rate=hourly_rate,
            estimated_hours=estimated_hours,
            actual_hours=actual_hours,
            work_description=request.POST.get('work_description'),
            contract_date=request.POST.get('contract_date')
        )
        _save_service_override(project, 'subcontractor', subcontractor.id, request.POST.get('service'))
        if is_ajax:
            return JsonResponse({
                'success': True,
                'subcontractor': {
                    'id': subcontractor.id,
                    'name': subcontractor.name,
                    'entity_type': subcontractor.entity_type,
                    'inn': subcontractor.inn,
                    'contact_person': subcontractor.contact_person,
                    'phone': subcontractor.phone,
                    'email': subcontractor.email,
                    'pricing_type': subcontractor.pricing_type,
                    'contract_amount': str(subcontractor.contract_amount),
                    'hourly_rate': str(subcontractor.hourly_rate) if subcontractor.hourly_rate else None,
                    'estimated_hours': subcontractor.estimated_hours,
                    'actual_hours': subcontractor.actual_hours,
                    'work_description': subcontractor.work_description,
                    'contract_date': subcontractor.contract_date.isoformat() if subcontractor.contract_date else None,
                }
            })
        messages.success(request, f'Подрядчик "{subcontractor.name}" добавлен к проекту.')
    except Exception as e:
        if is_ajax:
            return JsonResponse({'success': False, 'error': str(e)})
        messages.error(request, f'Ошибка при добавлении подрядчика: {str(e)}')
    return redirect('project_manage', project_id=project_id)

@login_required
@require_http_methods(["POST"])
def delete_subcontractor(request, project_id, subcontractor_id):
    """Удаление подрядчика из проекта"""
    subcontractor = get_object_or_404(Subcontractor, id=subcontractor_id, project_id=project_id)
    subcontractor.is_active = False
    subcontractor.save()
    messages.success(request, f'Подрядчик "{subcontractor.name}" удален из проекта.')
    return redirect('project_manage', project_id=project_id)

# Представления для работы с оборудованием

@login_required
@require_http_methods(["POST"])
def add_equipment(request, project_id):
    """Добавление оборудования к проекту"""
    project = get_object_or_404(Project, id=project_id, is_active=True)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    try:
        # Валидация и парсинг числовых значений
        cost_raw = request.POST.get('cost')
        monthly_raw = request.POST.get('monthly_cost')
        qty_raw = request.POST.get('quantity', 1)
        if cost_raw is None or str(cost_raw).strip() == '':
            raise ValueError('Стоимость оборудования обязательна и должна быть > 0.')
        try:
            cost = Decimal(str(cost_raw).replace(',', '.'))
        except Exception:
            raise ValueError('Стоимость должна быть десятичным числом.')
        if cost <= Decimal('0'):
            raise ValueError('Стоимость должна быть больше нуля.')
        monthly_cost = None
        if monthly_raw is not None and str(monthly_raw).strip() != '':
            try:
                monthly_cost = Decimal(str(monthly_raw).replace(',', '.'))
                if monthly_cost <= Decimal('0'):
                    raise ValueError('Ежемесячная стоимость должна быть больше нуля.')
            except Exception:
                raise ValueError('Ежемесячная стоимость должна быть десятичным числом.')
        
        operational_cost = Decimal('0.00')
        op_cost_raw = request.POST.get('operational_cost')
        if op_cost_raw is not None and str(op_cost_raw).strip() != '':
             try:
                operational_cost = Decimal(str(op_cost_raw).replace(',', '.'))
             except Exception:
                pass # Fallback to monthly_cost or 0
        
        # Если operational_cost не передан, но есть monthly_cost - используем его
        if operational_cost == Decimal('0.00') and monthly_cost:
            operational_cost = monthly_cost
            
        # Если operational_cost 0, и есть просто стоимость (cost) - берем ее как ставку
        if operational_cost == Decimal('0.00') and cost > Decimal('0.00'):
             operational_cost = cost
        
        try:
            quantity = int(qty_raw or 1)
            if quantity <= 0:
                raise ValueError('Количество должно быть положительным целым числом.')
        except Exception:
            raise ValueError('Количество должно быть целым числом.')

        equipment = Equipment.objects.create(
            project=project,
            name=request.POST.get('name'),
            model=request.POST.get('model', ''),
            manufacturer=request.POST.get('manufacturer', ''),
            cost=cost,
            monthly_cost=monthly_cost,
            operational_cost=operational_cost,
            pricing_type=request.POST.get('pricing_type', 'month'),
            quantity=quantity,
            purchase_date=request.POST.get('purchase_date'),
            warranty_period=request.POST.get('warranty_period') or None
        )
        _save_service_override(project, 'equipment', equipment.id, request.POST.get('service'))
        if is_ajax:
            return JsonResponse({
                'success': True,
                'equipment': {
                    'id': equipment.id,
                    'name': equipment.name,
                    'model': equipment.model,
                    'manufacturer': equipment.manufacturer,
                    'cost': str(equipment.cost),
                    'monthly_cost': str(equipment.monthly_cost) if equipment.monthly_cost is not None else None,
                    'quantity': equipment.quantity,
                    'purchase_date': equipment.purchase_date.isoformat() if equipment.purchase_date else None,
                    'warranty_period': equipment.warranty_period,
                }
            })
        messages.success(request, f'Оборудование "{equipment.name}" добавлено к проекту.')
    except Exception as e:
        if is_ajax:
            return JsonResponse({'success': False, 'error': str(e)})
        messages.error(request, f'Ошибка при добавлении оборудования: {str(e)}')
    return redirect('project_manage', project_id=project_id)

@login_required
@require_http_methods(["POST"])
def delete_equipment(request, project_id, equipment_id):
    """Удаление оборудования из проекта"""
    equipment = get_object_or_404(Equipment, id=equipment_id, project_id=project_id)
    equipment.is_active = False
    equipment.save()
    messages.success(request, f'Оборудование "{equipment.name}" удалено из проекта.')
    return redirect('project_manage', project_id=project_id)

# Представления для генерации документов

@login_required
def generate_documents(request, project_id):
    """Страница генерации документов по проекту.

    Использует единый калькулятор — числа идентичны странице project_manage.
    """
    project = get_object_or_404(Project, id=project_id, is_active=True)
    result = _calculate_project(project)
    totals = result['totals']
    cost_calculation = _persist_project_cost(project, totals)

    context = {
        'project': project,
        'employees': result['employees_qs'],
        'contractors': result['contractors_qs'],
        'subcontractors': result['subcontractors_qs'],
        'equipment': result['equipment_qs'],
        'cost_calculation': cost_calculation,
        'final_price': totals.final_price,
        'net_profit': totals.net_profit,
        'base_cost': totals.base_cost,
    }
    return render(request, 'generate_documents.html', context)


def custom_404(request, exception):
    """Обработчик 404 ошибки"""
    return render(request, '404.html', status=404)


def custom_500(request):
    """Обработчик 500 ошибки"""
    return render(request, '500.html', status=500)


@login_required
def projects_api(request):
    """JSON-список всех активных проектов"""
    projects = Project.objects.filter(is_active=True).order_by('-created_at')
    data = [
        {
            'id': p.id,
            'name': p.name,
            'description': p.description,
            'created_at': p.created_at.isoformat(),
        }
        for p in projects
    ]
    return JsonResponse(data, safe=False)

@login_required
@require_http_methods(["POST"])
def delete_project(request, project_id):
    """Удаление проекта (мягкое удаление)"""
    project = get_object_or_404(Project, id=project_id, is_active=True)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    try:
        project.is_active = False
        project.save()
        messages.success(request, f'Проект "{project.name}" удален.')
        if is_ajax:
            return JsonResponse({'success': True})
        return redirect('project_list')
    except Exception as e:
        if is_ajax:
            return JsonResponse({'success': False, 'error': str(e)})
        messages.error(request, f'Ошибка при удалении проекта: {str(e)}')
        return redirect('project_list')

@login_required
@require_http_methods(["POST"])
def update_resource_override(request, project_id):
    """Сохранение параметров ресурса из сводной таблицы"""
    project = get_object_or_404(Project, id=project_id, is_active=True)
    resource_type = request.POST.get('resource_type')
    resource_id = request.POST.get('resource_id')
    if not resource_type or not resource_id:
        messages.error(request, 'Не указан ресурс')
        return redirect('project_manage', project_id=project.id)
    try:
        resource_id = int(resource_id)
    except Exception:
        messages.error(request, 'Некорректный идентификатор ресурса')
        return redirect('project_manage', project_id=project.id)
    # Парсим поля
    resource_margin = request.POST.get('resource_margin')
    service_id = request.POST.get('service')
    working_hours = request.POST.get('working_hours')
    work_start_date_str = request.POST.get('work_start_date')
    work_end_date_str = request.POST.get('work_end_date')
    work_start_date = None
    work_end_date = None
    try:
        if work_start_date_str:
            work_start_date = date.fromisoformat(work_start_date_str)
    except Exception:
        messages.error(request, 'Некорректная дата начала работ')
    try:
        if work_end_date_str:
            work_end_date = date.fromisoformat(work_end_date_str)
    except Exception:
        messages.error(request, 'Некорректная дата окончания работ')
    try:
        override, _ = ProjectResourceOverride.objects.get_or_create(
            project=project,
            resource_type=resource_type,
            resource_id=resource_id
        )
        # Маржа ресурса: '' → None (взять маржу проекта), '0' → 0% явно, число → процент
        if resource_margin is not None:
            if resource_margin == '':
                override.resource_margin = None
            else:
                try:
                    override.resource_margin = Decimal(str(resource_margin).replace(',', '.').strip())
                except Exception:
                    pass
        # Услуга
        if service_id is not None and service_id != '':
            try:
                override.service = Service.objects.get(id=int(service_id))
            except (Service.DoesNotExist, ValueError, TypeError):
                override.service = None
        elif service_id == '':
            override.service = None
        # Часы: '' → None, '0' → 0 (явно), число → часы
        if working_hours is not None:
            if working_hours == '':
                override.working_hours = None
            else:
                try:
                    wh_int = int(working_hours)
                    override.working_hours = max(0, wh_int)
                    if resource_type == 'subcontractor':
                        try:
                            subcontractor = Subcontractor.objects.get(id=resource_id, project=project)
                            if subcontractor.pricing_type == 'hourly':
                                subcontractor.actual_hours = max(0, wh_int)
                                subcontractor.save()
                        except Subcontractor.DoesNotExist:
                            pass
                    elif resource_type == 'contractor':
                        try:
                            contractor = Contractor.objects.get(id=resource_id, project=project)
                            contractor.actual_hours = max(0, wh_int)
                            contractor.save()
                        except Contractor.DoesNotExist:
                            pass
                except (ValueError, TypeError):
                    pass
                    
        override.work_start_date = work_start_date
        override.work_end_date = work_end_date
        override.save()
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if is_ajax:
            # Возвращаем пересчитанные итоги для синхронизации фронта с сервером
            result = _calculate_project(project)
            totals = result['totals']
            _persist_project_cost(project, totals)
            return JsonResponse({
                'success': True,
                'totals': calc.serialize_totals(totals),
                'rows': [calc.serialize_row(_row_obj_for_serialization(r)) for r in result['rows']],
            })
        messages.success(request, 'Параметры ресурса сохранены')
    except Exception as e:
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if is_ajax:
            return JsonResponse({'success': False, 'error': str(e)})
        messages.error(request, f'Ошибка сохранения параметров ресурса: {e}')
    return redirect('project_manage', project_id=project.id)


# === Реестр услуг ===

def _services_with_usage():
    """Услуги с количеством привязок (ресурсов и проектов).

    `projectresourceoverride` — обратная связь от ProjectResourceOverride.service
    (FK без related_name). usage_projects — в скольких разных проектах используется.
    """
    return Service.objects.annotate(
        usage_resources=Count('projectresourceoverride', distinct=True),
        usage_projects=Count('projectresourceoverride__project', distinct=True),
    ).order_by('applies_to', 'name')


def _valid_applies_to(value):
    """Нормализовать applies_to: только из допустимых значений, иначе 'any'."""
    valid = {c[0] for c in Service.APPLIES_TO_CHOICES}
    return value if value in valid else 'any'


@login_required
def services_page(request):
    """Отдельная страница управления реестром услуг (по образцу страницы Ганта)."""
    services = list(_services_with_usage())
    services_json = json.dumps([
        {
            'id': s.id,
            'name': s.name,
            'description': s.description,
            'applies_to': s.applies_to,
            'applies_to_display': s.get_applies_to_display(),
            'is_active': s.is_active,
            'usage_resources': s.usage_resources,
            'usage_projects': s.usage_projects,
        }
        for s in services
    ], ensure_ascii=False)
    return render(request, 'services.html', {
        'services': services,
        'services_json': services_json,
        'applies_to_choices': Service.APPLIES_TO_CHOICES,
    })


@login_required
@require_http_methods(["POST"])
def add_service(request):
    """Добавление услуги в глобальный реестр"""
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    name = request.POST.get('name', '').strip()
    description = request.POST.get('description', '').strip()
    if not name:
        if is_ajax:
            return JsonResponse({'success': False, 'error': 'Название услуги обязательно'})
        messages.error(request, 'Название услуги обязательно')
        return redirect('services_page')
    if Service.objects.filter(name__iexact=name).exists():
        if is_ajax:
            return JsonResponse({'success': False, 'error': 'Услуга с таким названием уже существует'})
        messages.error(request, 'Услуга с таким названием уже существует')
        return redirect('services_page')
    applies_to = _valid_applies_to(request.POST.get('applies_to', 'any'))
    service = Service.objects.create(name=name, description=description, applies_to=applies_to)
    if is_ajax:
        return JsonResponse({'success': True, 'service': {
            'id': service.id, 'name': service.name, 'description': service.description,
            'applies_to': service.applies_to, 'applies_to_display': service.get_applies_to_display(),
            'is_active': service.is_active, 'usage_resources': 0, 'usage_projects': 0,
        }})
    messages.success(request, f'Услуга "{service.name}" добавлена')
    return redirect('services_page')


@login_required
@require_http_methods(["POST"])
def edit_service(request, service_id):
    """Редактирование услуги: название, описание, флаг активности."""
    service = get_object_or_404(Service, id=service_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    # Переключение активности (быстрый тумблер) — отдельный режим
    if request.POST.get('action') == 'toggle_active':
        service.is_active = not service.is_active
        service.save(update_fields=['is_active'])
        if is_ajax:
            return JsonResponse({'success': True, 'is_active': service.is_active})
        return redirect('services_page')

    name = request.POST.get('name', '').strip()
    description = request.POST.get('description', '').strip()
    if not name:
        if is_ajax:
            return JsonResponse({'success': False, 'error': 'Название услуги обязательно'})
        messages.error(request, 'Название услуги обязательно')
        return redirect('services_page')
    if Service.objects.filter(name__iexact=name).exclude(id=service.id).exists():
        if is_ajax:
            return JsonResponse({'success': False, 'error': 'Услуга с таким названием уже существует'})
        messages.error(request, 'Услуга с таким названием уже существует')
        return redirect('services_page')

    service.name = name
    service.description = description
    if 'applies_to' in request.POST:
        service.applies_to = _valid_applies_to(request.POST.get('applies_to'))
    if 'is_active' in request.POST:
        service.is_active = request.POST.get('is_active') in ('1', 'true', 'on', 'True')
    service.save()
    if is_ajax:
        return JsonResponse({'success': True, 'service': {
            'id': service.id, 'name': service.name,
            'description': service.description,
            'applies_to': service.applies_to, 'applies_to_display': service.get_applies_to_display(),
            'is_active': service.is_active,
        }})
    messages.success(request, f'Услуга "{service.name}" обновлена')
    return redirect('services_page')


@login_required
@require_http_methods(["POST"])
def delete_service(request, service_id):
    """Удаление услуги из глобального реестра.

    Связь с ресурсами (ProjectResourceOverride.service) обнуляется (SET_NULL).
    Фронтенд предупреждает, если услуга где-то используется.
    """
    service = get_object_or_404(Service, id=service_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    service_name = service.name
    service.delete()
    if is_ajax:
        return JsonResponse({'success': True})
    messages.success(request, f'Услуга "{service_name}" удалена')
    return redirect('services_page')


@login_required
def services_api(request):
    """API для получения списка активных услуг (для выпадающих списков)."""
    services = list(Service.objects.filter(is_active=True).values('id', 'name', 'description'))
    return JsonResponse({'services': services})


# Вспомогательные функции для генерации документов
def _get_company_settings_dict():
    """Реквизиты исполнителя для документов (КП/НМА).

    Значения-заполнители — отредактируйте под вашу организацию (или вынесите
    в настройки/БД). ИНН/ОГРН/КПП/адрес/банк попадают в шапку документов.
    """
    return {
        'name': 'ООО «Айкьюникс Тек»',
        'inn': '0000000000',
        'kpp': '000000000',
        'ogrn': '0000000000000',
        'address': 'Россия, г. Москва',
        'phone': '',
        'email': 'info@iqnix.tech',
        'ceo_position': 'Генеральный директор',
        'ceo_full_name': '',
        'logo_path': None,  # напр. os.path.join(settings.MEDIA_ROOT, 'company', 'logo.png')
    }


def _build_project_data(project):
    """Собирает данные проекта для генераторов КП и НМА (единый калькулятор).

    КП оперирует отпускной ценой (final + НДС), НМА — себестоимостью (cost)
    по статьям затрат (ФОТ + взносы + услуги + оборудование).
    """
    result = _calculate_project(project)
    rows = result['rows']
    totals = result['totals']

    type_display = {
        'employee': 'Сотрудник', 'contractor': 'Исполнитель',
        'subcontractor': 'Подрядчик', 'equipment': 'Оборудование',
    }

    def m(v):
        return float(calc.quantize_money(v or Decimal('0')))

    resources_list = []
    fot = ins = con = sub = eq = Decimal('0')      # статьи затрат для НМА
    kp_groups: dict = {}                            # работы для КП (по услуге/типу)
    for r in rows:
        rt = r.get('resource_type')
        base = Decimal(str(r.get('base_no_tax') or 0))
        cost = Decimal(str(r.get('cost') or 0))
        final = Decimal(str(r.get('final_cost') or 0))
        resources_list.append({
            'name': r.get('name', ''),
            'type': r.get('type') or type_display.get(rt, ''),
            'service': r.get('service') or '',
            'cost': m(cost),
            'final': m(final),
        })
        if rt == 'employee':
            fot += base
            ins += (cost - base)
        elif rt == 'contractor':
            con += cost
        elif rt == 'subcontractor':
            sub += cost
        elif rt == 'equipment':
            eq += cost
        # КП: группировка по услуге, иначе по типу ресурса
        key = r.get('service') or f"Работы: {type_display.get(rt, 'прочее').lower()}"
        kp_groups[key] = kp_groups.get(key, Decimal('0')) + final

    nma_breakdown = []
    for label, val in (
        ('Оплата труда (ФОТ)', fot),
        ('Страховые взносы на ФОТ', ins),
        ('Услуги исполнителей (ГПХ/НПД)', con),
        ('Услуги подрядчиков', sub),
        ('Оборудование и ПО', eq),
    ):
        if val > 0:
            nma_breakdown.append({'article': label, 'amount': m(val)})

    kp_items = [
        {'num': i, 'name': name, 'unit': 'усл.', 'qty': 1, 'price': m(total), 'sum': m(total)}
        for i, (name, total) in enumerate(kp_groups.items(), 1)
    ]

    def fmt_date(dt):
        return dt.strftime('%d.%m.%Y') if dt else '—'

    return {
        'name': project.name,
        'doc_number': str(project.id),
        'technical_task': project.technical_task_url or 'Не приложено',
        'start_date': fmt_date(project.start_date),
        'end_date': fmt_date(project.end_date),
        'customer': {'name': project.customer or '—'},
        # финансы
        'cost_total': m(totals.base_cost),       # себестоимость
        'final_price': m(totals.final_price),    # цена без НДС
        'margin_amount': m(totals.net_profit),   # наценка/прибыль
        'vat_rate': float(totals.vat_rate),
        'vat_amount': m(totals.vat_amount),
        'price_with_vat': m(totals.price_with_vat),
        # КП и НМА
        'kp_items': kp_items,
        'nma_breakdown': nma_breakdown,
        'nma_total': m(totals.base_cost),
        'resources': resources_list,
    }

# Представления скачивания документов
@login_required
def download_proposal_pdf(request, project_id):
    project = get_object_or_404(Project, id=project_id, is_active=True)
    generator = DocumentGenerator(_build_project_data(project), _get_company_settings_dict(), font_path=os.path.join(settings.MEDIA_ROOT, 'DejaVu Sans'))
    buffer = generator.generate_proposal_pdf()
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="proposal_{project.id}.pdf"'
    return response

@login_required
def download_proposal_excel(request, project_id):
    project = get_object_or_404(Project, id=project_id, is_active=True)
    generator = DocumentGenerator(_build_project_data(project), _get_company_settings_dict())
    buffer = generator.generate_proposal_excel()
    response = HttpResponse(buffer.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="proposal_{project.id}.xlsx"'
    return response

@login_required
def download_proposal_word(request, project_id):
    project = get_object_or_404(Project, id=project_id, is_active=True)
    generator = DocumentGenerator(_build_project_data(project), _get_company_settings_dict())
    buffer = generator.generate_proposal_word()
    response = HttpResponse(buffer.getvalue(), content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    response['Content-Disposition'] = f'attachment; filename="proposal_{project.id}.docx"'
    return response

@login_required
def download_nma_pdf(request, project_id):
    project = get_object_or_404(Project, id=project_id, is_active=True)
    generator = DocumentGenerator(_build_project_data(project), _get_company_settings_dict(), font_path=os.path.join(settings.MEDIA_ROOT, 'DejaVu Sans'))
    buffer = generator.generate_nma_pdf()
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="nma_{project.id}.pdf"'
    return response

@login_required
def download_nma_excel(request, project_id):
    project = get_object_or_404(Project, id=project_id, is_active=True)
    generator = DocumentGenerator(_build_project_data(project), _get_company_settings_dict())
    buffer = generator.generate_nma_excel()
    response = HttpResponse(buffer.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="nma_{project.id}.xlsx"'
    return response

@login_required
def download_nma_word(request, project_id):
    project = get_object_or_404(Project, id=project_id, is_active=True)
    generator = DocumentGenerator(_build_project_data(project), _get_company_settings_dict())
    buffer = generator.generate_nma_word()
    response = HttpResponse(buffer.getvalue(), content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    response['Content-Disposition'] = f'attachment; filename="nma_{project.id}.docx"'
    return response


# === Расписание сотрудников (Диаграмма Ганта) — глобальное ===

@login_required
def employee_gantt(request):
    """Глобальная страница расписания всех сотрудников из всех проектов"""
    employees = Employee.objects.filter(is_active=True, project__is_active=True).select_related('project')

    schedule_qs = EmployeeSchedule.objects.filter(
        employee__is_active=True, employee__project__is_active=True
    ).select_related('employee', 'employee__project')
    schedule_data = []
    for entry in schedule_qs:
        schedule_data.append({
            'id': entry.id,
            'employee_id': entry.employee_id,
            'employee_name': entry.employee.name,
            'project_name': entry.employee.project.name,
            'start_date': str(entry.start_date),
            'end_date': str(entry.end_date),
            'status': entry.status,
            'note': entry.note,
        })

    employees_list = []
    for e in employees:
        employees_list.append({
            'id': e.id,
            'name': e.name,
            'position': e.position,
            'project_name': e.project.name,
        })

    projects = Project.objects.filter(is_active=True).values('id', 'name')
    projects_json = json.dumps(list(projects), ensure_ascii=False)
    employees_json = json.dumps(employees_list, ensure_ascii=False)
    schedule_json = json.dumps(schedule_data, ensure_ascii=False)

    return render(request, 'gantt.html', {
        'employees_json': employees_json,
        'schedule_json': schedule_json,
        'projects_json': projects_json,
    })


@login_required
@require_http_methods(["POST"])
def add_schedule_entry(request):
    """Добавить запись расписания сотрудника"""
    employee_id = request.POST.get('employee_id')
    start_date = request.POST.get('start_date', '').strip()
    end_date = request.POST.get('end_date', '').strip()
    status = request.POST.get('status', 'work').strip()
    note = request.POST.get('note', '').strip()

    if not all([employee_id, start_date, end_date]):
        return JsonResponse({'success': False, 'error': 'Заполните все обязательные поля'})

    employee = get_object_or_404(Employee, id=employee_id, is_active=True)
    entry = EmployeeSchedule.objects.create(
        employee=employee,
        start_date=start_date,
        end_date=end_date,
        status=status,
        note=note,
    )
    return JsonResponse({
        'success': True,
        'entry': {
            'id': entry.id,
            'employee_id': entry.employee_id,
            'employee_name': employee.name,
            'project_name': employee.project.name,
            'start_date': str(entry.start_date),
            'end_date': str(entry.end_date),
            'status': entry.status,
            'note': entry.note,
        }
    })


@login_required
@require_http_methods(["POST"])
def delete_schedule_entry(request, entry_id):
    """Удалить запись расписания"""
    entry = get_object_or_404(EmployeeSchedule, id=entry_id)
    entry.delete()
    return JsonResponse({'success': True})
