from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from core.models import Invoice, Module, Notification, Project, Task, Team, TimeLog


class Command(BaseCommand):
    help = "Load or reset sample users, projects, and time logs for testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing demo/sample data before seeding.",
        )

    def handle(self, *args, **options):
        reset = options["reset"]
        User = get_user_model()

        if reset:
            self.stdout.write(self.style.WARNING("Resetting existing demo data..."))
            Invoice.objects.all().delete()
            Notification.objects.all().delete()
            TimeLog.objects.all().delete()
            Task.objects.all().delete()
            Module.objects.all().delete()
            Project.objects.all().delete()
            User.objects.exclude(email="pmeet5010@gmail.com").delete()
            Team.objects.exclude(code="default-team").delete()

        admin, _ = User.objects.get_or_create(
            email="pmeet5010@gmail.com",
            defaults={"role": "admin", "is_staff": True, "is_superuser": True},
        )
        admin.status = "active"
        admin.is_active = True
        if not admin.check_password("meet112005@"):
            admin.set_password("meet112005@")
        admin.save()

        team_alpha, _ = Team.objects.get_or_create(
            code="team-alpha",
            defaults={"name": "Team Alpha", "created_by": admin},
        )
        team_beta, _ = Team.objects.get_or_create(
            code="team-beta",
            defaults={"name": "Team Beta", "created_by": admin},
        )

        alpha_admin, _ = User.objects.get_or_create(email="alpha.admin@timetrack.local", defaults={"role": "admin"})
        alpha_admin.team = team_alpha
        alpha_admin.status = "active"
        alpha_admin.is_active = True
        alpha_admin.set_password("Admin@12345")
        alpha_admin.save()

        alpha_manager, _ = User.objects.get_or_create(email="alpha.manager@timetrack.local", defaults={"role": "manager"})
        alpha_manager.team = team_alpha
        alpha_manager.status = "active"
        alpha_manager.is_active = True
        alpha_manager.set_password("Manager@123")
        alpha_manager.save()

        alpha_dev1, _ = User.objects.get_or_create(email="alpha.dev1@timetrack.local", defaults={"role": "developer"})
        alpha_dev1.team = team_alpha
        alpha_dev1.status = "active"
        alpha_dev1.is_active = True
        alpha_dev1.set_password("Dev@12345")
        alpha_dev1.save()

        alpha_dev2, _ = User.objects.get_or_create(email="alpha.dev2@timetrack.local", defaults={"role": "developer"})
        alpha_dev2.team = team_alpha
        alpha_dev2.status = "active"
        alpha_dev2.is_active = True
        alpha_dev2.set_password("Dev@12345")
        alpha_dev2.save()

        beta_admin, _ = User.objects.get_or_create(email="beta.admin@timetrack.local", defaults={"role": "admin"})
        beta_admin.team = team_beta
        beta_admin.status = "active"
        beta_admin.is_active = True
        beta_admin.set_password("Admin@12345")
        beta_admin.save()

        beta_manager, _ = User.objects.get_or_create(email="beta.manager@timetrack.local", defaults={"role": "manager"})
        beta_manager.team = team_beta
        beta_manager.status = "active"
        beta_manager.is_active = True
        beta_manager.set_password("Manager@123")
        beta_manager.save()

        beta_dev1, _ = User.objects.get_or_create(email="beta.dev1@timetrack.local", defaults={"role": "developer"})
        beta_dev1.team = team_beta
        beta_dev1.status = "active"
        beta_dev1.is_active = True
        beta_dev1.set_password("Dev@12345")
        beta_dev1.save()

        p1, _ = Project.objects.get_or_create(
            name="TimeTrack Platform (Alpha)",
            defaults={
                "description": "Alpha team — main product build",
                "start_date": timezone.now().date() - timedelta(days=30),
                "end_date": timezone.now().date() + timedelta(days=60),
                "created_by": alpha_admin,
                "team": team_alpha,
            },
        )
        p2, _ = Project.objects.get_or_create(
            name="Client Portal (Alpha)",
            defaults={
                "description": "Alpha team — reporting and invoice portal",
                "start_date": timezone.now().date() - timedelta(days=20),
                "end_date": timezone.now().date() + timedelta(days=45),
                "created_by": alpha_manager,
                "team": team_alpha,
            },
        )
        p3, _ = Project.objects.get_or_create(
            name="Mobile Companion (Beta)",
            defaults={
                "description": "Beta team — mobile tracker companion",
                "start_date": timezone.now().date() - timedelta(days=15),
                "end_date": timezone.now().date() + timedelta(days=30),
                "created_by": beta_admin,
                "team": team_beta,
            },
        )

        m11, _ = Module.objects.get_or_create(project=p1, name="Authentication")
        m12, _ = Module.objects.get_or_create(project=p1, name="Time Engine")
        m21, _ = Module.objects.get_or_create(project=p2, name="Billing UI")
        m22, _ = Module.objects.get_or_create(project=p2, name="Reports API")
        m31, _ = Module.objects.get_or_create(project=p3, name="Sync Engine")

        t1, _ = Task.objects.get_or_create(
            module=m11,
            title="Email login hardening",
            defaults={
                "assigned_to": alpha_dev1,
                "description": "Improve session security and validation",
                "status": "in_progress",
                "priority": "high",
                "deadline": timezone.now().date() + timedelta(days=2),
            },
        )
        t2, _ = Task.objects.get_or_create(
            module=m12,
            title="Timer edge-case fixes",
            defaults={
                "assigned_to": alpha_dev1,
                "description": "Handle double-start and race conditions",
                "status": "todo",
                "priority": "high",
                "deadline": timezone.now().date() + timedelta(days=1),
            },
        )
        t3, _ = Task.objects.get_or_create(
            module=m21,
            title="Invoice print polish",
            defaults={
                "assigned_to": alpha_dev2,
                "description": "Refine invoice template for print view",
                "status": "done",
                "priority": "medium",
                "deadline": timezone.now().date() - timedelta(days=1),
            },
        )
        t4, _ = Task.objects.get_or_create(
            module=m22,
            title="Report aggregation optimization",
            defaults={
                "assigned_to": alpha_dev2,
                "description": "Reduce query cost for charts",
                "status": "in_progress",
                "priority": "medium",
                "deadline": timezone.now().date() + timedelta(days=3),
            },
        )
        t5, _ = Task.objects.get_or_create(
            module=m22,
            title="Weekly productivity trend",
            defaults={
                "assigned_to": alpha_manager,
                "description": "Add weekly trend chart data endpoint",
                "status": "todo",
                "priority": "low",
                "deadline": timezone.now().date() + timedelta(days=5),
            },
        )
        t6, _ = Task.objects.get_or_create(
            module=m31,
            title="Offline sync strategy",
            defaults={
                "assigned_to": beta_dev1,
                "description": "Design conflict resolution and batching",
                "status": "in_progress",
                "priority": "high",
                "deadline": timezone.now().date() + timedelta(days=4),
            },
        )

        base = timezone.now() - timedelta(days=6)
        entries = [
            (0, t1, alpha_dev1, 2.5),
            (1, t1, alpha_dev1, 3.0),
            (2, t2, alpha_dev1, 1.25),
            (3, t3, alpha_dev2, 4.0),
            (4, t4, alpha_dev2, 2.75),
            (5, t5, alpha_manager, 1.5),
            (6, t6, beta_dev1, 2.0),
        ]
        for idx, task, user, hours in entries:
            st = base + timedelta(days=idx, hours=9)
            et = st + timedelta(hours=hours)
            TimeLog.objects.get_or_create(task=task, user=user, start_time=st, defaults={"end_time": et})

        Notification.objects.get_or_create(
            user=alpha_dev1,
            message="New task assigned: Timer edge-case fixes",
            defaults={"is_read": False, "redirect_url": f"{reverse('tasks')}?task={t2.id}"},
        )
        Notification.objects.get_or_create(
            user=alpha_dev2,
            message=f"Deadline reminder: Task #{t4.id} - {t4.title}",
            defaults={"is_read": False, "redirect_url": f"{reverse('tasks')}?task={t4.id}"},
        )
        Notification.objects.get_or_create(
            user=alpha_manager,
            message="Sprint review meeting tomorrow at 11:00 AM",
            defaults={"is_read": False, "redirect_url": reverse("reports")},
        )

        for user in [alpha_dev1, alpha_dev2, alpha_manager, beta_dev1]:
            total = TimeLog.objects.filter(user=user).aggregate(s=Sum("total_time"))["s"]
            hours = round((total.total_seconds() / 3600), 2) if total else 0
            if hours > 0:
                rate = float(getattr(settings, "BILLING_HOURLY_RATE", 500))
                Invoice.objects.get_or_create(user=user, total_hours=hours, amount=round(hours * rate, 2))

        self.stdout.write(
            self.style.SUCCESS(
                "Demo seed complete: "
                f"users={User.objects.count()} "
                f"projects={Project.objects.count()} "
                f"modules={Module.objects.count()} "
                f"tasks={Task.objects.count()} "
                f"logs={TimeLog.objects.count()} "
                f"notifications={Notification.objects.count()} "
                f"invoices={Invoice.objects.count()}"
            )
        )
