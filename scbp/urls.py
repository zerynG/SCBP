
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
    
    # Проекты
    path('create_projects_resources/', views.create_project, name='create_projects_resources'),
    path('projects/', views.projects_api, name='projects_api'),
    path('project/', views.project_list, name='project_list'),
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
]

# Обработчики ошибок
handler404 = 'mainboard.views.custom_404'
handler500 = 'mainboard.views.custom_500'

# Обслуживание статических файлов в режиме разработки
if settings.DEBUG:
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns
    urlpatterns += staticfiles_urlpatterns()
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
