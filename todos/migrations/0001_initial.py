# Generated by Django 5.2.4 on 2025-07-29 11:26

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Task",
            fields=[
                ("updated", models.DateTimeField(auto_created=True, auto_now_add=True)),
                (
                    "id",
                    models.CharField(max_length=255, primary_key=True, serialize=False),
                ),
                ("kind", models.CharField(max_length=3)),
                ("etag", models.CharField()),
                ("title", models.CharField(max_length=1024)),
                ("self_link", models.URLField()),
                ("parent", models.CharField()),
                ("position", models.CharField()),
                ("notes", models.CharField(max_length=8192)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("needsAction", "Needs Action"),
                            ("completed", "Completed"),
                        ],
                        max_length=32,
                    ),
                ),
                ("due", models.DateTimeField(auto_now_add=True)),
                ("completed", models.DateTimeField()),
                ("deleted", models.BooleanField(default=False)),
                ("hidden", models.BooleanField(default=False)),
                ("web_view_link", models.URLField()),
            ],
        ),
        migrations.CreateModel(
            name="AssignmentInfo",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("link_to_task", models.URLField()),
                (
                    "surface_type",
                    models.CharField(
                        choices=[
                            (
                                "CONTEXT_TYPE_UNSPECIFIED",
                                "Unknown value for this task's context.",
                            ),
                            ("GMAIL", "The task is created from Gmail."),
                            ("DOCUMENT", "The task is assigned from a document."),
                            ("SPACE", " \tThe task is assigned from a Chat Space."),
                        ],
                        max_length=64,
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="task_assignments",
                        to="todos.task",
                    ),
                ),
            ],
        ),
    ]
