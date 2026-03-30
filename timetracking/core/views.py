import base64
import hashlib
import hmac
import json
import secrets
import csv
import urllib.error
import urllib.request
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from email.mime.image import MIMEImage
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncDate
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_POST
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.timesince import timesince
from django.core.mail import EmailMultiAlternatives

from .forms import (
    LoginForm,
    ManualTimeEntryForm,
    ModuleForm,
    OTPVerifyForm,
    ProjectForm,
    SignupForm,
    TaskForm,
)
from .models import Invoice, Module, Notification, Project, Task, Team, TimeLog, User
from .access import is_superadmin, projects_qs_for, tasks_qs_for


OTP_SESSION_KEY = "email_otp"
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_FLOW_SESSION_KEY = "otp_flow_type"


def _razorpay_configured() -> bool:
    kid = (getattr(settings, "RAZORPAY_KEY_ID", "") or "").strip()
    sec = (getattr(settings, "RAZORPAY_KEY_SECRET", "") or "").strip()
    return bool(kid and sec)


def _razorpay_basic_auth_header() -> str:
    raw = f"{settings.RAZORPAY_KEY_ID}:{settings.RAZORPAY_KEY_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _razorpay_create_order_api(amount_paise: int, currency: str, receipt: str, notes: dict) -> dict:
    body = json.dumps(
        {"amount": amount_paise, "currency": currency, "receipt": receipt, "notes": notes},
        separators=(",", ":"),
    ).encode()
    req = urllib.request.Request(
        "https://api.razorpay.com/v1/orders",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": _razorpay_basic_auth_header(),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        err = exc.read().decode() or str(exc)
        raise ValueError(err) from exc


def _razorpay_verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
    message = f"{order_id}|{payment_id}".encode()
    secret = (settings.RAZORPAY_KEY_SECRET or "").encode()
    expected = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


RAZORPAY_RETURN_TOKEN_SALT = "timetrack.razorpay.return"
RAZORPAY_RETURN_TOKEN_MAX_AGE = 7200


def _razorpay_sign_return_token(user_id: int, invoice_id: int, order_id: str) -> str:
    return signing.dumps(
        {"u": user_id, "i": invoice_id, "o": order_id},
        salt=RAZORPAY_RETURN_TOKEN_SALT,
    )


def _razorpay_parse_return_token(token: str) -> dict | None:
    try:
        return signing.loads(
            token,
            salt=RAZORPAY_RETURN_TOKEN_SALT,
            max_age=RAZORPAY_RETURN_TOKEN_MAX_AGE,
        )
    except (signing.BadSignature, signing.SignatureExpired):
        return None


def _razorpay_return_token_payload(request, invoice: Invoice) -> dict | None:
    rt = request.GET.get("rt")
    if not rt:
        return None
    data = _razorpay_parse_return_token(rt)
    if not data or data.get("i") != invoice.id:
        return None
    return data


def _razorpay_try_mark_invoice_paid(invoice, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str):
    """Returns (success: bool, error_message: str | None)."""
    if invoice.payment_status == Invoice.PAYMENT_PAID:
        return True, None
    if not invoice.razorpay_order_id or invoice.razorpay_order_id != razorpay_order_id:
        return False, "Order does not match this invoice. Start payment again from Billing."
    if not _razorpay_verify_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
        invoice.payment_status = Invoice.PAYMENT_FAILED
        invoice.save(update_fields=["payment_status"])
        return False, "Invalid payment signature."
    invoice.payment_status = Invoice.PAYMENT_PAID
    invoice.razorpay_order_id = razorpay_order_id
    invoice.razorpay_payment_id = razorpay_payment_id
    invoice.paid_at = timezone.now()
    invoice.save(update_fields=["payment_status", "razorpay_order_id", "razorpay_payment_id", "paid_at"])
    return True, None


def _generate_otp() -> str:
    # 6 digits OTP
    return f"{secrets.randbelow(1000000):06d}"


def _set_otp_in_session(request, email: str, otp: str):
    request.session[OTP_SESSION_KEY] = {
        "email": email,
        "otp": otp,
        "expires_ts": (timezone.now() + timedelta(minutes=10)).timestamp(),
        "attempts": 0,
        "sent_ts": timezone.now().timestamp(),
    }


def _get_otp_from_session(request, email: str):
    data = request.session.get(OTP_SESSION_KEY) or {}
    if data.get("email") != email:
        return None
    expires_ts = data.get("expires_ts")
    if expires_ts is None:
        return None
    if timezone.now().timestamp() > expires_ts:
        return None
    return data.get("otp")


def _get_otp_meta(request, email: str):
    data = request.session.get(OTP_SESSION_KEY) or {}
    if data.get("email") != email:
        return None
    return data


def _seconds_until_resend(request, email: str) -> int:
    data = _get_otp_meta(request, email)
    if not data:
        return 0
    sent_ts = data.get("sent_ts")
    if sent_ts is None:
        return 0
    elapsed = timezone.now().timestamp() - sent_ts
    remaining = int(OTP_RESEND_COOLDOWN_SECONDS - elapsed)
    return remaining if remaining > 0 else 0


def _send_otp_email(to_email: str, otp: str):
    # Demo/local accounts ke liye OTP terminal me show karo.
    # Real users ke liye SMTP email flow continue rahega.
    if to_email.lower().endswith("@timetrack.local"):
        print(f"[DEMO OTP] email={to_email} otp={otp}")
        return

    html_message = render_to_string(
        "core/email_otp.html",
        {"otp": otp, "email": to_email, "expires_minutes": 10},
    )
    email = EmailMultiAlternatives(
        subject="TimeTrack — email verification code",
        body="Your OTP is in the email. Please verify to continue.",
        from_email=settings.EMAIL_HOST_USER,
        to=[to_email],
    )
    email.attach_alternative(html_message, "text/html")

    # Logo attachment (SVG) so email me image attachment jaayegi.
    try:
        logo_path = Path(settings.BASE_DIR) / "static" / "images" / "timetrack-logo.svg"
        if logo_path.exists():
            email.attach(str(logo_path.name), logo_path.read_bytes(), "image/svg+xml")
    except Exception:
        pass

    try:
        email.send()
    except Exception:
        # SMTP issues ke case me app crash nahi hona chahiye.
        # (OTP verification flow DB/session pe based rahega)
        pass


def _send_welcome_email(to_email: str):
    html_message = render_to_string(
        "core/email_welcome.html",
        {"email": to_email},
    )
    email = EmailMultiAlternatives(
        subject="Welcome to TimeTrack",
        body="Thanks for registering with TimeTrack. You can log in and start tracking time.",
        from_email=settings.EMAIL_HOST_USER,
        to=[to_email],
    )
    email.attach_alternative(html_message, "text/html")
    try:
        banner_path = Path(settings.BASE_DIR) / "static" / "images" / "welcome-timetrack-banner.svg"
        if banner_path.exists():
            email.attach(str(banner_path.name), banner_path.read_bytes(), "image/svg+xml")
            inline_img = MIMEImage(banner_path.read_bytes(), _subtype="svg+xml")
            inline_img.add_header("Content-ID", "<welcome_banner>")
            inline_img.add_header("Content-Disposition", "inline", filename=banner_path.name)
            email.attach(inline_img)
    except Exception:
        pass
    try:
        email.send()
    except Exception:
        pass


def _notify_admins_and_managers(
    message: str,
    exclude_user_id=None,
    redirect_url="",
    team_id=None,
):
    recipients = User.objects.filter(role__in=["admin", "manager"], status="active")
    if team_id is not None:
        recipients = recipients.filter(team_id=team_id)
    if exclude_user_id:
        recipients = recipients.exclude(id=exclude_user_id)
    notifications = [
        Notification(user=user, message=message, redirect_url=redirect_url, is_read=False)
        for user in recipients
    ]
    if notifications:
        Notification.objects.bulk_create(notifications)


# Auth Views
def user_login(request):
    show_otp = request.GET.get("verify") == "1"
    resend = request.GET.get("resend") == "1"

    initial_email = request.GET.get("email") or request.POST.get("email") or request.session.get("otp_pending_email") or ""

    form = LoginForm(initial={"email": initial_email})
    otp_form = OTPVerifyForm()

    if request.method == "POST":
        # OTP verify flow (login page pe hi)
        if request.POST.get("otp"):
            otp_form = OTPVerifyForm(request.POST)
            form = LoginForm(request.POST, initial={"email": request.POST.get("email")})
            email = request.POST.get("email")
            if email and otp_form.is_valid():
                otp = otp_form.cleaned_data["otp"]
                user = User.objects.filter(email=email).first()
                if not user:
                    messages.error(request, "Invalid email address.")
                    show_otp = True
                else:
                    expected = _get_otp_from_session(request, email)
                    if not expected or expected != otp:
                        otp_data = request.session.get(OTP_SESSION_KEY, {})
                        if otp_data.get("email") == email:
                            otp_data["attempts"] = int(otp_data.get("attempts", 0)) + 1
                            request.session[OTP_SESSION_KEY] = otp_data
                        attempts = int((request.session.get(OTP_SESSION_KEY) or {}).get("attempts", 0))
                        if attempts >= OTP_MAX_ATTEMPTS:
                            request.session.pop(OTP_SESSION_KEY, None)
                            messages.error(request, "Too many invalid attempts. Please resend OTP.")
                        else:
                            remaining = OTP_MAX_ATTEMPTS - attempts
                            messages.error(
                                request,
                                f"Invalid or expired OTP. {remaining} attempt(s) left.",
                            )
                        show_otp = True
                    else:
                        if user.status == "inactive":
                            user.status = "active"
                            user.save()
                            if request.session.get(OTP_FLOW_SESSION_KEY) == "signup":
                                _send_welcome_email(user.email)
                        request.session.pop(OTP_SESSION_KEY, None)
                        request.session.pop("otp_pending_email", None)
                        request.session.pop(OTP_FLOW_SESSION_KEY, None)
                        login(request, user)
                        messages.success(request, "Email verified. You can use TimeTrack now.")
                        return redirect("dashboard")
            else:
                show_otp = True

            return render(
                request,
                "core/login.html",
                {"form": form, "otp_form": otp_form, "show_otp": True},
            )

        # Normal email/password login
        form = LoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            password = form.cleaned_data["password"]
            user = User.objects.filter(email=email).first()

            if not user or not user.check_password(password):
                messages.error(request, "Invalid email or password.")
            else:
                if user.status == "active":
                    otp = _generate_otp()
                    _set_otp_in_session(request, email, otp)
                    request.session["otp_pending_email"] = email
                    request.session[OTP_FLOW_SESSION_KEY] = "login"
                    _send_otp_email(email, otp)
                    messages.success(request, "OTP sent to your email. Please verify to continue login.")
                    return redirect(f"{reverse('login')}?verify=1&email={email}")
                if user.status == "inactive":
                    otp = _generate_otp()
                    _set_otp_in_session(request, email, otp)
                    request.session["otp_pending_email"] = email
                    request.session[OTP_FLOW_SESSION_KEY] = "signup"
                    _send_otp_email(email, otp)
                    messages.error(request, "Please verify your email. Enter OTP to continue.")
                    return redirect(f"{reverse('login')}?verify=1&email={email}")
                if user.status == "blocked":
                    messages.error(request, "Your account is blocked. Please contact support.")
                if user.status == "deleted":
                    messages.error(request, "This account has been deleted.")

    # GET flow: OTP ensure/resend
    if show_otp and initial_email:
        user = User.objects.filter(email=initial_email).first()
        if user:
            expected = _get_otp_from_session(request, initial_email)
            if resend or expected is None:
                remaining = _seconds_until_resend(request, initial_email)
                if resend and remaining > 0:
                    messages.error(request, f"Please wait {remaining}s before resending OTP.")
                else:
                    otp = _generate_otp()
                    _set_otp_in_session(request, initial_email, otp)
                    _send_otp_email(initial_email, otp)
                    messages.success(request, f"OTP sent to {initial_email}. Please verify.")

    return render(request, "core/login.html", {"form": form, "otp_form": otp_form, "show_otp": show_otp})

def user_logout(request):
    logout(request)
    return redirect('login')

def signup(request):
    if request.method == 'POST':
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.set_password(form.cleaned_data['password'])
            user.role = form.cleaned_data["role"]
            user.team = form.cleaned_data["team"]
            user.status = "inactive"
            user.save()
            otp = _generate_otp()
            _set_otp_in_session(request, user.email, otp)
            request.session["otp_pending_email"] = user.email
            request.session[OTP_FLOW_SESSION_KEY] = "signup"
            _send_otp_email(user.email, otp)

            messages.success(
                request,
                "OTP sent to your email. Please verify OTP to activate your account.",
            )
            return redirect(f"{reverse('login')}?verify=1&email={user.email}")
    else:
        form = SignupForm()
    return render(request, 'core/signup.html', {'form': form})

# Main Feature Views
@login_required
def dashboard(request):
    # Developers: personal stats. Admin/Manager: org-wide totals (matches Projects/Reports view).
    is_lead = request.user.role in ("admin", "manager") or is_superadmin(request.user)
    user_tasks_qs = tasks_qs_for(request.user)
    projects_count = projects_qs_for(request.user).count()
    time_log_filter = {"approval_status": "approved"}
    if not is_lead:
        time_log_filter["user"] = request.user
    elif not is_superadmin(request.user):
        time_log_filter["task__module__project__team_id"] = request.user.team_id

    active_tasks = user_tasks_qs.filter(status='in_progress').count()
    pending_tasks = user_tasks_qs.filter(status='todo').count()
    completed_tasks = user_tasks_qs.filter(status='done').count()
    total_hours_duration = TimeLog.objects.filter(**time_log_filter).aggregate(total=Sum('total_time'))['total'] or timedelta(0)
    total_hours = total_hours_duration.total_seconds() / 3600
    
    # Chart.js data
    # Completion chart
    statuses = user_tasks_qs.values('status').annotate(count=Count('id'))
    status_labels = [s['status'].replace('_', ' ').capitalize() for s in statuses]
    status_counts = [s['count'] for s in statuses]
    
    # Task assigned to current user
    user_tasks = Task.objects.filter(assigned_to=request.user)

    # Productivity chart: last 7 days total hours
    today = timezone.now().date()
    start_date = today - timedelta(days=6)
    daily_qs_base = TimeLog.objects.filter(
        start_time__date__gte=start_date,
        start_time__date__lte=today,
        approval_status="approved",
    )
    if not is_lead:
        daily_qs_base = daily_qs_base.filter(user=request.user)
    elif not is_superadmin(request.user):
        daily_qs_base = daily_qs_base.filter(task__module__project__team_id=request.user.team_id)
    daily_qs = (
        daily_qs_base.annotate(day=TruncDate('start_time'))
        .values('day')
        .annotate(total=Sum('total_time'))
        .order_by('day')
    )
    daily_map = {row['day']: row['total'] for row in daily_qs}
    productivity_labels = []
    productivity_hours = []
    for i in range(7):
        d = start_date + timedelta(days=i)
        productivity_labels.append(d.strftime('%a'))  # Mon, Tue, ...
        td = daily_map.get(d)
        productivity_hours.append(round((td.total_seconds() / 3600) if td else 0, 2))
    
    return render(request, 'core/dashboard.html', {
        'projects_count': projects_count,
        'active_tasks': active_tasks,
        'pending_tasks': pending_tasks,
        'completed_tasks': completed_tasks,
        'total_hours': round(total_hours, 2),
        'status_labels': status_labels,
        'status_counts': status_counts,
        'user_tasks': user_tasks,
        'productivity_labels': productivity_labels,
        'productivity_hours': productivity_hours,
        'dashboard_scope_org': is_lead,
    })

@login_required
def projects(request):
    can_manage = request.user.role in ['admin', 'manager'] or is_superadmin(request.user)
    focused_project_id = request.GET.get("project_id")

    projects_list = (
        projects_qs_for(request.user)
        .prefetch_related('modules')
        .annotate(
            total_tasks=Count("modules__tasks"),
            done_tasks=Count("modules__tasks", filter=Q(modules__tasks__status="done")),
            in_progress_tasks=Count("modules__tasks", filter=Q(modules__tasks__status="in_progress")),
            todo_tasks=Count("modules__tasks", filter=Q(modules__tasks__status="todo")),
            overdue_tasks=Count(
                "modules__tasks",
                filter=Q(modules__tasks__deadline__lt=timezone.now().date()) & ~Q(modules__tasks__status="done"),
            ),
        )
        .order_by('-created_at')
    )

    edit_project = None
    edit_project_form = None
    if request.method == 'GET' and can_manage and request.GET.get('edit'):
        edit_project = get_object_or_404(projects_qs_for(request.user), id=request.GET.get('edit'))
        edit_project_form = ProjectForm(instance=edit_project)

    create_project_form = ProjectForm()
    module_form = ModuleForm()

    if request.method == 'POST':
        action = request.POST.get('action', 'create_project')

        if action in ['create_project', 'update_project', 'delete_project', 'create_module'] and not can_manage:
            messages.error(request, "You don't have permission to manage projects.")
            return redirect('projects')

        if action == 'create_project':
            create_project_form = ProjectForm(request.POST)
            if create_project_form.is_valid():
                project = create_project_form.save(commit=False)
                project.created_by = request.user
                # Project.team is required; never access project.team before it is set (can raise RelatedObjectDoesNotExist).
                team = request.user.team
                if team is None:
                    team = Team.objects.filter(code="default-team").first()
                project.team = team
                project.save()
                messages.success(request, "Project created successfully.")
                return redirect('projects')

        elif action == 'update_project':
            project_id = request.POST.get('project_id')
            edit_project = get_object_or_404(projects_qs_for(request.user), id=project_id)
            edit_project_form = ProjectForm(request.POST, instance=edit_project)
            if edit_project_form.is_valid():
                edit_project_form.save()
                messages.success(request, "Project updated successfully.")
                return redirect('projects')

        elif action == 'delete_project':
            project_id = request.POST.get('project_id')
            project = get_object_or_404(projects_qs_for(request.user), id=project_id)
            project.delete()
            messages.success(request, "Project deleted successfully.")
            return redirect('projects')

        elif action == 'create_module':
            module_form = ModuleForm(request.POST)
            if module_form.is_valid():
                mod = module_form.save(commit=False)
                # Prevent cross-team module attach.
                if not is_superadmin(request.user) and mod.project.team_id != request.user.team_id:
                    messages.error(request, "You don't have permission to add modules to that project.")
                    return redirect("projects")
                mod.save()
                messages.success(request, "Module created successfully.")
                return redirect('projects')

    return render(request, 'core/projects.html', {
        'projects': projects_list,
        'can_manage': can_manage,
        'edit_project': edit_project,
        'edit_project_form': edit_project_form,
        'create_project_form': create_project_form,
        'module_form': module_form,
        'focused_project_id': int(focused_project_id) if focused_project_id and focused_project_id.isdigit() else None,
    })


@login_required
def teams(request):
    if not is_superadmin(request.user):
        messages.error(request, "You don't have permission.")
        return redirect("dashboard")

    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "create":
            name = (request.POST.get("name") or "").strip()
            code = (request.POST.get("code") or "").strip()
            if not name or not code:
                messages.error(request, "Team name and code are required.")
                return redirect("teams")
            if Team.objects.filter(code=code).exists():
                messages.error(request, "Team code already exists.")
                return redirect("teams")
            Team.objects.create(name=name, code=code, created_by=request.user)
            messages.success(request, "Team created.")
            return redirect("teams")
        if action == "delete":
            team_id = request.POST.get("team_id")
            team = get_object_or_404(Team, id=team_id)
            if team.members.exists() or team.projects.exists():
                messages.error(request, "Team has members/projects. Move them first.")
                return redirect("teams")
            team.delete()
            messages.success(request, "Team deleted.")
            return redirect("teams")

    teams_qs = Team.objects.prefetch_related("members", "projects").order_by("name")
    return render(request, "core/teams.html", {"teams": teams_qs})


@login_required
def team_detail(request, team_id):
    if not is_superadmin(request.user):
        messages.error(request, "You don't have permission.")
        return redirect("dashboard")

    team = get_object_or_404(Team, id=team_id)
    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "move_user":
            user_id = request.POST.get("user_id")
            target_team_id = request.POST.get("target_team_id")
            u = get_object_or_404(User, id=user_id)
            target = get_object_or_404(Team, id=target_team_id)
            u.team = target
            u.save(update_fields=["team"])
            messages.success(request, f"Moved {u.email} to {target.name}.")
            return redirect("team_detail", team_id=team.id)
        if action == "rename":
            name = (request.POST.get("name") or "").strip()
            code = (request.POST.get("code") or "").strip()
            if not name or not code:
                messages.error(request, "Name and code are required.")
                return redirect("team_detail", team_id=team.id)
            if Team.objects.exclude(id=team.id).filter(code=code).exists():
                messages.error(request, "Team code already exists.")
                return redirect("team_detail", team_id=team.id)
            team.name = name
            team.code = code
            team.save(update_fields=["name", "code"])
            messages.success(request, "Team updated.")
            return redirect("team_detail", team_id=team.id)

    members = User.objects.filter(team=team).order_by("role", "email")
    projects = Project.objects.filter(team=team).order_by("-created_at")
    other_teams = Team.objects.exclude(id=team.id).order_by("name")
    return render(
        request,
        "core/team_detail.html",
        {"team": team, "members": members, "projects": projects, "other_teams": other_teams},
    )


@login_required
def project_detail(request, project_id):
    project = get_object_or_404(
        projects_qs_for(request.user).select_related("created_by"),
        id=project_id,
    )
    tasks_qs = tasks_qs_for(request.user).filter(module__project=project)
    logs_qs = TimeLog.objects.filter(task__module__project=project, approval_status="approved")
    if not is_superadmin(request.user):
        logs_qs = logs_qs.filter(task__module__project__team_id=request.user.team_id)
    today = timezone.now().date()

    total_tasks = tasks_qs.count()
    done_tasks = tasks_qs.filter(status="done").count()
    in_progress_tasks = tasks_qs.filter(status="in_progress").count()
    todo_tasks = tasks_qs.filter(status="todo").count()
    overdue_tasks = tasks_qs.filter(deadline__lt=today).exclude(status="done").count()
    completion_pct = round((done_tasks / total_tasks) * 100, 1) if total_tasks else 0.0

    total_time = logs_qs.aggregate(total=Sum("total_time"))["total"] or timedelta(0)
    total_hours = round(total_time.total_seconds() / 3600, 2)
    approved_logs = logs_qs.count()

    latest_logs = logs_qs.select_related("task", "user").order_by("-start_time")[:12]
    team_progress = (
        tasks_qs.values("assigned_to__email")
        .annotate(total=Count("id"), done=Count("id", filter=Q(status="done")))
        .order_by("assigned_to__email")
    )
    module_progress = (
        project.modules.values("name")
        .annotate(total=Count("tasks"), done=Count("tasks", filter=Q(tasks__status="done")))
        .order_by("name")
    )

    return render(
        request,
        "core/project_detail.html",
        {
            "project": project,
            "total_tasks": total_tasks,
            "done_tasks": done_tasks,
            "in_progress_tasks": in_progress_tasks,
            "todo_tasks": todo_tasks,
            "overdue_tasks": overdue_tasks,
            "completion_pct": completion_pct,
            "total_hours": total_hours,
            "approved_logs": approved_logs,
            "latest_logs": latest_logs,
            "team_progress": team_progress,
            "module_progress": module_progress,
        },
    )


def _apply_task_form_querysets(form, request, can_assign):
    """Developers + active users in assign list; avoid empty queryset when no devs yet."""
    if can_assign:
        assign_qs = (
            User.objects.filter(status="active")
            .filter(Q(role="developer") | Q(pk=request.user.pk))
            .filter(team_id=request.user.team_id)
            .distinct()
        )
        form.fields["assigned_to"].queryset = assign_qs
        form.fields["assigned_to"].required = False
    else:
        form.fields["assigned_to"].queryset = User.objects.filter(id=request.user.id)
        form.fields["assigned_to"].required = True
    if is_superadmin(request.user):
        mod_qs = Module.objects.select_related("project").order_by("project__name", "name")
    else:
        mod_qs = Module.objects.select_related("project").filter(project__team_id=request.user.team_id).order_by("project__name", "name")
    form.fields["module"].queryset = mod_qs
    form.fields["module"].empty_label = "Select project module"


def _create_quick_task_for_user(user, title: str) -> Task:
    """Ad-hoc task in the user's personal workspace (manual log, live timer)."""
    title = (title or "").strip()
    # Superadmin / newly created users might not have team set yet; fall back safely.
    team = user.team
    if team is None:
        team = Team.objects.filter(code="default-team").first()
    personal_project, _ = Project.objects.get_or_create(
        name=f"{user.email} Personal Workspace",
        created_by=user,
        defaults={
            "description": "Auto-created personal project for time entries.",
            "start_date": timezone.now().date(),
            "team": team,
        },
    )
    if personal_project.team_id is None:
        personal_project.team = team
        personal_project.save(update_fields=["team"])
    personal_module, _ = Module.objects.get_or_create(
        project=personal_project,
        name="General",
    )
    return Task.objects.create(
        module=personal_module,
        assigned_to=user,
        title=title,
        description="Task created from time entry.",
        status="in_progress",
        priority="medium",
    )


@login_required
def tasks(request):
    can_assign = request.user.role in ["admin", "manager"] or is_superadmin(request.user)
    selected_project_id = request.GET.get("project")
    selected_task_id = request.GET.get("task")
    selected_status = request.GET.get("status")
    tasks_list = tasks_qs_for(request.user)
    if selected_project_id and selected_project_id.isdigit():
        tasks_list = tasks_list.filter(module__project_id=int(selected_project_id))
    if selected_status in {"todo", "in_progress", "done"}:
        tasks_list = tasks_list.filter(status=selected_status)

    todo_tasks = tasks_list.filter(status='todo')
    in_progress_tasks = tasks_list.filter(status='in_progress')
    done_tasks = tasks_list.filter(status='done')

    open_new_task_modal = False
    open_edit_task_modal = False
    modules_exist = Module.objects.exists()
    tasks_querysuffix = ("?" + request.GET.urlencode()) if request.GET else ""

    # Forms: separate create/edit to avoid DOM id collisions.
    edit_form = TaskForm(prefix="edit")
    _apply_task_form_querysets(edit_form, request, can_assign)

    if request.method == "POST" and request.POST.get("delete_task"):
        task_id = request.POST.get("task_id")
        task = get_object_or_404(tasks_qs_for(request.user), id=task_id)
        task.delete()
        messages.success(request, "Task deleted.")
        return redirect(f"{reverse('tasks')}{tasks_querysuffix}")

    if request.method == "POST" and request.POST.get("update_task"):
        task_id = request.POST.get("task_id")
        task = get_object_or_404(tasks_qs_for(request.user), id=task_id)
        edit_form = TaskForm(request.POST, instance=task, prefix="edit")
        _apply_task_form_querysets(edit_form, request, can_assign)
        if edit_form.is_valid():
            updated = edit_form.save(commit=False)
            if not can_assign:
                updated.assigned_to = request.user
            elif not updated.assigned_to_id:
                updated.assigned_to = request.user
            updated.save()
            messages.success(request, "Task updated.")
            return redirect(f"{reverse('tasks')}{tasks_querysuffix}")
        open_edit_task_modal = True

    if request.method == "POST" and request.POST.get("create_task"):
        form = TaskForm(request.POST)
        _apply_task_form_querysets(form, request, can_assign)
        if form.is_valid():
            task = form.save(commit=False)
            if not can_assign:
                task.assigned_to = request.user
            elif not task.assigned_to_id:
                task.assigned_to = request.user
            task.save()

            if task.assigned_to_id:
                Notification.objects.create(
                    user=task.assigned_to,
                    message=f"New task assigned: {task.title}",
                    redirect_url=f"{reverse('tasks')}?task={task.id}",
                    is_read=False,
                )

            messages.success(request, "Task created successfully.")
            return redirect(f"{reverse('tasks')}{tasks_querysuffix}")
        open_new_task_modal = True
    else:
        form = TaskForm()
        _apply_task_form_querysets(form, request, can_assign)

    return render(request, 'core/tasks.html', {
        'todo': todo_tasks,
        'in_progress': in_progress_tasks,
        'done': done_tasks,
        'form': form,
        'edit_form': edit_form,
        'can_assign': can_assign,
        'open_new_task_modal': open_new_task_modal,
        'open_edit_task_modal': open_edit_task_modal,
        'modules_exist': modules_exist,
        'tasks_querysuffix': tasks_querysuffix,
        'selected_project_id': int(selected_project_id) if selected_project_id and selected_project_id.isdigit() else None,
        'selected_task_id': int(selected_task_id) if selected_task_id and selected_task_id.isdigit() else None,
        'selected_status': selected_status if selected_status in {"todo", "in_progress", "done"} else "",
    })

@login_required
def tracking(request):
    logs = TimeLog.objects.select_related("task__module__project").filter(user=request.user).order_by('-start_time')
    active_log = TimeLog.objects.filter(user=request.user, end_time__isnull=True).first()

    user_tasks = Task.objects.filter(assigned_to=request.user, status__in=['todo', 'in_progress'])
    manual_tasks = Task.objects.filter(assigned_to=request.user)

    manual_form = ManualTimeEntryForm(tasks_qs=manual_tasks)

    can_approve = request.user.role in ["admin", "manager"] or is_superadmin(request.user)
    pending_manual_logs_qs = TimeLog.objects.select_related("task", "user").filter(
        entry_type="manual",
        approval_status="pending",
    )
    if can_approve and not is_superadmin(request.user):
        pending_manual_logs_qs = pending_manual_logs_qs.filter(
            task__module__project__team_id=request.user.team_id
        )
    pending_manual_logs = pending_manual_logs_qs.order_by("-start_time") if can_approve else TimeLog.objects.none()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'manual_log':
            manual_form = ManualTimeEntryForm(request.POST, tasks_qs=manual_tasks)
            if manual_form.is_valid():
                task = manual_form.cleaned_data['task']
                custom_task_title = (manual_form.cleaned_data.get('custom_task_title') or "").strip()
                start_time = manual_form.cleaned_data['start_time']
                end_time = manual_form.cleaned_data['end_time']

                if task is None and custom_task_title:
                    task = _create_quick_task_for_user(request.user, custom_task_title)

                if end_time < start_time:
                    messages.error(request, "End time start time se pehle nahi ho sakta.")
                else:
                    log = TimeLog.objects.create(
                        task=task,
                        user=request.user,
                        start_time=start_time,
                        end_time=end_time,
                        entry_type="manual",
                        approval_status="pending",
                    )
                    # If user logs time on a todo task, move it to in_progress
                    if task.status == 'todo':
                        task.status = 'in_progress'
                        task.save()

                    _notify_admins_and_managers(
                        f"Manual time log approval required: {log.user.email} - {log.task.title}",
                        exclude_user_id=request.user.id,
                        redirect_url=reverse("tracking"),
                        team_id=log.task.module.project.team_id,
                    )
                    messages.success(request, "Manual time log submitted for approval.")
                    return redirect('tracking')
        elif action in ["approve_manual", "reject_manual"] and can_approve:
            log_id = request.POST.get("log_id")
            if is_superadmin(request.user):
                log = get_object_or_404(TimeLog, id=log_id, entry_type="manual")
            else:
                log = get_object_or_404(
                    TimeLog,
                    id=log_id,
                    entry_type="manual",
                    task__module__project__team_id=request.user.team_id,
                )
            note = (request.POST.get("approval_note") or "").strip()
            log.approval_status = "approved" if action == "approve_manual" else "rejected"
            log.approved_by = request.user
            log.approved_at = timezone.now()
            log.approval_note = note
            log.save()
            Notification.objects.create(
                user=log.user,
                message=f"Manual entry {log.id} {log.approval_status} by {request.user.email}.",
                redirect_url=reverse("tracking"),
                is_read=False,
            )
            messages.success(request, f"Manual entry #{log.id} {log.approval_status}.")
            return redirect("tracking")

    return render(request, 'core/tracking.html', {
        'logs': logs,
        'active_log': active_log,
        'user_tasks': user_tasks,
        'manual_form': manual_form,
        'can_approve': can_approve,
        'pending_manual_logs': pending_manual_logs,
    })

@login_required
def reports(request):
    today = timezone.now().date()

    if request.user.role == "developer":
        tasks_qs = tasks_qs_for(request.user)
        time_logs = TimeLog.objects.filter(user=request.user, approval_status="approved")
        user_time = time_logs.values("user__email").annotate(total_seconds=Sum("total_time")).order_by("-total_seconds")[:10]

        projects_progress = projects_qs_for(request.user).annotate(
            total=Count("modules__tasks", filter=Q(modules__tasks__assigned_to=request.user)),
            done=Count(
                "modules__tasks",
                filter=Q(modules__tasks__status="done", modules__tasks__assigned_to=request.user),
            ),
            members=Count("modules__tasks__assigned_to", distinct=True, filter=Q(modules__tasks__assigned_to=request.user)),
        )
    else:
        tasks_qs = tasks_qs_for(request.user)
        # Time spent per user (top 10)
        time_logs = TimeLog.objects.filter(approval_status="approved")
        if not is_superadmin(request.user):
            time_logs = time_logs.filter(task__module__project__team_id=request.user.team_id)
        user_time = time_logs.values("user__email").annotate(total_seconds=Sum("total_time")).order_by("-total_seconds")[:10]

        projects_progress = projects_qs_for(request.user).annotate(
            total=Count("modules__tasks"),
            done=Count("modules__tasks", filter=Q(modules__tasks__status="done")),
            members=Count("modules__tasks__assigned_to", distinct=True),
        )

    total_tasks = tasks_qs.count()
    done_tasks = tasks_qs.filter(status="done").count()
    completion_rate = round((done_tasks / total_tasks * 100), 1) if total_tasks else 0.0
    upcoming_deadlines = tasks_qs.filter(deadline__gte=today).exclude(status="done").count()

    # Project completion arrays (for chart)
    project_names = []
    project_percents = []
    for p in projects_progress:
        project_names.append(p.name)
        project_percents.append(round((float(p.done) / float(p.total) * 100), 1) if p.total else 0.0)

    user_labels = [u["user__email"] for u in user_time]
    user_seconds = [u["total_seconds"].total_seconds() / 3600 if u["total_seconds"] else 0 for u in user_time]

    # Simple status breakdown for quick insight
    status_qs = tasks_qs.values("status").annotate(count=Count("id"))
    status_labels = [s["status"].replace("_", " ").capitalize() for s in status_qs]
    status_counts = [s["count"] for s in status_qs]

    return render(
        request,
        "core/reports.html",
        {
            "user_labels": user_labels,
            "user_seconds": user_seconds,
            "projects_progress": projects_progress,
            "project_names": project_names,
            "project_percents": project_percents,
            "completion_rate": completion_rate,
            "upcoming_deadlines": upcoming_deadlines,
            "status_labels": status_labels,
            "status_counts": status_counts,
        },
    )


@login_required
def enhancements(request):
    implemented_items = [
        "Role-based project, task, reporting and billing flows",
        "Manual time-entry approval queue for managers/admins",
        "Deadline, overdue, and time-limit alerts",
        "Idle tracking signal API for productivity monitoring",
    ]
    working_features = [
        "Predictive Risk Engine based on real project progress",
        "Project health drill-down with module/team metrics",
        "Scenario simulator for delivery timeline projection",
    ]
    today = timezone.now().date()
    risk_rows = []
    for project in projects_qs_for(request.user).order_by("name"):
        project_tasks = Task.objects.filter(module__project=project)
        total_tasks = project_tasks.count()
        done_tasks = project_tasks.filter(status="done").count()
        overdue_tasks = project_tasks.filter(deadline__lt=today).exclude(status="done").count()
        remaining_tasks = max(total_tasks - done_tasks, 0)
        completion_pct = round((done_tasks / total_tasks) * 100, 1) if total_tasks else 0.0

        spent = TimeLog.objects.filter(task__module__project=project, approval_status="approved").aggregate(
            total=Sum("total_time")
        )["total"]
        spent_hours = round(spent.total_seconds() / 3600, 2) if spent else 0.0

        deadline_pressure = 0
        if project.end_date:
            days_left = (project.end_date - today).days
            if days_left <= 0 and remaining_tasks > 0:
                deadline_pressure = 30
            elif days_left <= 7 and remaining_tasks > 0:
                deadline_pressure = 18
            elif days_left <= 14 and remaining_tasks > 0:
                deadline_pressure = 8

        risk_score = min(
            100,
            int((overdue_tasks * 24) + (remaining_tasks * 7) + ((100 - completion_pct) * 0.25) + deadline_pressure),
        )
        if risk_score >= 70:
            risk_level = "High"
            recommendation = "Immediate intervention: rebalance workload and review blockers."
        elif risk_score >= 40:
            risk_level = "Medium"
            recommendation = "Monitor closely: add checkpoint and support developers."
        else:
            risk_level = "Low"
            recommendation = "Healthy trend: continue current execution pace."

        risk_rows.append(
            {
                "project": project,
                "total_tasks": total_tasks,
                "done_tasks": done_tasks,
                "remaining_tasks": remaining_tasks,
                "overdue_tasks": overdue_tasks,
                "completion_pct": completion_pct,
                "spent_hours": spent_hours,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "recommendation": recommendation,
            }
        )

    risk_rows.sort(key=lambda x: x["risk_score"], reverse=True)

    sim_project_id = request.GET.get("sim_project")
    planned_daily_hours = request.GET.get("daily_hours", "6")
    simulator = None
    if sim_project_id and sim_project_id.isdigit():
        sim_project = projects_qs_for(request.user).filter(id=int(sim_project_id)).first()
        if sim_project:
            sim_tasks = Task.objects.filter(module__project=sim_project)
            remaining_tasks = sim_tasks.exclude(status="done").count()
            total_spent_td = TimeLog.objects.filter(task__module__project=sim_project, approval_status="approved").aggregate(
                total=Sum("total_time")
            )["total"] or timedelta(0)
            spent_hours = total_spent_td.total_seconds() / 3600
            done_count = sim_tasks.filter(status="done").count()
            avg_hours_per_done_task = (spent_hours / done_count) if done_count > 0 else 4.0
            projected_remaining_hours = round(remaining_tasks * avg_hours_per_done_task, 2)
            try:
                daily_hours = max(float(planned_daily_hours), 1.0)
            except ValueError:
                daily_hours = 6.0
            eta_days = int((projected_remaining_hours / daily_hours) + 0.999) if projected_remaining_hours > 0 else 0
            simulator = {
                "project": sim_project,
                "remaining_tasks": remaining_tasks,
                "avg_hours_per_done_task": round(avg_hours_per_done_task, 2),
                "projected_remaining_hours": projected_remaining_hours,
                "daily_hours": daily_hours,
                "eta_days": eta_days,
                "eta_date": (today + timedelta(days=eta_days)),
            }

    return render(
        request,
        "core/enhancements.html",
        {
            "implemented_items": implemented_items,
            "working_features": working_features,
            "risk_rows": risk_rows,
            "projects": projects_qs_for(request.user).order_by("name"),
            "simulator": simulator,
            "sim_project_id": int(sim_project_id) if sim_project_id and sim_project_id.isdigit() else None,
            "planned_daily_hours": planned_daily_hours,
        },
    )


@login_required
def data_health(request):
    if request.user.role not in ["admin", "manager"] and not is_superadmin(request.user):
        messages.error(request, "You don't have permission to access Data QA.")
        return redirect("dashboard")

    today = timezone.now().date()
    users_qs = User.objects.all()
    if not is_superadmin(request.user):
        users_qs = users_qs.filter(team_id=request.user.team_id)
    users_total = users_qs.count()
    users_by_role = dict(users_qs.values("role").annotate(c=Count("id")).values_list("role", "c"))
    users_active = users_qs.filter(status="active").count()
    users_inactive = users_qs.exclude(status="active").count()

    projects_qs = projects_qs_for(request.user)
    projects_count = projects_qs.count()
    modules_qs = Module.objects.filter(project__in=projects_qs)
    modules_count = modules_qs.count()

    task_status_rows = tasks_qs_for(request.user).values("status").annotate(n=Count("id")).order_by("status")
    task_status_map = {row["status"]: row["n"] for row in task_status_rows}
    tasks_total = sum(task_status_map.values())

    overdue_tasks = tasks_qs_for(request.user).filter(deadline__lt=today).exclude(status="done").count()
    unassigned_tasks = tasks_qs_for(request.user).filter(assigned_to__isnull=True).count()

    logs_qs = TimeLog.objects.all()
    if not is_superadmin(request.user):
        logs_qs = logs_qs.filter(task__module__project__team_id=request.user.team_id)
    log_approval_rows = logs_qs.values("approval_status").annotate(n=Count("id"))
    log_approval_map = {row["approval_status"]: row["n"] for row in log_approval_rows}
    logs_total = logs_qs.count()
    logs_auto = logs_qs.filter(entry_type="auto").count()
    logs_manual = logs_qs.filter(entry_type="manual").count()
    active_timers = logs_qs.filter(end_time__isnull=True).count()

    approved_td = logs_qs.filter(approval_status="approved").aggregate(t=Sum("total_time"))["t"]
    approved_hours_global = round(approved_td.total_seconds() / 3600, 2) if approved_td else 0.0

    invoices_qs = Invoice.objects.all()
    notif_qs = Notification.objects.all()
    if not is_superadmin(request.user):
        invoices_qs = invoices_qs.filter(user__team_id=request.user.team_id)
        notif_qs = notif_qs.filter(user__team_id=request.user.team_id)
    invoices_count = invoices_qs.count()
    notif_total = notif_qs.count()
    notif_unread = notif_qs.filter(is_read=False).count()

    checks = []
    checks.append(
        {
            "ok": overdue_tasks == 0,
            "severity": "warning" if overdue_tasks else "success",
            "label": "Overdue open tasks",
            "detail": f"{overdue_tasks} task(s) past deadline and not done.",
        }
    )
    checks.append(
        {
            "ok": unassigned_tasks == 0,
            "severity": "warning" if unassigned_tasks else "success",
            "label": "Unassigned tasks",
            "detail": f"{unassigned_tasks} task(s) without assignee.",
        }
    )
    checks.append(
        {
            "ok": active_timers <= users_qs.filter(role="developer", status="active").count(),
            "severity": "info",
            "label": "Active timers (running)",
            "detail": f"{active_timers} open time session(s).",
        }
    )
    pending_manual = log_approval_map.get("pending", 0)
    checks.append(
        {
            "ok": pending_manual == 0,
            "severity": "warning" if pending_manual else "success",
            "label": "Manual entries pending approval",
            "detail": f"{pending_manual} manual log(s) awaiting review.",
        }
    )

    return render(
        request,
        "core/data_health.html",
        {
            "users_total": users_total,
            "users_by_role": users_by_role,
            "users_active": users_active,
            "users_inactive": users_inactive,
            "projects_count": projects_count,
            "modules_count": modules_count,
            "tasks_total": tasks_total,
            "task_status_map": task_status_map,
            "overdue_tasks": overdue_tasks,
            "unassigned_tasks": unassigned_tasks,
            "logs_total": logs_total,
            "log_approval_map": log_approval_map,
            "logs_auto": logs_auto,
            "logs_manual": logs_manual,
            "active_timers": active_timers,
            "approved_hours_global": approved_hours_global,
            "invoices_count": invoices_count,
            "notif_total": notif_total,
            "notif_unread": notif_unread,
            "checks": checks,
            "generated_at": timezone.now(),
        },
    )


@login_required
@ensure_csrf_cookie
def billing(request):
    if not (request.user.role in ["admin", "manager"] or request.user.is_superuser):
        messages.error(request, "You don't have permission to access billing.")
        return redirect("dashboard")
    if request.user.is_superuser:
        invoices = Invoice.objects.all()
    else:
        invoices = Invoice.objects.filter(user__team_id=request.user.team_id)
    # Mocking hours for invoice generation demo
    total_hours_duration = TimeLog.objects.filter(
        user=request.user,
        approval_status="approved",
        is_billable=True,
    ).aggregate(total=Sum('total_time'))['total'] or timedelta(0)
    total_hours = total_hours_duration.total_seconds() / 3600
    hourly_rate = float(getattr(settings, "BILLING_HOURLY_RATE", 500))
    billable_amount = total_hours * hourly_rate

    if request.GET.get("generate_test") == "1":
        # Razorpay minimum ₹1 — zero-bill users can still test Checkout (card / UPI).
        test_amt = Decimal("1.00")
        rate_dec = Decimal(str(hourly_rate))
        test_hours = (test_amt / rate_dec).quantize(Decimal("0.0001")) if rate_dec > 0 else Decimal("0.01")
        inv = Invoice.objects.create(
            user=request.user,
            total_hours=test_hours,
            amount=test_amt,
        )
        messages.success(
            request,
            f"Test invoice #{inv.id} (₹1) created — click Pay to try Razorpay.",
        )
        return redirect("billing")

    if request.GET.get('generate') == '1':
        inv = Invoice.objects.create(
            user=request.user,
            total_hours=total_hours,
            amount=billable_amount
        )
        messages.success(request, f"Invoice #{inv.id} generated.")
        return redirect('billing')
        
    return render(request, 'core/billing.html', {
        'invoices': invoices,
        'total_hours': round(total_hours, 2),
        'billable_amount': round(billable_amount, 2),
        'hourly_rate': hourly_rate,
        'razorpay_enabled': _razorpay_configured(),
        'razorpay_key_id': getattr(settings, "RAZORPAY_KEY_ID", "") or "",
        'razorpay_currency': getattr(settings, "RAZORPAY_CURRENCY", "INR") or "INR",
        'razorpay_prefill_contact': getattr(settings, "RAZORPAY_PREFILL_CONTACT", "") or "",
    })

@login_required
def print_invoice(request, invoice_id):
    if not (request.user.role in ["admin", "manager"] or request.user.is_superuser):
        messages.error(request, "You don't have permission to print invoices.")
        return redirect("dashboard")
    if request.user.is_superuser:
        invoice = get_object_or_404(Invoice, id=invoice_id)
    else:
        invoice = get_object_or_404(Invoice, id=invoice_id, user__team_id=request.user.team_id)
    th = float(invoice.total_hours) if invoice.total_hours else 0.0
    if th > 0:
        hourly_rate = round(float(invoice.amount) / th, 2)
    else:
        hourly_rate = float(getattr(settings, "BILLING_HOURLY_RATE", 500))
    return render(request, 'core/invoice_print.html', {'invoice': invoice, 'hourly_rate': hourly_rate})

@login_required
@require_POST
def razorpay_create_order(request):
    if not (request.user.role in ["admin", "manager"] or request.user.is_superuser):
        return JsonResponse({"error": "Forbidden"}, status=403)
    if not _razorpay_configured():
        return JsonResponse({"error": "Razorpay not configured — set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env"}, status=503)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    invoice_id = payload.get("invoice_id")
    if not invoice_id:
        return JsonResponse({"error": "invoice_id required"}, status=400)
    if request.user.is_superuser:
        invoice = get_object_or_404(Invoice, id=invoice_id)
    else:
        invoice = get_object_or_404(Invoice, id=invoice_id, user__team_id=request.user.team_id)
    if invoice.payment_status == Invoice.PAYMENT_PAID:
        return JsonResponse({"error": "Invoice already paid"}, status=400)

    # Float * 100 se galat paise (e.g. 10.99) — Decimal se exact paise
    amt = invoice.amount if isinstance(invoice.amount, Decimal) else Decimal(str(invoice.amount))
    amount_paise = int((amt * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if amount_paise < 100:
        return JsonResponse({"error": "Minimum payable amount is ₹1 (100 paise)."}, status=400)

    currency = getattr(settings, "RAZORPAY_CURRENCY", "INR") or "INR"
    try:
        order = _razorpay_create_order_api(
            amount_paise,
            currency,
            f"inv_{invoice.id}"[:40],
            {"invoice_id": str(invoice.id), "user_email": invoice.user.email},
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    invoice.razorpay_order_id = order["id"]
    invoice.save(update_fields=["razorpay_order_id"])

    return JsonResponse(
        {
            "key_id": settings.RAZORPAY_KEY_ID.strip(),
            "order_id": order["id"],
            "amount": amount_paise,
            "currency": currency,
            "invoice_id": invoice.id,
            "return_token": _razorpay_sign_return_token(request.user.id, invoice.id, order["id"]),
        }
    )


@login_required
@require_POST
def razorpay_verify_payment(request):
    if not (request.user.role in ["admin", "manager"] or request.user.is_superuser):
        return JsonResponse({"error": "Forbidden"}, status=403)
    if not _razorpay_configured():
        return JsonResponse({"error": "Razorpay not configured"}, status=503)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    invoice_id = payload.get("invoice_id")
    razorpay_order_id = payload.get("razorpay_order_id")
    razorpay_payment_id = payload.get("razorpay_payment_id")
    razorpay_signature = payload.get("razorpay_signature")
    if not all([invoice_id, razorpay_order_id, razorpay_payment_id, razorpay_signature]):
        return JsonResponse({"error": "Missing payment fields"}, status=400)

    if request.user.is_superuser:
        invoice = get_object_or_404(Invoice, id=invoice_id)
    else:
        invoice = get_object_or_404(Invoice, id=invoice_id, user__team_id=request.user.team_id)
    ok, err = _razorpay_try_mark_invoice_paid(invoice, razorpay_order_id, razorpay_payment_id, razorpay_signature)
    if ok:
        return JsonResponse({"ok": True})
    return JsonResponse({"error": err or "Verification failed"}, status=400)


def _razorpay_callback_param(request, key: str):
    """Razorpay may return GET query or POST body (redirect / form POST); no CSRF on POST."""
    if request.method == "POST":
        v = request.POST.get(key)
        if v is not None:
            return v
    return request.GET.get(key)


@csrf_exempt
def razorpay_payment_return(request, invoice_id):
    """
    Razorpay Checkout with callback_url + redirect:true — card 3DS / mock bank opens in full window
    (avoids Edge about:blank pop-up with handler-only flow). Razorpay POSTs here; session cookie is
    often not sent on that cross-site POST, so ?rt= signed token authorizes the invoice owner.
    """
    if not _razorpay_configured():
        messages.error(request, "Razorpay is not configured.")
        return redirect("billing")

    invoice = get_object_or_404(Invoice, id=invoice_id)
    token_payload = _razorpay_return_token_payload(request, invoice)
    authed_owner = request.user.is_authenticated and invoice.user_id == request.user.id
    if not authed_owner and not token_payload:
        if request.user.is_authenticated:
            messages.error(request, "You don't have permission.")
            return redirect("billing")
        messages.warning(request, "Payment return link is invalid or expired. Open Billing and try Pay again.")
        return redirect("billing")

    err_code = _razorpay_callback_param(request, "error[code]") or _razorpay_callback_param(request, "error_code")
    err_desc = _razorpay_callback_param(request, "error[description]") or _razorpay_callback_param(
        request, "error_description"
    )
    if err_code or err_desc:
        invoice.payment_status = Invoice.PAYMENT_FAILED
        invoice.save(update_fields=["payment_status"])
        messages.error(request, err_desc or err_code or "Payment failed.")
        return redirect("billing")

    payment_id = _razorpay_callback_param(request, "razorpay_payment_id")
    order_id = _razorpay_callback_param(request, "razorpay_order_id")
    signature = _razorpay_callback_param(request, "razorpay_signature")

    if not all([payment_id, order_id, signature]):
        messages.warning(request, "Payment was cancelled or did not complete.")
        return redirect("billing")

    if token_payload and token_payload.get("o") != order_id:
        messages.error(request, "Payment session does not match this invoice. Start again from Billing.")
        return redirect("billing")

    ok, err = _razorpay_try_mark_invoice_paid(invoice, order_id, payment_id, signature)
    if ok:
        messages.success(request, f"Payment successful. Invoice #{invoice.id} is paid.")
    else:
        messages.error(request, err or "Could not verify payment.")
    return redirect("billing")


@login_required
def notifications(request):
    if request.method == 'GET':
        # Deadline reminder: next 24 hours (simple on-demand generation)
        today = timezone.now().date()
        tomorrow = today + timedelta(days=1)
        deadline_tasks = Task.objects.filter(
            assigned_to=request.user,
            deadline__isnull=False,
            deadline__gte=today,
            deadline__lte=tomorrow,
        ).exclude(status='done')

        for task in deadline_tasks:
            msg = f"Deadline reminder: Task #{task.id} - {task.title}"
            Notification.objects.get_or_create(
                user=request.user,
                message=msg,
                defaults={'is_read': False, 'redirect_url': f"{reverse('tasks')}?task={task.id}"},
            )

        overdue_tasks = Task.objects.filter(
            assigned_to=request.user,
            deadline__isnull=False,
            deadline__lt=today,
        ).exclude(status='done')
        for task in overdue_tasks:
            Notification.objects.get_or_create(
                user=request.user,
                message=f"Overdue task alert: Task #{task.id} - {task.title}",
                defaults={'is_read': False, 'redirect_url': f"{reverse('tasks')}?task={task.id}"},
            )

        # Time limit exceeded simple rule: 8h+ spent and still not done.
        long_running_tasks = (
            Task.objects.filter(assigned_to=request.user)
            .exclude(status='done')
            .annotate(total_spent=Sum("time_logs__total_time"))
        )
        for task in long_running_tasks:
            if task.total_spent and task.total_spent >= timedelta(hours=8):
                Notification.objects.get_or_create(
                    user=request.user,
                    message=f"Time limit exceeded: Task #{task.id} - {task.title}",
                    defaults={'is_read': False, 'redirect_url': f"{reverse('tasks')}?task={task.id}"},
                )

    notifs = Notification.objects.filter(user=request.user).order_by('-created_at')

    if request.method == 'POST':
        Notification.objects.filter(user=request.user).update(is_read=True)
        return JsonResponse({'status': 'ok'})

    return render(request, 'core/notifications.html', {'notifications': notifs})


@login_required
def api_notifications_feed(request):
    notifs = Notification.objects.filter(user=request.user).order_by("-created_at")[:8]
    unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
    items = []
    for n in notifs:
        items.append(
            {
                "id": n.id,
                "message": n.message,
                "redirect_url": n.redirect_url or reverse("tasks"),
                "is_read": n.is_read,
                "time_ago": f"{timesince(n.created_at)} ago",
            }
        )
    return JsonResponse({"unread_count": unread_count, "items": items})


@login_required
def profile_settings(request):
    user = request.user
    if request.method == "POST":
        full_name = (request.POST.get("full_name") or "").strip()
        avatar = request.FILES.get("avatar")
        remove_avatar = request.POST.get("remove_avatar") == "1"
        if full_name:
            parts = full_name.split(" ", 1)
            user.first_name = parts[0]
            user.last_name = parts[1] if len(parts) > 1 else ""
        if avatar:
            user.avatar = avatar
        if remove_avatar:
            user.avatar = None
        user.save()
        messages.success(request, "Profile updated successfully.")
        return redirect("profile_settings")

    recent_logs = TimeLog.objects.filter(user=user).select_related("task").order_by("-start_time")[:8]
    recent_notifs = Notification.objects.filter(user=user).order_by("-created_at")[:8]

    timeline = []
    for log in recent_logs:
        timeline.append(
            {
                "kind": "log",
                "title": f"Time logged on {log.task.title}",
                "meta": str(log.total_time) if log.total_time else "In progress",
                "when": log.start_time,
            }
        )
    for notif in recent_notifs:
        timeline.append(
            {
                "kind": "notification",
                "title": notif.message,
                "meta": "Notification",
                "when": notif.created_at,
            }
        )

    timeline.sort(key=lambda x: x["when"], reverse=True)
    timeline = timeline[:12]

    return render(
        request,
        "core/profile_settings.html",
        {
            "timeline": timeline,
        },
    )


@login_required
def profile_activity_export_csv(request):
    user = request.user
    logs = TimeLog.objects.filter(user=user).select_related("task").order_by("-start_time")[:200]
    notifs = Notification.objects.filter(user=user).order_by("-created_at")[:200]

    rows = []
    for log in logs:
        rows.append(
            {
                "type": "time_log",
                "title": f"Time logged on {log.task.title}",
                "meta": str(log.total_time) if log.total_time else "In progress",
                "when": log.start_time,
            }
        )
    for notif in notifs:
        rows.append(
            {
                "type": "notification",
                "title": notif.message,
                "meta": "Notification",
                "when": notif.created_at,
            }
        )
    rows.sort(key=lambda x: x["when"], reverse=True)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="timetrack_activity.csv"'
    writer = csv.writer(response)
    writer.writerow(["Type", "Title", "Meta", "When"])
    for row in rows:
        writer.writerow([row["type"], row["title"], row["meta"], row["when"].strftime("%Y-%m-%d %H:%M:%S")])
    return response


@login_required
def profile_activity_export_pdf(request):
    user = request.user
    logs = TimeLog.objects.filter(user=user).select_related("task").order_by("-start_time")[:80]
    notifs = Notification.objects.filter(user=user).order_by("-created_at")[:80]
    rows = []
    for log in logs:
        rows.append(
            {
                "type": "Time Log",
                "title": f"Time logged on {log.task.title}",
                "meta": str(log.total_time) if log.total_time else "In progress",
                "when": log.start_time,
            }
        )
    for notif in notifs:
        rows.append(
            {
                "type": "Notification",
                "title": notif.message,
                "meta": "Notification",
                "when": notif.created_at,
            }
        )
    rows.sort(key=lambda x: x["when"], reverse=True)
    return render(request, "core/profile_activity_print.html", {"rows": rows})

# AJAX API Views
@login_required
@require_POST
def api_timer_start(request):
    custom_title = (request.POST.get("custom_title") or "").strip()
    task_id = request.POST.get("task_id")

    active = TimeLog.objects.filter(user=request.user, end_time__isnull=True)
    if active.exists():
        return JsonResponse({"error": "You already have an active timer! Stop it first."})

    task = None
    if task_id:
        task_qs = tasks_qs_for(request.user)
        task = get_object_or_404(task_qs, id=task_id)
    elif custom_title:
        task = _create_quick_task_for_user(request.user, custom_title)
    else:
        return JsonResponse(
            {"error": "Select a task from the list or type a task name in the box."}
        )

    log = TimeLog.objects.create(
        task=task,
        user=request.user,
        start_time=timezone.now(),
        entry_type="auto",
        approval_status="approved",
    )
    # Update task status to in progress if it was todo
    if task.status == 'todo':
        task.status = 'in_progress'
        task.save()
        
    return JsonResponse({'status': 'ok', 'log_id': log.id, 'task_title': task.title})

@login_required
def api_timer_stop(request):
    log = TimeLog.objects.filter(user=request.user, end_time__isnull=True).last()
    if log:
        log.end_time = timezone.now()
        log.save()
        return JsonResponse({'status': 'ok', 'duration': str(log.total_time)})
    return JsonResponse({'error': 'No active timer found.'})

@login_required
def api_task_update_status(request):
    task_id = request.POST.get('task_id')
    new_status = request.POST.get('status')
    task_qs = tasks_qs_for(request.user)
    task = get_object_or_404(task_qs, id=task_id)

    valid_statuses = {choice[0] for choice in Task.STATUS_CHOICES}
    if new_status not in valid_statuses:
        return JsonResponse({'error': 'Invalid status'}, status=400)

    task.status = new_status
    task.save()
    return JsonResponse({'status': 'ok'})


@login_required
def api_idle_ping(request):
    idle_minutes = int(request.POST.get("idle_minutes", 0))
    if idle_minutes < 15:
        return JsonResponse({"status": "ok"})
    active_log = TimeLog.objects.filter(user=request.user, end_time__isnull=True).last()
    if active_log:
        active_log.idle_minutes = max(active_log.idle_minutes, idle_minutes)
        active_log.save()
        _notify_admins_and_managers(
            f"Idle warning: {request.user.email} inactive for {idle_minutes} minutes on {active_log.task.title}.",
            exclude_user_id=request.user.id,
            redirect_url=reverse("tracking"),
            team_id=active_log.task.module.project.team_id,
        )
    return JsonResponse({"status": "ok"})
