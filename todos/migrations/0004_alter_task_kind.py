# Generated by Django 5.2.4 on 2025-07-29 20:19

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("todos", "0003_alter_task_kind_alter_task_parent"),
    ]

    operations = [
        migrations.AlterField(
            model_name="task",
            name="kind",
            field=models.CharField(blank=True, null=True),
        ),
    ]
