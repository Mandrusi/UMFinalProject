# Generated by Django 3.2.10 on 2023-05-11 23:36

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('my_app', '0004_image_format'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='image',
            name='format',
        ),
    ]
