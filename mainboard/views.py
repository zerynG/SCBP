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

from .models import (
    Contractor,
    Customer,
    Employee,
    EmployeeSchedule,
    Equipment,
    Project,
    ProjectCost,
    ProjectResourceOverride,
    Service,
    Subcontractor,
    Workspace,
)
from . import calculator as calc


# Генерация документов вынесена в отдельный модуль (КП/НМА, PDF/Excel/Word).
from .documents import DocumentGenerator


@login_required
def index(request):
    """Главная страница — dashboard."""
    # KPI для сводки
    stats = {
        'projects_active': Project.objects.filter(is_active=True).count(),
        'resources_total': (
            Employee.objects.filter(is_active=True).count() +
            Contractor.objects.filter(is_active=True).count() +
            Subcontractor.objects.filter(is_active=True).count() +
            Equipment.objects.filter(is_active=True).count()
        ),
        # Смет за месяц — заглушка, пока нет модели «смета».
        # Когда появится — считайте через filter(created_at__gte=now-30d).
        'sheets_month': 0,
    }

    # 5 последних активных проектов (пригодится, если решите вернуть блок)
    recent_projects = Project.objects.filter(is_active=True).order_by('-created_at')[:5]

    return render(request, 'index.html', {
        'stats': stats,
        'recent_projects': recent_projects,
    })

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
    """Словарь {employee_id: [entries]} только для dayoff/other_project.

    Фильтруем расписание по сотрудникам, которые сейчас в проекте (через overrides).
    """
    # Какие сотрудники сейчас в проекте?
    employee_ids = list(
        ProjectResourceOverride.objects.filter(
            project=project, resource_type='employee',
        ).values_list('resource_id', flat=True)
    )
    if not employee_ids:
        return {}

    schedule_by_employee = {}
    for entry in EmployeeSchedule.objects.filter(
        employee_id__in=employee_ids,
        employee__is_active=True,
        status__in=['dayoff', 'other_project'],
    ):
        schedule_by_employee.setdefault(entry.employee_id, []).append(entry)
    return schedule_by_employee


def _calculate_project(project):
    """Единая точка расчёта проекта: возвращает dict с rows, totals и т.д.

    Собирает ресурсы через ProjectResourceOverride:
      - для каждого override → берёт ресурс из каталога Workspace
      - передаёт в calculator.calc_project
    """
    # Собираем ресурсы проекта через overrides
    overrides_map = _load_overrides_map(project)
    calendars = _load_project_calendars()

    # Группируем override по типам, читаем сами ресурсы из каталога Workspace
    ws = project.workspace
    employees_qs = []
    contractors_qs = []
    subcontractors_qs = []
    equipment_qs = []
    customers_qs = []

    if ws:
        for (rtype, rid), override in overrides_map.items():
            if rtype == 'employee':
                try:
                    employees_qs.append(ws.employees.get(id=rid, is_active=True))
                except Employee.DoesNotExist:
                    pass
            elif rtype == 'contractor':
                try:
                    contractors_qs.append(ws.contractors.get(id=rid, is_active=True))
                except Contractor.DoesNotExist:
                    pass
            elif rtype == 'subcontractor':
                try:
                    subcontractors_qs.append(ws.subcontractors.get(id=rid, is_active=True))
                except Subcontractor.DoesNotExist:
                    pass
            elif rtype == 'equipment':
                try:
                    equipment_qs.append(ws.equipment.get(id=rid, is_active=True))
                except Equipment.DoesNotExist:
                    pass
            elif rtype == 'customer':
                try:
                    customers_qs.append(ws.customers.get(id=rid, is_active=True))
                except Customer.DoesNotExist:
                    pass


    # Расписание сотрудников
    schedule_by_employee = _load_employee_schedule(project)

    rows, totals = calc.calc_project(
        project=project,
        employees=employees_qs,
        contractors=contractors_qs,
        subcontractors=subcontractors_qs,
        equipment=equipment_qs,
        customers=customers_qs,
        overrides_map=overrides_map,
        schedule_by_employee=schedule_by_employee,
        calendars=calendars,
    )

    rows_dicts = [r.as_dict() for r in rows]

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
        'customers_qs': customers_qs,
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
    request.session['last_project_id'] = project.id

    if request.method == 'POST' and request.POST.get('form_type') == 'update_project':
        project.name = request.POST.get('name', project.name)
        project.description = request.POST.get('description', project.description)
        project.technical_task_url = request.POST.get('technical_task_url', project.technical_task_url)
        project.status = request.POST.get('status', project.status)
        customer_id = request.POST.get('customer_id')
        if customer_id:
            try:
                project.customer = Customer.objects.get(
                    id=int(customer_id), workspace=project.workspace, is_active=True
                )
            except (Customer.DoesNotExist, ValueError, TypeError):
                pass
        elif 'customer_id' in request.POST:
            project.customer = None
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

    # Каталог Workspace для модалки «Добавить ресурс»
    ws = project.workspace
    catalog = _get_workspace_resources_grouped(ws) if ws else {
        'employees': [], 'executors': [], 'subcontractors': [],
        'customers': [], 'equipment': []
    }

    # Override-ы для передачи в JS (какие ресурсы уже в проекте)
    overrides_list = []
    for o in ProjectResourceOverride.objects.filter(project=project):
        overrides_list.append(_serialize_override(o))

    # Услуги для select
    services = list(Service.objects.filter(is_active=True).values('id', 'name', 'applies_to'))

    context = {
        'project': project,
        'workspace': ws,
        'sidebar_active': 'project-manage',

        'project_json': {
            'id': project.id,
            'name': project.name or '',
            'description': project.description or '',
            'customerId': project.customer_id,
            'customerName': project.customer.name if project.customer else '',
            'tz': project.technical_task_url or '',
            'startDate': project.start_date.isoformat() if project.start_date else '',
            'endDate': project.end_date.isoformat() if project.end_date else '',
         },
        # Расчёт
        'resources': result['rows'],
        'base_cost': totals.base_cost,
        'final_price': totals.final_price,
        'net_profit': totals.net_profit,
        'vat_rate': totals.vat_rate,
        'vat_amount': totals.vat_amount,
        'price_with_vat': totals.price_with_vat,

        # JSON для JS
        'calendars_json': result['calendars'],
        'catalog_json': catalog,
        'overrides_json': overrides_list,
        'services_json': services,
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
    """Собирает данные проекта для генераторов КП и НМА."""
    result = _calculate_project(project)
    rows = result['rows']
    totals = result['totals']

    type_display = {
        'employee': 'Сотрудник',
        'contractor': 'Исполнитель',
        'subcontractor': 'Подрядчик',
        'equipment': 'Оборудование',
        'customer': 'Заказчик',
    }

    def m(v):
        return float(calc.quantize_money(v or Decimal('0')))

    resources_list = []
    fot = ins = con = sub = eq = Decimal('0')
    kp_groups: dict = {}

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
        # customer → 0, не учитываем в breakdown

        # КП: только ресурсы с ненулевой стоимостью (не заказчики)
        if final > 0:
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

    customer_name = project.customer.name if project.customer else '—'

    return {
        'name': project.name,
        'doc_number': str(project.id),
        'technical_task': project.technical_task_url or 'Не приложено',
        'start_date': fmt_date(project.start_date),
        'end_date': fmt_date(project.end_date),
        'customer': {'name': customer_name},
        'cost_total': m(totals.base_cost),
        'final_price': m(totals.final_price),
        'margin_amount': m(totals.net_profit),
        'vat_rate': float(totals.vat_rate),
        'vat_amount': m(totals.vat_amount),
        'price_with_vat': m(totals.price_with_vat),
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


# ============================================================================
# Рабочие области (Workspace)
# ============================================================================

import re
from django.views.decorators.http import require_http_methods
# ↑ на случай, если ещё не импортированы в вашем файле

RESERVED_SUBDOMAINS = {'admin', 'www', 'api', 'app', 'mail', 'static', 'media'}
SUBDOMAIN_RE = re.compile(r'^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$')


def _serialize_workspace(w):
    """Сериализация Workspace в JSON-совместимый dict."""
    return {
        'id': w.id,
        'name': w.name,
        'subdomain': w.subdomain,
        'description': w.description,
        'admin': {
            'name': w.admin_name,
            'email': w.admin_email,
            'phone': w.admin_phone,
            'role': w.admin_role,
        },
        'is_active': w.is_active,
        'createdAt': w.created_at.isoformat(),
    }


def _validate_workspace_payload(data, exclude_id=None):
    """Общая валидация для create. Возвращает (errors_dict, cleaned_dict)."""
    errors = {}
    cleaned = {}

    name = (data.get('name') or '').strip()
    if not name:
        errors['name'] = 'Введите название области'
    elif len(name) > 120:
        errors['name'] = 'Название не должно превышать 120 символов'
    else:
        cleaned['name'] = name

    subdomain = (data.get('subdomain') or '').strip().lower()
    if not subdomain:
        errors['subdomain'] = 'Введите поддомен'
    elif len(subdomain) < 3:
        errors['subdomain'] = 'Минимум 3 символа'
    elif len(subdomain) > 40:
        errors['subdomain'] = 'Максимум 40 символов'
    elif not SUBDOMAIN_RE.match(subdomain):
        errors['subdomain'] = 'Только латиница, цифры и дефис (не в начале/конце)'
    elif subdomain in RESERVED_SUBDOMAINS:
        errors['subdomain'] = 'Этот поддомен зарезервирован'
    else:
        qs = Workspace.objects.filter(subdomain=subdomain)
        if exclude_id:
            qs = qs.exclude(id=exclude_id)
        if qs.exists():
            errors['subdomain'] = 'Этот поддомен уже используется'
        else:
            cleaned['subdomain'] = subdomain

    admin_name = (data.get('admin_name') or '').strip()
    if not admin_name:
        errors['admin_name'] = 'Введите ФИО'
    else:
        cleaned['admin_name'] = admin_name

    admin_email = (data.get('admin_email') or '').strip()
    if not admin_email:
        errors['admin_email'] = 'Введите email'
    elif '@' not in admin_email or '.' not in admin_email.split('@')[-1]:
        errors['admin_email'] = 'Некорректный email'
    else:
        cleaned['admin_email'] = admin_email

    admin_phone = (data.get('admin_phone') or '').strip()
    if admin_phone:
        digits = re.sub(r'\D', '', admin_phone)
        if not (len(digits) == 10 or (len(digits) == 11 and digits.startswith('7'))):
            errors['admin_phone'] = 'Телефон должен содержать 10 цифр'
        else:
            cleaned['admin_phone'] = admin_phone
    else:
        cleaned['admin_phone'] = ''

    cleaned['description'] = (data.get('description') or '').strip()
    cleaned['admin_role'] = (data.get('admin_role') or '').strip() or 'Администратор'

    return errors, cleaned


@login_required
@require_http_methods(["GET"])
def workspace_list(request):
    """Страница со списком рабочих областей."""
    workspaces = Workspace.objects.filter(is_active=True)
    return render(request, 'workspace_list.html', {
        'workspaces': workspaces,
        'sidebar_active': 'workspace',
    })


@login_required
@require_http_methods(["GET"])
def workspaces_api(request):
    """GET /api/workspaces/ — список активных областей."""
    qs = Workspace.objects.filter(is_active=True)
    return JsonResponse({
        'workspaces': [_serialize_workspace(w) for w in qs]
    })


@login_required
@require_http_methods(["POST"])
def workspace_create(request):
    """POST /api/workspaces/create/ — создание рабочей области."""
    errors, cleaned = _validate_workspace_payload(request.POST)
    if errors:
        return JsonResponse({'success': False, 'errors': errors}, status=400)

    workspace = Workspace.objects.create(
        name=cleaned['name'],
        subdomain=cleaned['subdomain'],
        description=cleaned['description'],
        admin_name=cleaned['admin_name'],
        admin_email=cleaned['admin_email'],
        admin_phone=cleaned['admin_phone'],
        admin_role=cleaned['admin_role'],
    )
    return JsonResponse({
        'success': True,
        'workspace': _serialize_workspace(workspace),
    })


@login_required
@require_http_methods(["POST"])
def workspace_check_subdomain(request):
    """POST /api/workspaces/check-subdomain/ — проверка свободен ли поддомен.

    Принимает: subdomain (str), опционально exclude_id (int).
    Возвращает: {available: bool, reason: str|null}
    """
    subdomain = (request.POST.get('subdomain') or '').strip().lower()
    exclude_id = request.POST.get('exclude_id')

    if not subdomain:
        return JsonResponse({'available': False, 'reason': 'Введите поддомен'})
    if len(subdomain) < 3:
        return JsonResponse({'available': False, 'reason': 'Минимум 3 символа'})
    if len(subdomain) > 40:
        return JsonResponse({'available': False, 'reason': 'Максимум 40 символов'})
    if not SUBDOMAIN_RE.match(subdomain):
        return JsonResponse({'available': False, 'reason': 'Только латиница, цифры и дефис'})
    if subdomain in RESERVED_SUBDOMAINS:
        return JsonResponse({'available': False, 'reason': 'Этот поддомен зарезервирован'})

    qs = Workspace.objects.filter(subdomain=subdomain)
    if exclude_id:
        try:
            qs = qs.exclude(id=int(exclude_id))
        except (ValueError, TypeError):
            pass
    if qs.exists():
        return JsonResponse({'available': False, 'reason': 'Этот поддомен уже используется'})

    return JsonResponse({'available': True, 'reason': None})


# ============================================================================
# КАТАЛОГ РЕСУРСОВ (в рамках Workspace)
# ============================================================================

def _get_active_workspace(request):
    """Получить текущую рабочую область из сессии или последнюю созданную."""
    ws_id = request.session.get('active_workspace_id')
    if ws_id:
        try:
            return Workspace.objects.get(id=ws_id, is_active=True)
        except Workspace.DoesNotExist:
            pass
    ws = Workspace.objects.filter(is_active=True).order_by('-created_at').first()
    if ws:
        request.session['active_workspace_id'] = ws.id
    return ws


def _parse_date(s):
    """ISO-строка → date или None."""
    if not s or not str(s).strip():
        return None
    try:
        return date.fromisoformat(str(s).strip())
    except (ValueError, TypeError):
        return None

# ---------- Сериализаторы ----------

def _serialize_employee(e):
    salary = float(e.monthly_salary or 0)
    tax = float(e.tax_rate or 0)
    cost = salary * (1 + tax / 100)
    return {
        'id': e.id,
        'name': e.name,
        'position': e.position,
        'salary': str(e.monthly_salary),
        'taxRate': str(e.tax_rate),
        'cost': str(round(cost, 2)),
    }


def _serialize_contractor(c):
    tax = float(c.tax_rate or 0)
    if c.unit == 'fixed':
        base = float(c.fixed_cost or 0)
    elif c.unit == 'days':
        base = float(c.daily_cost or 0)
    else:
        base = float(c.hourly_cost or 0)
    cost = base * (1 + tax / 100)
    return {
        'id': c.id,
        'name': c.name,
        'contractType': c.contract_type,
        'unit': c.unit,
        'taxRate': str(c.tax_rate),
        'hourlyCost': str(c.hourly_cost or ''),
        'dailyCost': str(c.daily_cost or ''),
        'fixedCost': str(c.fixed_cost or ''),
        'cost': str(round(cost, 2)),
    }


def _serialize_subcontractor(s):
    return {
        'id': s.id,
        'name': s.name,
        'type': s.entity_type,
        'inn': s.inn,
        'kpp': s.kpp,
        'ogrn': s.ogrn,
        'legalAddress': s.legal_address,
        'actualAddress': s.actual_address,
        'phone': s.phone,
        'email': s.email,
        'director': s.director,
        'bankName': s.bank_name,
        'bik': s.bik,
        'account': s.account,
        'corrAccount': s.corr_account,
    }


def _serialize_customer(c):
    return {
        'id': c.id,
        'name': c.name,
        'type': c.customer_type,
        'inn': c.inn,
        'organization': c.organization,
        'email': c.email,
        'phone': c.phone,
    }


def _serialize_equipment(eq):
    return {
        'id': eq.id,
        'name': eq.name,
        'acquisitionType': eq.acquisition_type,
        'unit': eq.unit,
        'cost': str(eq.cost),
        'operationalCost': str(eq.operational_cost or ''),
        'description': eq.description,
    }


# ---------- Хелперы ----------

def _dec(val, default=None):
    if val is None or str(val).strip() == '':
        return default
    try:
        return Decimal(str(val).replace(',', '.').strip())
    except Exception:
        return default


# ---------- Страница каталога ----------

@login_required
def resources_page(request):
    """Страница каталога ресурсов."""
    ws = _get_active_workspace(request)
    return render(request, 'resources.html', {
        'workspace': ws,
        'sidebar_active': 'resources-employees',
    })


# ---------- API: список ----------

@login_required
@require_http_methods(["GET"])
def resources_api(request):
    ws = _get_active_workspace(request)
    if not ws:
        return JsonResponse({
            'employees': [], 'executors': [], 'subcontractors': [],
            'customers': [], 'equipment': [],
            'counts': {'employees': 0, 'executors': 0, 'subcontractors': 0,
                       'customers': 0, 'equipment': 0},
        })
    return JsonResponse({
        'employees': [_serialize_employee(e) for e in ws.employees.filter(is_active=True)],
        'executors': [_serialize_contractor(c) for c in ws.contractors.filter(is_active=True)],
        'subcontractors': [_serialize_subcontractor(s) for s in ws.subcontractors.filter(is_active=True)],
        'customers': [_serialize_customer(c) for c in ws.customers.filter(is_active=True)],
        'equipment': [_serialize_equipment(e) for e in ws.equipment.filter(is_active=True)],
        'counts': {
            'employees': ws.employees.filter(is_active=True).count(),
            'executors': ws.contractors.filter(is_active=True).count(),
            'subcontractors': ws.subcontractors.filter(is_active=True).count(),
            'customers': ws.customers.filter(is_active=True).count(),
            'equipment': ws.equipment.filter(is_active=True).count(),
        },
    })


# ---------- API: Employees ----------

@login_required
@require_http_methods(["POST"])
def employee_create(request):
    ws = _get_active_workspace(request)
    if not ws:
        return JsonResponse({'success': False, 'error': 'Рабочая область не найдена'}, status=400)
    name = (request.POST.get('name') or '').strip()
    position = (request.POST.get('position') or '').strip()
    salary = _dec(request.POST.get('salary'))
    tax_rate = _dec(request.POST.get('taxRate'), Decimal('13'))
    if not name or not position or not salary:
        return JsonResponse({'success': False, 'error': 'Заполните обязательные поля'}, status=400)
    e = Employee.objects.create(
        workspace=ws, name=name, position=position,
        monthly_salary=salary, tax_rate=tax_rate,
    )
    return JsonResponse({'success': True, 'item': _serialize_employee(e)})


@login_required
@require_http_methods(["POST"])
def employee_update(request, pk):
    ws = _get_active_workspace(request)
    e = get_object_or_404(Employee, id=pk, workspace=ws)
    name = (request.POST.get('name') or '').strip()
    position = (request.POST.get('position') or '').strip()
    salary = _dec(request.POST.get('salary'))
    tax_rate = _dec(request.POST.get('taxRate'), Decimal('13'))
    if not name or not position or not salary:
        return JsonResponse({'success': False, 'error': 'Заполните обязательные поля'}, status=400)
    e.name = name
    e.position = position
    e.monthly_salary = salary
    e.tax_rate = tax_rate
    e.save()
    return JsonResponse({'success': True, 'item': _serialize_employee(e)})


@login_required
@require_http_methods(["POST"])
def employee_delete(request, pk):
    ws = _get_active_workspace(request)
    e = get_object_or_404(Employee, id=pk, workspace=ws)
    e.is_active = False
    e.save(update_fields=['is_active'])
    return JsonResponse({'success': True})


# ---------- API: Contractors (executors) ----------

@login_required
@require_http_methods(["POST"])
def contractor_create(request):
    ws = _get_active_workspace(request)
    if not ws:
        return JsonResponse({'success': False, 'error': 'Рабочая область не найдена'}, status=400)
    name = (request.POST.get('name') or '').strip()
    if not name:
        return JsonResponse({'success': False, 'error': 'Укажите ФИО'}, status=400)
    c = Contractor.objects.create(
        workspace=ws,
        name=name,
        contract_type=request.POST.get('contractType') or 'gph',
        unit=request.POST.get('unit') or 'hours',
        tax_rate=_dec(request.POST.get('taxRate'), Decimal('13')),
        hourly_cost=_dec(request.POST.get('hourlyCost')),
        daily_cost=_dec(request.POST.get('dailyCost')),
        fixed_cost=_dec(request.POST.get('fixedCost')),
    )
    return JsonResponse({'success': True, 'item': _serialize_contractor(c)})


@login_required
@require_http_methods(["POST"])
def contractor_update(request, pk):
    ws = _get_active_workspace(request)
    c = get_object_or_404(Contractor, id=pk, workspace=ws)
    c.name = (request.POST.get('name') or c.name).strip()
    c.contract_type = request.POST.get('contractType') or c.contract_type
    c.unit = request.POST.get('unit') or c.unit
    c.tax_rate = _dec(request.POST.get('taxRate'), c.tax_rate)
    c.hourly_cost = _dec(request.POST.get('hourlyCost'))
    c.daily_cost = _dec(request.POST.get('dailyCost'))
    c.fixed_cost = _dec(request.POST.get('fixedCost'))
    c.save()
    return JsonResponse({'success': True, 'item': _serialize_contractor(c)})


@login_required
@require_http_methods(["POST"])
def contractor_delete(request, pk):
    ws = _get_active_workspace(request)
    c = get_object_or_404(Contractor, id=pk, workspace=ws)
    c.is_active = False
    c.save(update_fields=['is_active'])
    return JsonResponse({'success': True})


# ---------- API: Subcontractors ----------

@login_required
@require_http_methods(["POST"])
def subcontractor_create(request):
    ws = _get_active_workspace(request)
    if not ws:
        return JsonResponse({'success': False, 'error': 'Рабочая область не найдена'}, status=400)
    name = (request.POST.get('name') or '').strip()
    if not name:
        return JsonResponse({'success': False, 'error': 'Укажите наименование'}, status=400)
    s = Subcontractor.objects.create(
        workspace=ws,
        name=name,
        entity_type=request.POST.get('type') or 'legal',
        inn=request.POST.get('inn') or '',
        kpp=request.POST.get('kpp') or '',
        ogrn=request.POST.get('ogrn') or '',
        legal_address=request.POST.get('legalAddress') or '',
        actual_address=request.POST.get('actualAddress') or '',
        phone=request.POST.get('phone') or '',
        email=request.POST.get('email') or '',
        director=request.POST.get('director') or '',
        bank_name=request.POST.get('bankName') or '',
        bik=request.POST.get('bik') or '',
        account=request.POST.get('account') or '',
        corr_account=request.POST.get('corrAccount') or '',
    )
    return JsonResponse({'success': True, 'item': _serialize_subcontractor(s)})


@login_required
@require_http_methods(["POST"])
def subcontractor_update(request, pk):
    ws = _get_active_workspace(request)
    s = get_object_or_404(Subcontractor, id=pk, workspace=ws)
    s.name = (request.POST.get('name') or s.name).strip()
    s.entity_type = request.POST.get('type') or s.entity_type
    s.inn = request.POST.get('inn') or ''
    s.kpp = request.POST.get('kpp') or ''
    s.ogrn = request.POST.get('ogrn') or ''
    s.legal_address = request.POST.get('legalAddress') or ''
    s.actual_address = request.POST.get('actualAddress') or ''
    s.phone = request.POST.get('phone') or ''
    s.email = request.POST.get('email') or ''
    s.director = request.POST.get('director') or ''
    s.bank_name = request.POST.get('bankName') or ''
    s.bik = request.POST.get('bik') or ''
    s.account = request.POST.get('account') or ''
    s.corr_account = request.POST.get('corrAccount') or ''
    s.save()
    return JsonResponse({'success': True, 'item': _serialize_subcontractor(s)})


@login_required
@require_http_methods(["POST"])
def subcontractor_delete(request, pk):
    ws = _get_active_workspace(request)
    s = get_object_or_404(Subcontractor, id=pk, workspace=ws)
    s.is_active = False
    s.save(update_fields=['is_active'])
    return JsonResponse({'success': True})


# ---------- API: Customers ----------

@login_required
@require_http_methods(["POST"])
def customer_create(request):
    ws = _get_active_workspace(request)
    if not ws:
        return JsonResponse({'success': False, 'error': 'Рабочая область не найдена'}, status=400)
    name = (request.POST.get('name') or '').strip()
    if not name:
        return JsonResponse({'success': False, 'error': 'Укажите ФИО'}, status=400)
    c = Customer.objects.create(
        workspace=ws,
        name=name,
        customer_type=request.POST.get('type') or 'individual',
        inn=request.POST.get('inn') or '',
        organization=request.POST.get('organization') or '',
        email=request.POST.get('email') or '',
        phone=request.POST.get('phone') or '',
    )
    return JsonResponse({'success': True, 'item': _serialize_customer(c)})


@login_required
@require_http_methods(["POST"])
def customer_update(request, pk):
    ws = _get_active_workspace(request)
    c = get_object_or_404(Customer, id=pk, workspace=ws)
    c.name = (request.POST.get('name') or c.name).strip()
    c.customer_type = request.POST.get('type') or c.customer_type
    c.inn = request.POST.get('inn') or ''
    c.organization = request.POST.get('organization') or ''
    c.email = request.POST.get('email') or ''
    c.phone = request.POST.get('phone') or ''
    c.save()
    return JsonResponse({'success': True, 'item': _serialize_customer(c)})


@login_required
@require_http_methods(["POST"])
def customer_delete(request, pk):
    ws = _get_active_workspace(request)
    c = get_object_or_404(Customer, id=pk, workspace=ws)
    c.is_active = False
    c.save(update_fields=['is_active'])
    return JsonResponse({'success': True})


# ---------- API: Equipment ----------

@login_required
@require_http_methods(["POST"])
def equipment_create(request):
    ws = _get_active_workspace(request)
    if not ws:
        return JsonResponse({'success': False, 'error': 'Рабочая область не найдена'}, status=400)
    name = (request.POST.get('name') or '').strip()
    cost = _dec(request.POST.get('cost'))
    if not name or not cost:
        return JsonResponse({'success': False, 'error': 'Укажите название и стоимость'}, status=400)
    eq = Equipment.objects.create(
        workspace=ws,
        name=name,
        acquisition_type=request.POST.get('acquisitionType') or 'owned',
        unit=request.POST.get('unit') or 'hours',
        cost=cost,
        operational_cost=_dec(request.POST.get('operationalCost'), Decimal('0')),
        description=request.POST.get('description') or '',
    )
    return JsonResponse({'success': True, 'item': _serialize_equipment(eq)})


@login_required
@require_http_methods(["POST"])
def equipment_update(request, pk):
    ws = _get_active_workspace(request)
    eq = get_object_or_404(Equipment, id=pk, workspace=ws)
    eq.name = (request.POST.get('name') or eq.name).strip()
    eq.acquisition_type = request.POST.get('acquisitionType') or eq.acquisition_type
    eq.unit = request.POST.get('unit') or eq.unit
    eq.cost = _dec(request.POST.get('cost'), eq.cost)
    eq.operational_cost = _dec(request.POST.get('operationalCost'), Decimal('0'))
    eq.description = request.POST.get('description') or ''
    eq.save()
    return JsonResponse({'success': True, 'item': _serialize_equipment(eq)})


@login_required
@require_http_methods(["POST"])
def equipment_delete(request, pk):
    ws = _get_active_workspace(request)
    eq = get_object_or_404(Equipment, id=pk, workspace=ws)
    eq.is_active = False
    eq.save(update_fields=['is_active'])
    return JsonResponse({'success': True})


# ---------- API: счётчики для сайдбара ----------

@login_required
@require_http_methods(["GET"])
def sidebar_counters_api(request):
    """Счётчики ресурсов для сайдбара (по текущей рабочей области)."""
    ws = _get_active_workspace(request)
    if not ws:
        return JsonResponse({
            'employees': 0, 'executors': 0, 'subcontractors': 0,
            'customers': 0, 'equipment': 0,
        })
    return JsonResponse({
        'employees': ws.employees.filter(is_active=True).count(),
        'executors': ws.contractors.filter(is_active=True).count(),
        'subcontractors': ws.subcontractors.filter(is_active=True).count(),
        'customers': ws.customers.filter(is_active=True).count(),
        'equipment': ws.equipment.filter(is_active=True).count(),
    })

# ============================================================================
# ОБЗОР ОДНОЙ РАБОЧЕЙ ОБЛАСТИ
# ============================================================================

def _serialize_workspace_detail(w):
    """Полная сериализация Workspace для страницы обзора."""
    return {
        'id': w.id,
        'name': w.name,
        'subdomain': w.subdomain,
        'description': w.description,
        'admin': {
            'name': w.admin_name,
            'email': w.admin_email,
            'phone': w.admin_phone,
            'role': w.admin_role,
        },
        'is_active': w.is_active,
        'createdAt': w.created_at.isoformat(),
        'updatedAt': w.updated_at.isoformat(),
        'counts': {
            'employees': w.employees.filter(is_active=True).count(),
            'executors': w.contractors.filter(is_active=True).count(),
            'subcontractors': w.subcontractors.filter(is_active=True).count(),
            'customers': w.customers.filter(is_active=True).count(),
            'equipment': w.equipment.filter(is_active=True).count(),
        },
    }


def _collect_workspace_resources(w):
    """Собирает все ресурсы области в плоский список с типом и индексом."""
    out = []
    for e in w.employees.filter(is_active=True):
        out.append({'type': 'employee', 'id': e.id, 'item': _serialize_employee(e)})
    for c in w.contractors.filter(is_active=True):
        out.append({'type': 'executor', 'id': c.id, 'item': _serialize_contractor(c)})
    for s in w.subcontractors.filter(is_active=True):
        out.append({'type': 'subcontractor', 'id': s.id, 'item': _serialize_subcontractor(s)})
    for cu in w.customers.filter(is_active=True):
        out.append({'type': 'customer', 'id': cu.id, 'item': _serialize_customer(cu)})
    for eq in w.equipment.filter(is_active=True):
        out.append({'type': 'equipment', 'id': eq.id, 'item': _serialize_equipment(eq)})
    return out


@login_required
def workspace_detail(request, workspace_id):
    """Страница обзора одной рабочей области."""
    ws = get_object_or_404(Workspace, id=workspace_id)
    # Запоминаем как активную для сайдбара/каталога
    request.session['active_workspace_id'] = ws.id

    # Группированные данные (для первичного рендера в JSON-скриптах)
    groups = {
        'employees':      [_serialize_employee(e)      for e in ws.employees.filter(is_active=True)],
        'executors':      [_serialize_contractor(c)    for c in ws.contractors.filter(is_active=True)],
        'subcontractors': [_serialize_subcontractor(s) for s in ws.subcontractors.filter(is_active=True)],
        'customers':      [_serialize_customer(cu)     for cu in ws.customers.filter(is_active=True)],
        'equipment':      [_serialize_equipment(eq)    for eq in ws.equipment.filter(is_active=True)],
    }

    return render(request, 'workspace_detail.html', {
        'workspace': ws,
        'workspace_json': _serialize_workspace_detail(ws),
        'resources_json': groups,
        'sidebar_active': 'workspace',
    })


@login_required
@require_http_methods(["GET"])
def workspace_detail_api(request, workspace_id):
    """GET /api/workspaces/<id>/ — детали + ресурсы."""
    ws = get_object_or_404(Workspace, id=workspace_id)
    groups = {
        'employees':      [_serialize_employee(e)      for e in ws.employees.filter(is_active=True)],
        'executors':      [_serialize_contractor(c)    for c in ws.contractors.filter(is_active=True)],
        'subcontractors': [_serialize_subcontractor(s) for s in ws.subcontractors.filter(is_active=True)],
        'customers':      [_serialize_customer(cu)     for cu in ws.customers.filter(is_active=True)],
        'equipment':      [_serialize_equipment(eq)    for eq in ws.equipment.filter(is_active=True)],
    }
    return JsonResponse({
        'workspace': _serialize_workspace_detail(ws),
        'resources': groups,
    })


@login_required
@require_http_methods(["POST"])
def workspace_toggle(request, workspace_id):
    """POST /api/workspaces/<id>/toggle/ — активировать/деактивировать."""
    ws = get_object_or_404(Workspace, id=workspace_id)
    ws.is_active = not ws.is_active
    ws.save(update_fields=['is_active'])
    return JsonResponse({
        'success': True,
        'is_active': ws.is_active,
    })


@login_required
@require_http_methods(["POST"])
def workspace_delete(request, workspace_id):
    """POST /api/workspaces/<id>/delete/ — удалить."""
    ws = get_object_or_404(Workspace, id=workspace_id)
    ws.delete()
    return JsonResponse({'success': True})


@login_required
@require_http_methods(["POST"])
def workspace_update(request, workspace_id):
    """POST /api/workspaces/<id>/update/ — редактировать."""
    ws = get_object_or_404(Workspace, id=workspace_id)
    errors, cleaned = _validate_workspace_payload(request.POST, exclude_id=ws.id)
    if errors:
        return JsonResponse({'success': False, 'errors': errors}, status=400)

    ws.name = cleaned['name']
    ws.subdomain = cleaned['subdomain']
    ws.description = cleaned['description']
    ws.admin_name = cleaned['admin_name']
    ws.admin_email = cleaned['admin_email']
    ws.admin_phone = cleaned['admin_phone']
    ws.admin_role = cleaned['admin_role']
    ws.save()

    return JsonResponse({
        'success': True,
        'workspace': _serialize_workspace_detail(ws),
    })

# ============================================================================
# СПИСОК ПРОЕКТОВ (в рамках Workspace)
# ============================================================================

def _serialize_project(p):
    """Краткая сериализация Project для списка."""
    resources_count = p.resource_overrides.count()
    return {
        'id': p.id,
        'name': p.name,
        'description': p.description,
        'customer': p.customer.name if p.customer else '',
        'customerId': p.customer_id,
        'status': p.status,
        'startDate': p.start_date.isoformat() if p.start_date else '',
        'endDate': p.end_date.isoformat() if p.end_date else '',
        'tz': p.technical_task_url,
        'isActive': p.is_active,
        'resourcesCount': resources_count,
        'createdAt': p.created_at.isoformat(),
        'updatedAt': p.updated_at.isoformat(),
    }


@login_required
def project_list_page(request):
    """Страница списка проектов текущей Workspace."""
    ws = _get_active_workspace(request)
    return render(request, 'project_list.html', {
        'workspace': ws,
        'sidebar_active': 'project-manage',
    })


@login_required
@require_http_methods(["GET"])
def projects_api(request):
    """GET /api/projects/ — список проектов текущей Workspace."""
    ws = _get_active_workspace(request)
    if not ws:
        return JsonResponse({'projects': []})
    qs = Project.objects.filter(workspace=ws, is_active=True).select_related('customer')
    return JsonResponse({'projects': [_serialize_project(p) for p in qs]})


@login_required
@require_http_methods(["POST"])
def project_create(request):
    """POST /api/projects/create/ — создать проект в текущей Workspace."""
    ws = _get_active_workspace(request)
    if not ws:
        return JsonResponse({'success': False, 'error': 'Рабочая область не найдена'}, status=400)

    name = (request.POST.get('name') or '').strip()
    if not name:
        return JsonResponse({'success': False, 'error': 'Укажите название проекта'}, status=400)

    customer_id = request.POST.get('customer_id')
    customer = None
    if customer_id:
        try:
            customer = Customer.objects.get(id=int(customer_id), workspace=ws, is_active=True)
        except (Customer.DoesNotExist, ValueError, TypeError):
            pass

    project = Project.objects.create(
        workspace=ws,
        name=name,
        description=(request.POST.get('description') or '').strip(),
        customer=customer,
        technical_task_url=(request.POST.get('tz') or '').strip(),
        start_date=_parse_date(request.POST.get('start_date')),
        end_date=_parse_date(request.POST.get('end_date')),
    )
    return JsonResponse({'success': True, 'project': _serialize_project(project)})


@login_required
@require_http_methods(["POST"])
def project_update_meta(request, project_id):
    """POST /api/projects/<id>/update/ — редактировать основные данные проекта."""
    ws = _get_active_workspace(request)
    project = get_object_or_404(Project, id=project_id, workspace=ws)

    name = (request.POST.get('name') or '').strip()
    if not name:
        return JsonResponse({'success': False, 'error': 'Укажите название'}, status=400)

    customer_id = request.POST.get('customer_id')
    customer = None
    if customer_id:
        try:
            customer = Customer.objects.get(id=int(customer_id), workspace=ws, is_active=True)
        except (Customer.DoesNotExist, ValueError, TypeError):
            pass

    project.name = name
    project.description = (request.POST.get('description') or '').strip()
    project.customer = customer
    project.technical_task_url = (request.POST.get('tz') or '').strip()
    project.start_date = _parse_date(request.POST.get('start_date'))
    project.end_date = _parse_date(request.POST.get('end_date'))

    # Настройки расчёта
    tax_rate = _dec(request.POST.get('tax_rate'))
    margin = _dec(request.POST.get('margin'))
    vat_rate = _dec(request.POST.get('vat_rate'))
    wh_per_day = _dec(request.POST.get('work_hours_per_day'))

    if tax_rate is not None:
        project.tax_rate = tax_rate
    if margin is not None:
        project.margin = margin
    if vat_rate is not None:
        project.vat_rate = vat_rate
    if wh_per_day is not None and Decimal('0') < wh_per_day <= Decimal('24'):
        project.work_hours_per_day = wh_per_day

    project.save()
    return JsonResponse({'success': True, 'project': _serialize_project(project)})


@login_required
@require_http_methods(["POST"])
def project_hide(request, project_id):
    """POST /api/projects/<id>/hide/ — is_active=False."""
    ws = _get_active_workspace(request)
    project = get_object_or_404(Project, id=project_id, workspace=ws)
    project.is_active = False
    project.save(update_fields=['is_active'])
    return JsonResponse({'success': True})


@login_required
@require_http_methods(["POST"])
def project_delete(request, project_id):
    """POST /api/projects/<id>/delete/ — удалить проект."""
    ws = _get_active_workspace(request)
    project = get_object_or_404(Project, id=project_id, workspace=ws)
    project.delete()
    return JsonResponse({'success': True})


# ============================================================================
# РЕСУРСЫ ПРОЕКТА (overrides)
# ============================================================================

def _get_workspace_resources_grouped(ws):
    """Каталог ресурсов Workspace, сгруппированный по типу."""
    if not ws:
        return {'employees': [], 'executors': [], 'subcontractors': [],
                'customers': [], 'equipment': []}
    return {
        'employees':      [_serialize_employee(e)      for e in ws.employees.filter(is_active=True)],
        'executors':      [_serialize_contractor(c)    for c in ws.contractors.filter(is_active=True)],
        'subcontractors': [_serialize_subcontractor(s) for s in ws.subcontractors.filter(is_active=True)],
        'customers':      [_serialize_customer(cu)     for cu in ws.customers.filter(is_active=True)],
        'equipment':      [_serialize_equipment(eq)    for eq in ws.equipment.filter(is_active=True)],
    }


@login_required
@require_http_methods(["GET"])
def workspace_catalog_api(request):
    """GET /api/catalog/ — ресурсы каталога текущей Workspace (для модалки)."""
    ws = _get_active_workspace(request)
    return JsonResponse(_get_workspace_resources_grouped(ws))


def _serialize_override(o):
    return {
        'id': o.id,
        'resource_type': o.resource_type,
        'resource_id': o.resource_id,
        'resource_margin': str(o.resource_margin) if o.resource_margin is not None else None,
        'service_id': o.service_id,
        'service_name': o.service.name if o.service else '',
        'work_start_date': o.work_start_date.isoformat() if o.work_start_date else '',
        'work_end_date': o.work_end_date.isoformat() if o.work_end_date else '',
        'working_hours': o.working_hours,
        'quantity': o.quantity,
        'use_project_dates': o.use_project_dates,
    }


@login_required
@require_http_methods(["POST"])
def project_add_resource(request, project_id):
    """POST /api/projects/<id>/add-resource/ — добавить ресурс в проект.

    Принимает: resource_type, resource_id. Проверяет, что ресурс есть в
    каталоге Workspace и его ещё нет в проекте. Создаёт override.
    """
    ws = _get_active_workspace(request)
    project = get_object_or_404(Project, id=project_id, workspace=ws)

    rtype = request.POST.get('resource_type')
    rid_raw = request.POST.get('resource_id')

    if rtype not in ('employee', 'contractor', 'subcontractor', 'customer', 'equipment'):
        return JsonResponse({'success': False, 'error': 'Неверный тип ресурса'}, status=400)

    try:
        rid = int(rid_raw)
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Неверный ID ресурса'}, status=400)

    # Проверка, что ресурс существует в каталоге Workspace
    model_map = {
        'employee': Employee, 'contractor': Contractor,
        'subcontractor': Subcontractor, 'customer': Customer, 'equipment': Equipment,
    }
    Model = model_map[rtype]
    try:
        Model.objects.get(id=rid, workspace=ws, is_active=True)
    except Model.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Ресурс не найден в каталоге'}, status=404)

    # Уже добавлен?
    if ProjectResourceOverride.objects.filter(
        project=project, resource_type=rtype, resource_id=rid
    ).exists():
        return JsonResponse({'success': False, 'error': 'Ресурс уже в проекте'}, status=400)

    override = ProjectResourceOverride.objects.create(
        project=project, resource_type=rtype, resource_id=rid,
        quantity=1, use_project_dates=False,
    )
    return JsonResponse({'success': True, 'override': _serialize_override(override)})


@login_required
@require_http_methods(["POST"])
def project_remove_resource(request, project_id):
    """POST /api/projects/<id>/remove-resource/ — удалить ресурс из проекта.

    Принимает: resource_type, resource_id.
    """
    ws = _get_active_workspace(request)
    project = get_object_or_404(Project, id=project_id, workspace=ws)

    rtype = request.POST.get('resource_type')
    try:
        rid = int(request.POST.get('resource_id') or 0)
    except (ValueError, TypeError):
        rid = 0

    if not rtype or not rid:
        return JsonResponse({'success': False, 'error': 'Не указан ресурс'}, status=400)

    ProjectResourceOverride.objects.filter(
        project=project, resource_type=rtype, resource_id=rid
    ).delete()
    return JsonResponse({'success': True})


@login_required
@require_http_methods(["POST"])
def project_update_override(request, project_id):
    """POST /api/projects/<id>/update-override/ — сохранить параметры ресурса.

    Принимает: resource_type, resource_id, а также поля override:
    resource_margin, service_id, work_start_date, work_end_date,
    working_hours, quantity, use_project_dates.
    """
    ws = _get_active_workspace(request)
    project = get_object_or_404(Project, id=project_id, workspace=ws)

    rtype = request.POST.get('resource_type')
    try:
        rid = int(request.POST.get('resource_id') or 0)
    except (ValueError, TypeError):
        rid = 0

    if not rtype or not rid:
        return JsonResponse({'success': False, 'error': 'Не указан ресурс'}, status=400)

    override, _ = ProjectResourceOverride.objects.get_or_create(
        project=project, resource_type=rtype, resource_id=rid,
        defaults={'quantity': 1, 'use_project_dates': False},
    )

    # Маржа — только для employee/contractor. Для остальных принудительно None.
    if rtype in ('employee', 'contractor'):
        raw = request.POST.get('resource_margin')
        if raw is None or str(raw).strip() == '':
            override.resource_margin = None
        else:
            override.resource_margin = _dec(raw)
    else:
        override.resource_margin = None

    # Услуга
    service_id = request.POST.get('service_id')
    if service_id is not None:
        if str(service_id).strip() == '':
            override.service = None
        else:
            try:
                override.service = Service.objects.get(id=int(service_id))
            except (Service.DoesNotExist, ValueError, TypeError):
                override.service = None

    # Даты
    override.work_start_date = _parse_date(request.POST.get('work_start_date'))
    override.work_end_date = _parse_date(request.POST.get('work_end_date'))

    # Часы
    wh = request.POST.get('working_hours')
    if wh is None or str(wh).strip() == '':
        override.working_hours = None
    else:
        try:
            override.working_hours = max(0, int(wh))
        except (ValueError, TypeError):
            override.working_hours = None

    # Количество
    qty = request.POST.get('quantity')
    if qty is not None and str(qty).strip() != '':
        try:
            override.quantity = max(1, int(qty))
        except (ValueError, TypeError):
            override.quantity = 1

    # Флаг
    override.use_project_dates = (request.POST.get('use_project_dates') == '1')

    override.save()

    # Пересчёт и возврат актуального снимка
    result = _calculate_project(project)
    totals = result['totals']
    _persist_project_cost(project, totals)
    return JsonResponse({
        'success': True,
        'totals': calc.serialize_totals(totals),
        'rows': [calc.serialize_row(_row_obj_for_serialization(r)) for r in result['rows']],
        'override': _serialize_override(override),
    })


@login_required
@require_http_methods(["POST"])
def project_recalculate(request, project_id):
    """POST /api/projects/<id>/recalculate/ — актуальный снимок расчёта."""
    ws = _get_active_workspace(request)
    project = get_object_or_404(Project, id=project_id, workspace=ws)
    result = _calculate_project(project)
    totals = result['totals']
    _persist_project_cost(project, totals)
    return JsonResponse({
        'success': True,
        'totals': calc.serialize_totals(totals),
        'rows': [calc.serialize_row(_row_obj_for_serialization(r)) for r in result['rows']],
    })

# ============================================================================
# ДИАГРАММА ГАНТА (в рамках проекта)
# ============================================================================

@login_required
def project_gantt(request, project_id):
    """Страница диаграммы Ганта для одного проекта."""
    ws = _get_active_workspace(request)
    project = get_object_or_404(Project, id=project_id, workspace=ws, is_active=True)

    # Запоминаем — чтобы при клике из сайдбара открывался этот проект
    request.session['last_project_id'] = project.id

    return render(request, 'gantt_project.html', {
        'project': project,
        'workspace': ws,
        'sidebar_active': 'gantt',
    })


@login_required
@require_http_methods(["GET"])
def gantt_tasks_api(request, project_id):
    """GET /api/projects/<id>/gantt/ — все задачи Ганта для проекта.

    Возвращает список overrides с расширенной информацией (имя, тип, сроки).
    Только те, у которых заданы сроки (есть work_start_date и work_end_date).
    """
    ws = _get_active_workspace(request)
    project = get_object_or_404(Project, id=project_id, workspace=ws)

    overrides = list(ProjectResourceOverride.objects.filter(project=project).select_related('service'))

    tasks = []
    for o in overrides:
        # Имя ресурса
        name = ''
        model_map = {
            'employee': Employee, 'contractor': Contractor,
            'subcontractor': Subcontractor, 'customer': Customer, 'equipment': Equipment,
        }
        Model = model_map.get(o.resource_type)
        if Model and ws:
            try:
                name = Model.objects.get(id=o.resource_id, workspace=ws).name
            except Model.DoesNotExist:
                name = '—'
        if not name:
            name = '—'

        # Сроки: если не заданы — используем даты проекта
        start = o.work_start_date or project.start_date
        end = o.work_end_date or project.end_date

        if not start or not end:
            # Без сроков — не показываем в Ганте
            continue

        # Статус по датам
        today = date.today()
        if end < today:
            status = 'done'
        elif start <= today <= end:
            status = 'in_progress'
        else:
            status = 'planned'

        # «Задача» — название услуги, если есть, иначе тип ресурса
        task_name = o.service.name if o.service else {
            'employee': 'Работа', 'contractor': 'Работа',
            'subcontractor': 'Работа', 'customer': 'Согласование',
            'equipment': 'Использование',
        }.get(o.resource_type, 'Задача')

        tasks.append({
            'id': o.id,
            'resource_type': o.resource_type,
            'resource_id': o.resource_id,
            'name': name,
            'task_name': task_name,
            'start': start.isoformat(),
            'end': end.isoformat(),
            'duration': (end - start).days + 1,
            'status': status,
            'service_id': o.service_id,
            'service_name': o.service.name if o.service else '',
        })

    return JsonResponse({
        'project': {
            'id': project.id,
            'name': project.name,
            'start_date': project.start_date.isoformat() if project.start_date else None,
            'end_date': project.end_date.isoformat() if project.end_date else None,
        },
        'tasks': tasks,
    })


@login_required
@require_http_methods(["GET"])
def gantt_project_resources_api(request, project_id):
    """GET /api/projects/<id>/gantt/resources/ — ресурсы проекта для модалки «Добавить».

    Возвращает все overrides (даже без дат), чтобы можно было выбрать любой
    ресурс проекта и задать/изменить сроки.
    """
    ws = _get_active_workspace(request)
    project = get_object_or_404(Project, id=project_id, workspace=ws)

    overrides = list(ProjectResourceOverride.objects.filter(project=project).select_related('service'))

    items = []
    for o in overrides:
        name = ''
        model_map = {
            'employee': Employee, 'contractor': Contractor,
            'subcontractor': Subcontractor, 'customer': Customer, 'equipment': Equipment,
        }
        Model = model_map.get(o.resource_type)
        if Model and ws:
            try:
                name = Model.objects.get(id=o.resource_id, workspace=ws).name
            except Model.DoesNotExist:
                name = '—'
        if not name:
            name = '—'
        items.append({
            'override_id': o.id,
            'resource_type': o.resource_type,
            'resource_id': o.resource_id,
            'name': name,
            'start': o.work_start_date.isoformat() if o.work_start_date else '',
            'end': o.work_end_date.isoformat() if o.work_end_date else '',
            'service_id': o.service_id,
            'service_name': o.service.name if o.service else '',
        })

    return JsonResponse({'items': items})


@login_required
@require_http_methods(["POST"])
def gantt_update_dates(request, project_id):
    """POST /api/projects/<id>/gantt/update-dates/ — обновить сроки одного ресурса.

    Принимает: override_id, start_date, end_date.
    Обновляет ProjectResourceOverride.work_start_date/work_end_date.
    Возвращает пересчёт проекта (rows + totals).
    """
    ws = _get_active_workspace(request)
    project = get_object_or_404(Project, id=project_id, workspace=ws)

    override_id = request.POST.get('override_id')
    start_str = request.POST.get('start_date')
    end_str = request.POST.get('end_date')

    if not override_id:
        return JsonResponse({'success': False, 'error': 'Не указан override'}, status=400)

    try:
        override = ProjectResourceOverride.objects.get(id=int(override_id), project=project)
    except (ProjectResourceOverride.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Ресурс не найден'}, status=404)

    override.work_start_date = _parse_date(start_str)
    override.work_end_date = _parse_date(end_str)
    override.save(update_fields=['work_start_date', 'work_end_date'])

    # Пересчёт проекта
    result = _calculate_project(project)
    totals = result['totals']
    _persist_project_cost(project, totals)

    return JsonResponse({
        'success': True,
        'totals': calc.serialize_totals(totals),
        'rows': [calc.serialize_row(_row_obj_for_serialization(r)) for r in result['rows']],
    })


@login_required
@require_http_methods(["GET"])
def project_selector_api(request):
    """GET /api/projects/for-gantt/ — список проектов для модалки выбора."""
    ws = _get_active_workspace(request)
    if not ws:
        return JsonResponse({'projects': []})
    qs = Project.objects.filter(workspace=ws, is_active=True).select_related('customer')
    return JsonResponse({
        'projects': [{
            'id': p.id,
            'name': p.name,
            'customer': p.customer.name if p.customer else '',
            'start_date': p.start_date.isoformat() if p.start_date else '',
            'end_date': p.end_date.isoformat() if p.end_date else '',
        } for p in qs]
    })


@login_required
@require_http_methods(["GET"])
def last_project_redirect(request):
    """GET /project/gantt/ — редирект на Гант последнего проекта или выбор."""
    project_id = request.session.get('last_project_id')
    if project_id:
        try:
            Project.objects.get(id=project_id, is_active=True)
            return redirect('project_gantt', project_id=project_id)
        except Project.DoesNotExist:
            pass
    # Нет последнего — показываем модалку выбора
    return render(request, 'gantt_selector.html', {'sidebar_active': 'gantt'})