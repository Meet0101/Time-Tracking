from decimal import Decimal

from django.db import migrations


# Must match BILLING_HOURLY_RATE (₹500/hr) — backfill existing rows.
HOURLY_RATE = Decimal("500")


def forwards(apps, schema_editor):
    Invoice = apps.get_model("core", "Invoice")
    for inv in Invoice.objects.all().only("id", "total_hours", "amount"):
        new_amount = (inv.total_hours * HOURLY_RATE).quantize(Decimal("0.01"))
        if inv.amount != new_amount:
            inv.amount = new_amount
            inv.save(update_fields=["amount"])


def backwards(apps, schema_editor):
    # Cannot restore previous amounts; no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_invoice_payment_razorpay"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
