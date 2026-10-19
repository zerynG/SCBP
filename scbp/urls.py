
from django.contrib import admin
from django.urls import path
from django.conf.urls import handler404, handler500
from django.conf import settings
from django.conf.urls.static import static
from mainboard import views

urlpatterns = [
    path('scbp_admin_panel/', admin.site.urls),
    path('login/', views.login_view, name='login'),
    path('', views.index, name='index'),
    path('profile/', views.profile, name='profile'),
    path('logout/', views.logout_view, name='logout'),
    
    path('workspaces/last/', views.workspace_last_redirect, name='workspace_last'),
    path('project/documents/', views.documents_last_redirect, name='documents_last'),
    # Создание Рабочей Области
    path('workspaces/', views.workspace_list, name='workspace_list'),
    path('api/workspaces/', views.workspaces_api, name='workspaces_api'),
    path('api/workspaces/create/', views.workspace_create, name='workspace_create'),
    path('api/workspaces/check-subdomain/', views.workspace_check_subdomain, name='workspace_check_subdomain'),
    
        # ============================================================
    # Список проектов
    # ============================================================
    path('api/projects/', views.projects_api, name='projects_api'),
    path('api/projects/create/', views.project_create, name='project_create'),
    path('api/projects/<int:project_id>/update/', views.project_update_meta, name='project_update_meta'),
    path('api/projects/<int:project_id>/hide/', views.project_hide, name='project_hide'),
    path('api/projects/<int:project_id>/delete/', views.project_delete, name='project_delete'),

    # ============================================================
    # Диаграмма Ганта
    # ============================================================
    path('project/gantt/', views.last_project_redirect, name='gantt_last'),
    path('project/<int:project_id>/gantt/', views.project_gantt, name='project_gantt'),
    path('api/projects/for-gantt/', views.project_selector_api, name='project_selector_api'),
    path('api/projects/<int:project_id>/gantt/', views.gantt_tasks_api, name='gantt_tasks_api'),
    path('api/projects/<int:project_id>/gantt/resources/', views.gantt_project_resources_api, name='gantt_resources_api'),
    path('api/projects/<int:project_id>/gantt/update-dates/', views.gantt_update_dates, name='gantt_update_dates'),

    # ============================================================
    # Ресурсы проекта
    # ============================================================
    path('api/catalog/', views.workspace_catalog_api, name='workspace_catalog_api'),
    path('api/projects/<int:project_id>/add-resource/', views.project_add_resource, name='project_add_resource'),
    path('api/projects/<int:project_id>/remove-resource/', views.project_remove_resource, name='project_remove_resource'),
    path('api/projects/<int:project_id>/update-override/', views.project_update_override, name='project_update_override'),
    path('api/projects/<int:project_id>/recalculate/', views.project_recalculate, name='project_recalculate'),
    # Проекты
    path('create_projects_resources/', views.create_project, name='create_projects_resources'),
    path('projects/', views.projects_api, name='projects_api'),
    path('project/', views.project_list_page, name='project_list'),
    path('project/<int:project_id>/', views.project_manage, name='project_manage'),
    path('project/<int:project_id>/generate_documents/', views.generate_documents, name='generate_documents'),
    # Новые маршруты для скачивания
    path('project/<int:project_id>/download/proposal/pdf/', views.download_proposal_pdf, name='download_proposal_pdf'),
    path('project/<int:project_id>/download/proposal/excel/', views.download_proposal_excel, name='download_proposal_excel'),
    path('project/<int:project_id>/download/proposal/word/', views.download_proposal_word, name='download_proposal_word'),
    path('project/<int:project_id>/download/nma/pdf/', views.download_nma_pdf, name='download_nma_pdf'),
    path('project/<int:project_id>/download/nma/excel/', views.download_nma_excel, name='download_nma_excel'),
    path('project/<int:project_id>/download/nma/word/', views.download_nma_word, name='download_nma_word'),
    path('project/<int:project_id>/delete/', views.delete_project, name='delete_project'),
    
    # Управление ресурсами проекта
    path('project/<int:project_id>/add_employee/', views.add_employee, name='add_employee'),
    path('project/<int:project_id>/add_contractor/', views.add_contractor, name='add_contractor'),
    path('project/<int:project_id>/add_subcontractor/', views.add_subcontractor, name='add_subcontractor'),
    path('project/<int:project_id>/add_equipment/', views.add_equipment, name='add_equipment'),

    # Удаление ресурсов
    path('project/<int:project_id>/delete_employee/<int:employee_id>/', views.delete_employee, name='delete_employee'),
    path('project/<int:project_id>/delete_contractor/<int:contractor_id>/', views.delete_contractor, name='delete_contractor'),
    path('project/<int:project_id>/delete_subcontractor/<int:subcontractor_id>/', views.delete_subcontractor, name='delete_subcontractor'),
    path('project/<int:project_id>/delete_equipment/<int:equipment_id>/', views.delete_equipment, name='delete_equipment'),
    
    # Сводная таблица: сохранение параметров ресурса
    path('project/<int:project_id>/update_resource/', views.update_resource_override, name='update_resource'),
    path('project/<int:project_id>/recalculate/', views.recalculate_project, name='recalculate_project'),

    # Расписание сотрудников (Диаграмма Ганта) — глобальное
    path('gantt/', views.employee_gantt, name='employee_gantt'),
    path('gantt/add/', views.add_schedule_entry, name='add_schedule_entry'),
    path('gantt/delete/<int:entry_id>/', views.delete_schedule_entry, name='delete_schedule_entry'),

    # Реестр услуг (глобальный) — отдельная страница управления
    path('services/', views.services_page, name='services_page'),
    path('services/add/', views.add_service, name='add_service'),
    path('services/<int:service_id>/edit/', views.edit_service, name='edit_service'),
    path('services/delete/<int:service_id>/', views.delete_service, name='delete_service'),
    path('services/api/', views.services_api, name='services_api'),

    # ============================================================
    # Каталог ресурсов
    # ============================================================
    path('resources/', views.resources_page, name='resources_page'),
    path('api/resources/', views.resources_api, name='resources_api'),
    path('api/sidebar/counters/', views.sidebar_counters_api, name='sidebar_counters'),

    # Employees
    path('api/resources/employees/create/', views.employee_create, name='employee_create'),
    path('api/resources/employees/<int:pk>/update/', views.employee_update, name='employee_update'),
    path('api/resources/employees/<int:pk>/delete/', views.employee_delete, name='employee_delete'),

    # Contractors (исполнители)
    path('api/resources/contractors/create/', views.contractor_create, name='contractor_create'),
    path('api/resources/contractors/<int:pk>/update/', views.contractor_update, name='contractor_update'),
    path('api/resources/contractors/<int:pk>/delete/', views.contractor_delete, name='contractor_delete'),

    # Subcontractors
    path('api/resources/subcontractors/create/', views.subcontractor_create, name='subcontractor_create'),
    path('api/resources/subcontractors/<int:pk>/update/', views.subcontractor_update, name='subcontractor_update'),
    path('api/resources/subcontractors/<int:pk>/delete/', views.subcontractor_delete, name='subcontractor_delete'),

    # Customers
    path('api/resources/customers/create/', views.customer_create, name='customer_create'),
    path('api/resources/customers/<int:pk>/update/', views.customer_update, name='customer_update'),
    path('api/resources/customers/<int:pk>/delete/', views.customer_delete, name='customer_delete'),

    # Equipment
    path('api/resources/equipment/create/', views.equipment_create, name='equipment_create'),
    path('api/resources/equipment/<int:pk>/update/', views.equipment_update, name='equipment_update'),
    path('api/resources/equipment/<int:pk>/delete/', views.equipment_delete, name='equipment_delete'),

    # ============================================================
    # Рабочие области — обзор
    # ============================================================
    path('workspaces/<int:workspace_id>/', views.workspace_detail, name='workspace_detail'),
    path('api/workspaces/<int:workspace_id>/', views.workspace_detail_api, name='workspace_detail_api'),
    path('api/workspaces/<int:workspace_id>/toggle/', views.workspace_toggle, name='workspace_toggle'),
    path('api/workspaces/<int:workspace_id>/delete/', views.workspace_delete, name='workspace_delete'),
    path('api/workspaces/<int:workspace_id>/update/', views.workspace_update, name='workspace_update'),    
]

# Обработчики ошибок
handler404 = 'mainboard.views.custom_404'
handler500 = 'mainboard.views.custom_500'

# Обслуживание статических файлов в режиме разработки
if settings.DEBUG:
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns
    urlpatterns += staticfiles_urlpatterns()
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
