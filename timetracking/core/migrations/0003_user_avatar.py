from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_user_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="avatar",
            field=models.FileField(blank=True, null=True, upload_to="avatars/"),
        ),
    ]
