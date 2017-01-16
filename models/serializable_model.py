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
from django.db import models
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
                queue_sender.send_message(settings.QUEUES.get('QUEUES_NAME').get('MIGRATIONS_TO_PRODUCE'),
                                          format_data_for_migration([self]))
            except (ChannelClosed, ConnectionClosed):
                LOGGER.exception('QueueServer is not installed or not launched')

    def delete(self, *args, **kwargs):
        super(SerializableModel, self).delete(*args, **kwargs)
        if hasattr(settings, 'QUEUES'):
            try:
                queue_sender.send_message(settings.QUEUES.get('QUEUES_NAME').get('MIGRATIONS_TO_PRODUCE'),
                                          format_data_for_migration([self], to_delete=True))
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


def format_data_for_migration(objects, to_delete=False):
    """
    Format data to fit to a specific structure.
    :param objects: A list of model instances.
    :param to_delete: True if these records are to be deleted on the Osis-portal side.
                      False if these records are to insert or update on the OPsis-portal side.
    :return: A structured dictionary containing the necessary data to migrate from Osis to Osis-portal.
    """
    return {'serialized_objects': serialize_objects(objects), 'to_delete': to_delete}


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


def serialize(obj):
    dict = {}
    for f in obj.__class__._meta.fields:
        if f.is_relation:
            print("is_relation true" + f.name)
            print(str(getattr(obj, f.name)))
            dict[f.name] = serialize(getattr(obj, f.name))
        else:
            try:
                json.dumps(getattr(obj, f.name))
                dict[f.name] = getattr(obj, f.name)
            except TypeError:
                if isinstance(f, DateTimeField) or isinstance(f, DateField):
                    dict[f.name] = getattr(obj, f.name).timestamp()
                else:
                    dict[f.name] = force_text(getattr(obj, f.name))
    return {"model": obj.__class__._meta.label, "fields": dict}


def deserialize(deser_data):
    model_class = apps.get_model(deser_data.get('model'))
    fields = deser_data['fields']
    obj = model_class()
    for field_name, value in fields.items():
        if isinstance(value, dict):
            foreign_obj = deserialize(value)
            setattr(obj, field_name, foreign_obj)
        else:
            setattr(obj, field_name, value)
    return obj


def get_attribute(obj, field):
    attribute = getattr(obj, field.name)
    if isinstance(field, DateTimeField) or isinstance(field, DateField):
        return datetime.datetime.fromtimestamp(attribute) if attribute else None
    return attribute


def persist(obj):
    for f in obj.__class__._meta.fields:
        if f.is_relation:
            setattr(obj, f.name, persist(getattr(obj, f.name)))
            #if obj.changed > last_sync

    query_set = obj.__class__.objects.filter(uuid=obj.uuid)
    kwargs = {f.name: get_attribute(obj, f) for f in obj.__class__._meta.fields}
    persisted_obj = query_set.first()
    if persisted_obj:
        kwargs['id'] = persisted_obj.id
    if not query_set.update(**kwargs):
        print("kwargs == " + str(kwargs))
        return obj.__class__.objects.create(**kwargs)
    else:
        return persisted_obj