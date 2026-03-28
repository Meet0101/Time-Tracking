from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from core.models import Invoice, Module, Notification, Project, Task, TimeLog


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

        admin, _ = User.objects.get_or_create(
            email="pmeet5010@gmail.com",
            defaults={"role": "admin", "is_staff": True, "is_superuser": True},
        )
        admin.status = "active"
        admin.is_active = True
        if not admin.check_password("meet112005@"):
            admin.set_password("meet112005@")
        admin.save()

        manager, _ = User.objects.get_or_create(email="manager@timetrack.local", defaults={"role": "manager"})
        manager.status = "active"
        manager.is_active = True
        manager.set_password("Manager@123")
        manager.save()

        dev1, _ = User.objects.get_or_create(email="dev1@timetrack.local", defaults={"role": "developer"})
        dev1.status = "active"
        dev1.is_active = True
        dev1.set_password("Dev@12345")
        dev1.save()

        dev2, _ = User.objects.get_or_create(email="dev2@timetrack.local", defaults={"role": "developer"})
        dev2.status = "active"
        dev2.is_active = True
        dev2.set_password("Dev@12345")
        dev2.save()

        p1, _ = Project.objects.get_or_create(
            name="TimeTrack Platform",
            defaults={
                "description": "Main product build — web app",
                "start_date": timezone.now().date() - timedelta(days=30),
                "end_date": timezone.now().date() + timedelta(days=60),
                "created_by": admin,
            },
        )
        p2, _ = Project.objects.get_or_create(
            name="Client Portal",
            defaults={
                "description": "Customer-facing reporting and invoice portal",
                "start_date": timezone.now().date() - timedelta(days=20),
                "end_date": timezone.now().date() + timedelta(days=45),
                "created_by": manager,
            },
        )

        m11, _ = Module.objects.get_or_create(project=p1, name="Authentication")
        m12, _ = Module.objects.get_or_create(project=p1, name="Time Engine")
        m21, _ = Module.objects.get_or_create(project=p2, name="Billing UI")
        m22, _ = Module.objects.get_or_create(project=p2, name="Reports API")

        t1, _ = Task.objects.get_or_create(
            module=m11,
            title="Email login hardening",
            defaults={
                "assigned_to": dev1,
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
                "assigned_to": dev1,
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
                "assigned_to": dev2,
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
                "assigned_to": dev2,
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
                "assigned_to": manager,
                "description": "Add weekly trend chart data endpoint",
                "status": "todo",
                "priority": "low",
                "deadline": timezone.now().date() + timedelta(days=5),
            },
        )

        base = timezone.now() - timedelta(days=6)
        entries = [
            (0, t1, dev1, 2.5),
            (1, t1, dev1, 3.0),
            (2, t2, dev1, 1.25),
            (3, t3, dev2, 4.0),
            (4, t4, dev2, 2.75),
            (5, t5, manager, 1.5),
        ]
        for idx, task, user, hours in entries:
            st = base + timedelta(days=idx, hours=9)
            et = st + timedelta(hours=hours)
            TimeLog.objects.get_or_create(task=task, user=user, start_time=st, defaults={"end_time": et})

        Notification.objects.get_or_create(
            user=dev1,
            message="New task assigned: Timer edge-case fixes",
            defaults={"is_read": False, "redirect_url": f"{reverse('tasks')}?task={t2.id}"},
        )
        Notification.objects.get_or_create(
            user=dev2,
            message=f"Deadline reminder: Task #{t4.id} - {t4.title}",
            defaults={"is_read": False, "redirect_url": f"{reverse('tasks')}?task={t4.id}"},
        )
        Notification.objects.get_or_create(
            user=manager,
            message="Sprint review meeting tomorrow at 11:00 AM",
            defaults={"is_read": False, "redirect_url": reverse("reports")},
        )

        for user in [dev1, dev2, manager]:
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
