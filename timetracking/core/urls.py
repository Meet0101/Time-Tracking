from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    # Auth
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
    path('signup/', views.signup, name='signup'),
    
    # Main
    path('', views.dashboard, name='home'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('projects/', views.projects, name='projects'),
    path('projects/<int:project_id>/', views.project_detail, name='project_detail'),
    path('tasks/', views.tasks, name='tasks'),
    path('tracking/', views.tracking, name='tracking'),
    path('reports/', views.reports, name='reports'),
    path('data-health/', views.data_health, name='data_health'),
    path('enhancements/', views.enhancements, name='enhancements'),
    path('teams/', views.teams, name='teams'),
    path('teams/<int:team_id>/', views.team_detail, name='team_detail'),
    path('billing/', views.billing, name='billing'),
    path('billing/print/<int:invoice_id>/', views.print_invoice, name='print_invoice'),
    path('billing/razorpay/create-order/', views.razorpay_create_order, name='razorpay_create_order'),
    path('billing/razorpay/verify/', views.razorpay_verify_payment, name='razorpay_verify_payment'),
    path(
        'billing/razorpay/return/<int:invoice_id>/',
        views.razorpay_payment_return,
        name='razorpay_payment_return',
    ),
    path('notifications/', views.notifications, name='notifications'),
    path('profile/', views.profile_settings, name='profile_settings'),
    path('profile/activity/export/csv/', views.profile_activity_export_csv, name='profile_activity_export_csv'),
    path('profile/activity/export/pdf/', views.profile_activity_export_pdf, name='profile_activity_export_pdf'),
    
    # AJAX / API for Timer and Kanban
    path('api/timer/start/', views.api_timer_start, name='api_timer_start'),
    path('api/timer/stop/', views.api_timer_stop, name='api_timer_stop'),
    path('api/task/update-status/', views.api_task_update_status, name='api_task_update_status'),
    path('api/timer/idle-ping/', views.api_idle_ping, name='api_idle_ping'),
    path('api/notifications/feed/', views.api_notifications_feed, name='api_notifications_feed'),
    
    # Password Reset
    path('password-reset/', auth_views.PasswordResetView.as_view(template_name='core/password_reset.html'), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='core/password_reset_done.html'), name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='core/password_reset_confirm.html'), name='password_reset_confirm'),
    path('password-reset-complete/', auth_views.PasswordResetCompleteView.as_view(template_name='core/password_reset_complete.html'), name='password_reset_complete'),
]
