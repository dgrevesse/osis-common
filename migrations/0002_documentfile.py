# -*- coding: utf-8 -*-
# Generated by Django 1.9 on 2016-07-26 10:03
from __future__ import unicode_literals

from django.conf import settings
import django.core.files.storage
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('osis_common', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='DocumentFile',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file_name', models.CharField(max_length=100)),
                ('content_type', models.CharField(choices=[('application/csv', 'application/csv'), ('application/doc', 'application/doc'), ('application/pdf', 'application/pdf'), ('application/xls', 'application/xls'), ('application/xlsx', 'application/xlsx'), ('application/xml', 'application/xml'), ('application/zip', 'application/zip'), ('image/jpeg', 'image/jpeg'), ('image/gif', 'image/gif'), ('image/png', 'image/png'), ('text/html', 'text/html'), ('text/plain', 'text/plain')], default='application/csv', max_length=50)),
                ('creation_date', models.DateTimeField(auto_now_add=True)),
                ('storage_duration', models.IntegerField()),
                ('file',models.FileField(upload_to='files/'),),
                ('description', models.CharField(choices=[('ID_CARD', 'identity_card'), ('LETTER_MOTIVATION', 'letter_motivation')], default='LETTER_MOTIVATION', max_length=50)),
                ('document_type', models.CharField(blank=True, max_length=100, null=True)),
                ('size', models.IntegerField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
