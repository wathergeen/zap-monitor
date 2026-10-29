from django.db import migrations

def create_extensions(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("CREATE EXTENSION IF NOT EXISTS unaccent;")
        schema_editor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

def drop_extensions(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("DROP EXTENSION IF EXISTS pg_trgm;")
        schema_editor.execute("DROP EXTENSION IF EXISTS unaccent;")

class Migration(migrations.Migration):
    dependencies = [
        ("messages_monitor", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_extensions, drop_extensions),
    ]
