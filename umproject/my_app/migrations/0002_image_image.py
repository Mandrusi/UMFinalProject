# Generated by Django 3.2.10 on 2023-05-11 17:56

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('my_app', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='image',
            name='image',
            field=models.BinaryField(default=None),
        ),
    ]
