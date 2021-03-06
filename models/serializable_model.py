##############################################################################
#
#    OSIS stands for Open Student Information System. It's an application
#    designed to manage the core business of higher education institutions,
#    such as universities, faculties, institutes and professional schools.
#    The core business involves the administration of students, teachers,
#    courses, programs and so on.
#
#    Copyright (C) 2015-2016 Université catholique de Louvain (http://www.uclouvain.be)
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    A copy of this license - GNU General Public License - is available
#    at the root of the source code of this program.  If not,
#    see http://www.gnu.org/licenses/.
#
##############################################################################
import logging
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models, IntegrityError
from django.db.models import DateTimeField, DateField
from django.core import serializers
import uuid
from pika.exceptions import ChannelClosed, ConnectionClosed
from osis_common.models.exception import MultipleModelsSerializationException
from osis_common.queue import queue_sender
import json
import datetime
from django.utils.encoding import force_text
from django.apps import apps
import time

LOGGER = logging.getLogger(settings.DEFAULT_LOGGER)


class SerializableQuerySet(models.QuerySet):
    # Called in case of bulk delete
    # Override this function is important to force to call the delete() function of a model's instance
    def delete(self, *args, **kwargs):
        for obj in self:
            obj.delete()


class SerializableModelManager(models.Manager):
    def get_by_natural_key(self, uuid):
        return self.get(uuid=uuid)

    def get_queryset(self):
        return SerializableQuerySet(self.model, using=self._db)


class SerializableModel(models.Model):
    objects = SerializableModelManager()

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)

    def save(self, *args, **kwargs):
        super(SerializableModel, self).save(*args, **kwargs)

        if hasattr(settings, 'QUEUES'):
            try:
                ser_obj = serialize(self)
                queue_sender.send_message(settings.QUEUES.get('QUEUES_NAME').get('MIGRATIONS_TO_PRODUCE'),
                                          wrap_serialization(ser_obj))
            except (ChannelClosed, ConnectionClosed):
                LOGGER.exception('QueueServer is not installed or not launched')

    def delete(self, *args, **kwargs):
        super(SerializableModel, self).delete(*args, **kwargs)
        if hasattr(settings, 'QUEUES'):
            try:
                ser_obj = serialize(self)
                queue_sender.send_message(settings.QUEUES.get('QUEUES_NAME').get('MIGRATIONS_TO_PRODUCE'),
                                          wrap_serialization(ser_obj, to_delete=True))
            except (ChannelClosed, ConnectionClosed):
                LOGGER.exception('QueueServer is not installed or not launched')

    def natural_key(self):
        return [self.uuid]

    def __str__(self):
        return self.uuid

    class Meta:
        abstract = True

    @classmethod
    def find_by_uuid(cls,uuid):
        try:
            return cls.objects.get(uuid=uuid)
        except ObjectDoesNotExist:
            return None


# To be deleted
def format_data_for_migration(objects, to_delete=False):
    """
    Format data to fit to a specific structure.
    :param objects: A list of model instances.
    :param to_delete: True if these records are to be deleted on the Osis-portal side.
                      False if these records are to insert or update on the OPsis-portal side.
    :return: A structured dictionary containing the necessary data to migrate from Osis to Osis-portal.
    """
    return {'serialized_objects': serialize_objects(objects), 'to_delete': to_delete}


# To be deleted
def serialize_objects(objects, format='json'):
    """
    Serialize all objects given by parameter.
    All objects must come from the same model. Otherwise, an exception will be thrown.
    If the object contains a FK 'user', this field will be ignored for the serialization.
    :param objects: List of objects to serialize.
    :return: Json data containing serializable objects.
    """
    if not objects:
        return None
    if len({obj.__class__ for obj in objects}) > 1:
        raise MultipleModelsSerializationException
    model_class = objects[0].__class__
    return serializers.serialize(format,
                                 objects,
                                 # indent=2,
                                 fields=[field.name for field in model_class._meta.fields if field.name != 'user'],
                                 use_natural_foreign_keys=True,
                                 use_natural_primary_keys=True)


def wrap_serialization(body, to_delete=False):
    wrapped_body = {"body": body}

    if to_delete:
        wrapped_body["to_delete"] = True

    return wrapped_body


def unwrap_serialization(wrapped_serialization):
    if wrapped_serialization.get("to_delete"):
        body = wrapped_serialization.get('body')
        model_class = apps.get_model(body.get('model'))
        fields = body.get('fields')
        model_class.objects.filter(uuid=fields.get('uuid')).delete()
        return None
    else:
        return wrapped_serialization.get("body")


def serialize(obj, last_syncs=None):
    if obj:
        dict = {}
        for f in obj.__class__._meta.fields:
            attribute = getattr(obj, f.name)
            if f.is_relation:
                if isinstance(attribute, SerializableModel):
                    dict[f.name] = serialize(attribute, last_syncs=last_syncs)
            else:
                try:
                    json.dumps(attribute)
                    dict[f.name] = attribute
                except TypeError:
                    if isinstance(f, DateTimeField) or isinstance(f, DateField):
                        dt = attribute
                        dict[f.name] = _convert_datetime_to_long(dt)
                    else:
                        dict[f.name] = force_text(attribute)
        class_label = obj.__class__._meta.label
        last_sync = None
        if last_syncs:
            last_sync = _convert_datetime_to_long(last_syncs.get(class_label))
        return {"model": class_label, "fields": dict, 'last_sync': last_sync}
    else:
        return None


def _convert_datetime_to_long(dtime):
    return time.mktime(dtime.timetuple()) if dtime else None


def _get_value(fields, field):
    attribute = fields.get(field.name)
    if isinstance(field, DateTimeField) or isinstance(field, DateField):
        return _convert_long_to_datetime(attribute)
    return attribute


def _convert_long_to_datetime(date_as_long):
    return datetime.datetime.fromtimestamp(date_as_long) if date_as_long else None


def _get_field_name(field):
    if field.is_relation:
        return '{}_id'.format(field.name)
    return field.name


def persist(structure):
    model_class = apps.get_model(structure.get('model'))
    if structure:
        fields = structure.get('fields')
        query_set = model_class.objects.filter(uuid=fields.get('uuid'))
        persisted_obj = query_set.first()
        if _changed_since_last_synchronization(fields, structure) or not persisted_obj:
            for field_name, value in fields.items():
                if isinstance(value, dict):
                    fields[field_name] = persist(value)
            kwargs = {_get_field_name(f): _get_value(fields, f) for f in model_class._meta.fields if f.name in fields.keys()}
            if persisted_obj:
                kwargs['id'] = persisted_obj.id
                query_set.update(**kwargs)
                return persisted_obj.id
            else:
                del kwargs['id']
                model_class.objects.bulk_create([model_class(**kwargs)])
                ids = model_class.objects.filter(uuid=kwargs.get('uuid')).values_list('id', flat=True)
                return ids[0]
        else:
            return persisted_obj.id
    else:
        return None


def _changed_since_last_synchronization(fields, structure):
    last_sync = _convert_long_to_datetime(structure.get('last_sync'))
    changed = _convert_long_to_datetime(fields.get('changed'))
    return not last_sync or not changed or changed > last_sync
