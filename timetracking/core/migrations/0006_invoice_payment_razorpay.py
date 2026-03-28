from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_notification_redirect_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="paid_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="payment_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("paid", "Paid"),
                    ("failed", "Failed"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="razorpay_order_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="invoice",
            name="razorpay_payment_id",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
